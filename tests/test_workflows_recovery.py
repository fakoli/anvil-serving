"""Native owner recovery snapshots share the mutation gate."""

import os
from pathlib import Path

import pytest

from anvil_serving.propagation_fencing import NativeMutationFence, PropagationFenceError, TrustedNativeOwner
from anvil_serving.commands.workflows_recovery import snapshot_journal


pytestmark = pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="native no-follow open is required")


def _fence(tmp_path: Path) -> NativeMutationFence:
    tmp_path.chmod(0o700)
    owner = TrustedNativeOwner("owner-1", "resource-1", tmp_path)
    return NativeMutationFence(owner, tmp_path / "ignored")


def test_snapshot_copies_stable_journal_and_blocks_during_mutation(tmp_path):
    fence = _fence(tmp_path)
    with fence._lock():
        path = fence.journal_root / "resource-1.index.json"
        path.write_text('{"schema":"synthetic"}')
        path.chmod(0o600)
        with pytest.raises(PropagationFenceError, match="operation_in_progress"):
            fence.snapshot_journal(tmp_path / "blocked")
    assert not (tmp_path / "blocked").exists()

    result = fence.snapshot_journal(tmp_path / "export")
    assert result["schema"] == "anvil-serving.native-journal-snapshot/v1"
    assert set(result["files"]) == {"resource-1.index.json"}
    assert (tmp_path / "export/resource-1.index.json").read_bytes() == path.read_bytes()
    assert (tmp_path / "export/resource-1.index.json").stat().st_mode & 0o777 == 0o600


def test_snapshot_rejects_existing_or_symlink_destination(tmp_path):
    fence = _fence(tmp_path)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    with pytest.raises(PropagationFenceError, match="unsafe_custody_path"):
        fence.snapshot_journal(occupied)
    linked = tmp_path / "linked"
    linked.symlink_to(occupied, target_is_directory=True)
    with pytest.raises(PropagationFenceError, match="unsafe_custody_path"):
        fence.snapshot_journal(linked)


def test_operator_snapshot_uses_protected_configured_root(tmp_path):
    fence = _fence(tmp_path)
    with fence._lock():
        path = fence.journal_root / "resource-1.index.json"
        path.write_text("{}")
        path.chmod(0o600)
    result = snapshot_journal(str(tmp_path), "propagation-v1", str(tmp_path / "operator-export"))
    assert result["file_count"] == 1
    assert len(result["manifest_digest"]) == 64
