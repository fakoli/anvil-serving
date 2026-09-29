"""One reviewed, digest-pinned approval for the installed propagation owner."""

import hashlib
import os
from pathlib import Path
import stat
import sys

from ..propagation import ApprovedAuthority, MAX_CONTRACT_BYTES, parse_contract, _digest, _id
from .propagation_job_store import PropagationJobError


class PinnedApprovedContract:
    """Resolve an approval only from the operator-pinned local contract file."""

    def __init__(self, path: str | Path, contract_digest: str):
        if sys.platform != "linux":
            raise PropagationJobError("approval_unavailable")
        self.path = Path(path)
        try:
            self.digest = _digest(contract_digest)
        except Exception:
            raise PropagationJobError("approval_unavailable") from None
        if not self.path.is_absolute() or ".." in self.path.parts:
            raise PropagationJobError("approval_unavailable")

    def _read(self):
        try:
            parent = self.path.parent
            if parent.resolve(strict=True) != parent:
                raise ValueError()
            for ancestor in (parent, *parent.parents):
                info = os.lstat(ancestor)
                if (not stat.S_ISDIR(info.st_mode)
                        or info.st_uid not in {0, os.geteuid()}
                        or info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)):
                    raise ValueError()
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.geteuid()}
                        or info.st_mode & 0o022 or info.st_size > MAX_CONTRACT_BYTES):
                    raise ValueError()
                raw = stream.read(MAX_CONTRACT_BYTES + 1)
            if len(raw) > MAX_CONTRACT_BYTES or hashlib.sha256(raw).hexdigest() != self.digest:
                raise ValueError()
            parsed = parse_contract(raw)
            if parsed.canonical != raw or parsed.digest != self.digest:
                raise ValueError()
            return parsed
        except (OSError, ValueError, RuntimeError):
            raise PropagationJobError("approval_unavailable") from None

    def contract_lookup(self, approval_ref: str) -> bytes:
        try:
            ref = _id(approval_ref)
        except Exception:
            raise PropagationJobError("approval_unavailable") from None
        parsed = self._read()
        if parsed.value["approval_ref"] != ref:
            raise PropagationJobError("approval_unavailable")
        return parsed.canonical

    def approval_lookup(self, approval_ref: str) -> ApprovedAuthority | None:
        try:
            ref = _id(approval_ref)
        except Exception:
            return None
        parsed = self._read()
        value = parsed.value
        if value["approval_ref"] != ref:
            return None
        return ApprovedAuthority(ref, value["approval_digest"], parsed.digest)
