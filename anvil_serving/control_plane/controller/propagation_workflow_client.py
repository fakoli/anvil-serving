"""Bounded local callback client for the separate Workflows process."""

import json
import os
from pathlib import Path
import re
import socket
import stat

from ..propagation import _digest, _id
from .propagation_job_store import PropagationJobError


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


class WorkflowControlClient:
    def __init__(self, socket_path: str | Path, token_file: str | Path):
        self.path = Path(socket_path)
        if not self.path.is_absolute() or len(os.fsencode(self.path)) > 100:
            raise PropagationJobError("workflow_service_unavailable")
        token_path = Path(token_file)
        if not token_path.is_absolute():
            raise PropagationJobError("workflow_service_unavailable")
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
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(10)
                conn.connect(str(self.path))
                conn.sendall(raw)
                response = bytearray()
                while len(response) <= 4096 and not response.endswith(b"\n"):
                    part = conn.recv(4097 - len(response))
                    if not part:
                        break
                    response.extend(part)
            if len(response) > 4096 or not response.endswith(b"\n"):
                raise ValueError()
            value = json.loads(response)
            if type(value) is not dict or set(value) != {"ok", "result"} or value["ok"] is not True or type(value["result"]) is not dict:
                raise ValueError()
            return value["result"]
        except (OSError, ValueError, TypeError, UnicodeError):
            raise PropagationJobError("workflow_service_unavailable") from None

    def status(self, workflow_id: str, contract_digest: str):
        return self._call("status", workflow_id, "intent-" + contract_digest, contract_digest)

    def cancel(self, workflow_id: str, intent_id: str, contract_digest: str, _caller_id: str):
        return self._call("cancel", workflow_id, intent_id, contract_digest)

    def resume(self, workflow_id: str, intent_id: str, contract_digest: str, _caller_id: str):
        return self._call("resume", workflow_id, intent_id, contract_digest)
