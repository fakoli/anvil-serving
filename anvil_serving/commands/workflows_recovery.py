"""Read-only native journal export for encrypted workflow recovery."""

import hashlib
import json
from pathlib import Path

from ..propagation_fencing import NativeMutationFence, PropagationFenceError, TrustedNativeOwner


def snapshot_journal(storage_root: str, profile: str, output: str) -> dict:
    if profile != "propagation-v1":
        raise ValueError("unsupported workflow profile")
    try:
        root, destination = Path(storage_root), Path(output)
        if not root.is_absolute() or not destination.is_absolute():
            raise ValueError("invalid snapshot path")
        owner = TrustedNativeOwner("backup", "backup", root)
        manifest = NativeMutationFence(owner, root / ".anvil-serving/propagation-fencing").snapshot_journal(destination)
        digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {"profile_id": profile, "file_count": len(manifest["files"]), "manifest_digest": digest}
    except PropagationFenceError:
        raise ValueError("native journal snapshot is unavailable") from None
