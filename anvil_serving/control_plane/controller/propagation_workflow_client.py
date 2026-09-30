"""Bounded local callback client for the separate Workflows process."""

import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import time

from ..propagation import _digest, _id
from .propagation_job_store import PropagationJobError

_EXCHANGE_SECONDS = 10


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError()
    return remaining


def _token(path: Path) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > 128:
                raise ValueError()
            value = stream.read(129).strip().decode("ascii")
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError()
        return value
    except (OSError, ValueError, UnicodeError):
        raise PropagationJobError("workflow_service_unavailable") from None


def _socket_identity(path: Path, expected_uid: int, expected_gid: int) -> int:
    parent = path.parent
    if parent.resolve(strict=True) != parent:
        raise ValueError()
    for ancestor in (parent, *parent.parents):
        info = os.lstat(ancestor)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.geteuid(), expected_uid}:
            raise ValueError()
        if info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
            raise ValueError()
    directory = os.lstat(parent)
    endpoint = os.lstat(path)
    private = (directory.st_uid == os.geteuid() and stat.S_IMODE(directory.st_mode) == 0o700
               and stat.S_IMODE(endpoint.st_mode) == 0o600)
    shared = (expected_gid in (*os.getgroups(), os.getegid()) and stat.S_IMODE(directory.st_mode) == 0o710
              and stat.S_IMODE(endpoint.st_mode) == 0o660)
    if (not stat.S_ISSOCK(endpoint.st_mode) or directory.st_uid != expected_uid
            or directory.st_gid != expected_gid or endpoint.st_uid != expected_uid
            or endpoint.st_gid != expected_gid or not (private or shared)):
        raise ValueError()
    return endpoint.st_ino


class WorkflowControlClient:
    def __init__(self, socket_path: str | Path, token_file: str | Path, *, expected_peer_uid: int, expected_peer_gid: int):
        self.path = Path(socket_path)
        if not self.path.is_absolute() or len(os.fsencode(self.path)) > 100:
            raise PropagationJobError("workflow_service_unavailable")
        token_path = Path(token_file)
        if not token_path.is_absolute():
            raise PropagationJobError("workflow_service_unavailable")
        if (type(expected_peer_uid) is not int or expected_peer_uid < 0
                or type(expected_peer_gid) is not int or expected_peer_gid < 0):
            raise PropagationJobError("workflow_service_unavailable")
        self.peer_uid, self.peer_gid = expected_peer_uid, expected_peer_gid
        self.secret = _token(token_path)

    def _call(self, action: str, workflow_id: str, intent_id: str, contract_digest: str):
        _id(workflow_id)
        _id(intent_id)
        _digest(contract_digest)
        request = {"schema": "anvil-workflows.control/v1", "action": action,
                   "workflow_id": workflow_id, "intent_id": intent_id,
                   "contract_digest": contract_digest, "token": self.secret}
        raw = json.dumps(request, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
        try:
            endpoint = _socket_identity(self.path, self.peer_uid, self.peer_gid)
            deadline = time.monotonic() + _EXCHANGE_SECONDS
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(_remaining(deadline))
                conn.connect(str(self.path))
                pid, uid, gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
                # Linux reports PID 0 when the peer is outside our PID namespace.
                # Custody, credentials and the unchanged socket inode bind the peer.
                if (pid < 0 or (uid, gid) != (self.peer_uid, self.peer_gid)
                        or _socket_identity(self.path, self.peer_uid, self.peer_gid) != endpoint):
                    raise ValueError()
                conn.settimeout(_remaining(deadline))
                conn.sendall(raw)
                response = bytearray()
                while len(response) <= 4096 and not response.endswith(b"\n"):
                    conn.settimeout(_remaining(deadline))
                    part = conn.recv(4097 - len(response))
                    if not part:
                        break
                    response.extend(part)
            if time.monotonic() > deadline or len(response) > 4096 or not response.endswith(b"\n"):
                raise ValueError()
            value = json.loads(response)
            if type(value) is not dict or set(value) != {"ok", "result"} or value["ok"] is not True or type(value["result"]) is not dict:
                raise ValueError()
            return value["result"]
        except (OSError, ValueError, TypeError, UnicodeError, RuntimeError):
            raise PropagationJobError("workflow_service_unavailable") from None

    def status(self, workflow_id: str, contract_digest: str):
        return self._call("status", workflow_id, "intent-" + contract_digest, contract_digest)

    def cancel(self, workflow_id: str, intent_id: str, contract_digest: str, _caller_id: str):
        return self._call("cancel", workflow_id, intent_id, contract_digest)

    def resume(self, workflow_id: str, intent_id: str, contract_digest: str, _caller_id: str):
        return self._call("resume", workflow_id, intent_id, contract_digest)
