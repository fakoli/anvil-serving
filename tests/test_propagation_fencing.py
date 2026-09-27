"""Controlled-file behavior checks for native propagation fencing."""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import time
import sys

import pytest

from anvil_serving.propagation_fencing import (
    NativeMutationFence,
    PropagationFenceError,
    TrustedNativeOwner,
)


_CONTRACT = b'{"generation":1,"schema":"anvil-propagation/v1"}'


def _fence(tmp_path: Path) -> tuple[NativeMutationFence, Path]:
    target = tmp_path / "catalog.json"
    target.write_bytes(b"before")
    owner = TrustedNativeOwner("owner-1", "catalog-1", tmp_path)
    return NativeMutationFence(owner, tmp_path / "journal"), target


def _grant(fence: NativeMutationFence, target: Path, generation: int = 1):
    return fence.grant(
        canonical_contract=_CONTRACT,
        generation=generation,
        effects=("catalog",),
        target_paths=(target,),
    )


def test_fence_persists_before_write_backup_and_verified_readback(tmp_path):
    fence, target = _fence(tmp_path)
    with fence.transaction(_grant(fence, target), canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
        journal.begin_effect("catalog", target, target.read_bytes(), b"after")
        journal.bind_backup("catalog", "backup-1")
        target.write_bytes(b"after")
        journal.observe_bytes("catalog", target.read_bytes())

    state = fence.journal()
    assert state["status"] == "completed"
    assert state["effects"]["catalog"] == {
        "path": str(target.resolve()),
        "before_digest": hashlib.sha256(b"before").hexdigest(),
        "desired_digest": hashlib.sha256(b"after").hexdigest(),
        "backup_id": "backup-1",
        "state": "verified",
        "observed_digest": hashlib.sha256(b"after").hexdigest(),
    }


def test_stale_or_reused_generation_fails_under_the_shared_lock(tmp_path):
    fence, target = _fence(tmp_path)
    with fence.transaction(_grant(fence, target, 2), canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
        journal.begin_effect("catalog", target, b"before", b"after")
        journal.observe_bytes("catalog", b"after")
    with pytest.raises(PropagationFenceError, match="stale_generation"):
        with fence.transaction(_grant(fence, target, 1), canonical_contract=_CONTRACT, target_paths=(target,)):
            pass
    with pytest.raises(PropagationFenceError, match="generation_reused"):
        with fence.transaction(_grant(fence, target, 2), canonical_contract=_CONTRACT, target_paths=(target,)):
            pass


def test_drift_and_interruption_remain_recovery_required_without_rollback_claim(tmp_path):
    fence, target = _fence(tmp_path)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        with fence.transaction(_grant(fence, target), canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
            journal.begin_effect("catalog", target, b"before", b"after")
            target.write_bytes(b"external-drift")
            journal.observe_bytes("catalog", target.read_bytes())
            raise RuntimeError("simulated interruption")

    state = fence.journal()
    assert state["status"] == "recovery_required"
    assert state["effects"]["catalog"]["state"] == "drift"
    assert target.read_bytes() == b"external-drift"
    with pytest.raises(PropagationFenceError, match="unresolved_reservation"):
        with fence.transaction(_grant(fence, target, 2), canonical_contract=_CONTRACT, target_paths=(target,)):
            pass


def test_dead_process_reservation_cannot_be_released_by_timeout_or_supersession(tmp_path):
    fence, target = _fence(tmp_path)
    script = """
import os
from pathlib import Path
from anvil_serving.propagation_fencing import NativeMutationFence, TrustedNativeOwner
root = Path(os.environ['FENCE_ROOT'])
target = root / 'catalog.json'
fence = NativeMutationFence(TrustedNativeOwner('owner-1', 'catalog-1', root), root / 'journal')
contract = b'{\\"generation\\":1,\\"schema\\":\\"anvil-propagation/v1\\"}'
grant = fence.grant(canonical_contract=contract, generation=1, effects=('catalog',), target_paths=(target,))
with fence.transaction(grant, canonical_contract=contract, target_paths=(target,)) as journal:
    journal.begin_effect('catalog', target, target.read_bytes(), b'after')
    target.write_bytes(b'after')
    os._exit(17)
"""
    env = {"FENCE_ROOT": str(tmp_path), "PYTHONPATH": str(Path(__file__).parents[1])}
    completed = subprocess.run([sys.executable, "-c", script], env=env, check=False)
    assert completed.returncode == 17
    assert fence.journal()["status"] == "active"
    with pytest.raises(PropagationFenceError, match="unresolved_reservation"):
        with fence.transaction(_grant(fence, target, 2), canonical_contract=_CONTRACT, target_paths=(target,)):
            pass


def test_fenced_pi_media_sync_uses_the_shared_native_journal(tmp_path):
    from anvil_serving.client_catalog_sync import sync_pi_media

    fence, target = _fence(tmp_path)
    target.write_text(
        '{"mcpServers":{"anvil-media-mcp":{"url":"https://retired.example"}}}',
        encoding="utf-8",
    )
    grant = fence.grant(
        canonical_contract=_CONTRACT,
        generation=1,
        effects=("pi-media",),
        target_paths=(target,),
    )
    result = sync_pi_media(
        mcp_config=str(target), backup_root=str(tmp_path / "backups"), withdraw=True,
        dry_run=False, confirm=True, fence=fence, grant=grant,
        canonical_contract=_CONTRACT,
    )
    assert result["backupCreated"] and fence.journal()["effects"]["pi-media"]["state"] == "verified"


def test_separate_process_overlap_fails_before_it_can_mutate(tmp_path):
    fence, target = _fence(tmp_path)
    ready = tmp_path / "ready"
    script = """
import os
from pathlib import Path
import time
from anvil_serving.propagation_fencing import NativeMutationFence, TrustedNativeOwner
root = Path(os.environ['FENCE_ROOT'])
target = root / 'catalog.json'
fence = NativeMutationFence(TrustedNativeOwner('owner-1', 'catalog-1', root), root / 'journal')
contract = b'{\"generation\":1,\"schema\":\"anvil-propagation/v1\"}'
grant = fence.grant(canonical_contract=contract, generation=1, effects=('catalog',), target_paths=(target,))
with fence.transaction(grant, canonical_contract=contract, target_paths=(target,)) as journal:
    journal.begin_effect('catalog', target, target.read_bytes(), b'after')
    (root / 'ready').write_text('ready')
    time.sleep(5)
"""
    env = {"FENCE_ROOT": str(tmp_path), "PYTHONPATH": str(Path(__file__).parents[1])}
    process = subprocess.Popen([sys.executable, "-c", script], env=env)
    try:
        for _ in range(100):
            if ready.exists():
                break
            time.sleep(0.01)
        assert ready.exists()
        with pytest.raises(PropagationFenceError, match="operation_in_progress"):
            with fence.transaction(_grant(fence, target, 2), canonical_contract=_CONTRACT, target_paths=(target,)):
                pass
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_fenced_preview_does_not_create_lock_or_journal_effects(tmp_path):
    from anvil_serving.client_catalog_sync import sync_pi_media

    fence, target = _fence(tmp_path)
    target.write_text('{"mcpServers":{"anvil-media-mcp":{}}}', encoding="utf-8")
    grant = fence.grant(
        canonical_contract=_CONTRACT, generation=1, effects=("pi-media",),
        target_paths=(target,),
    )
    result = sync_pi_media(
        mcp_config=str(target), backup_root=str(tmp_path / "backups"), withdraw=True,
        dry_run=True, confirm=False, fence=fence, grant=grant,
        canonical_contract=_CONTRACT,
    )
    assert result["dryRun"] is True
    assert not (tmp_path / "journal").exists()
