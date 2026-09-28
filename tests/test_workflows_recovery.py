"""Native owner recovery snapshots share the mutation gate."""

import os
from dataclasses import replace
from pathlib import Path

import pytest

from anvil_serving.control_plane.propagation import effect_scope_digest, parse_contract
from anvil_serving.propagation_fencing import NativeMutationFence, PropagationFenceError, TrustedNativeOwner
from anvil_serving.commands.workflows_recovery import snapshot_journal
from tests.test_propagation_fencing import _CONTRACT, _CONTRACT2, _fence as catalog_fence, _grant, _trusted_test_clock


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


def test_restored_epoch_requires_quiescence_approval_and_exact_bytes(tmp_path):
    fence, target = catalog_fence(tmp_path)
    with fence.transaction(_grant(fence, target), canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
        journal.begin_effect("catalog", target, b"before", b"after")
        target.write_bytes(b"after")
        journal.observe_bytes("catalog", target.read_bytes())
    approved = "a" * 64
    targets = "b" * 64
    with pytest.raises(PropagationFenceError, match="recovery_authority_unavailable"):
        fence.advance_recovery_epoch("epoch-2", approved, targets)

    def with_custody(quiescent, authority):
        return NativeMutationFence(replace(fence.owner, recovery_quiescent=quiescent,
            recovery_authority=authority), tmp_path / "journal")

    refused = with_custody(lambda *_: False, lambda *_: True)
    with pytest.raises(PropagationFenceError, match="quiescence_unproven"):
        refused.advance_recovery_epoch("epoch-2", approved, targets)
    refused = with_custody(lambda *_: True, lambda *_: False)
    with pytest.raises(PropagationFenceError, match="recovery_not_approved"):
        refused.advance_recovery_epoch("epoch-2", approved, targets)
    ready = with_custody(lambda *_: True, lambda *_: True)
    target.write_bytes(b"reverted")
    with pytest.raises(PropagationFenceError, match="external_drift"):
        ready.advance_recovery_epoch("epoch-2", approved, targets)
    target.write_bytes(b"after")
    digest = ready.advance_recovery_epoch("epoch-2", approved, targets)
    assert len(digest) == 64
    with pytest.raises(PropagationFenceError, match="unsafe_journal"):
        with fence.transaction(_grant(fence, target), canonical_contract=_CONTRACT, target_paths=(target,)):
            pass
    recovered = NativeMutationFence(replace(fence.owner, epoch="epoch-2"), tmp_path / "journal")
    with recovered._lock():
        index = recovered._read_index()
    assert index["high_water_generation"] == 1
    assert index["recovery"]["approval_digest"] == approved
    with recovered.transaction(_grant(recovered, target, 2), canonical_contract=_CONTRACT2,
                               target_paths=(target,)) as journal:
        journal.begin_effect("catalog", target, b"after", b"next")
        target.write_bytes(b"next")
        journal.observe_bytes("catalog", target.read_bytes())
    assert recovered.journal()["status"] == "completed"


def test_catalog_cutover_is_bound_to_recovery_and_survives_later_epochs(tmp_path):
    tmp_path.chmod(0o700)
    target = tmp_path / "catalog.json"
    target.write_bytes(b"before")
    target.chmod(0o600)
    value = parse_contract(_CONTRACT).value
    value["targets"][0]["resource_keys"] = ["client-catalog"]
    value["effect_set_digest"] = effect_scope_digest(value)
    contract = parse_contract(value).canonical
    owner = TrustedNativeOwner("owner-1", "client-catalog", tmp_path,
        clock=_trusted_test_clock,
        current_authority=lambda *_: True, recovery_quiescent=lambda *_: True,
        recovery_authority=lambda *_: True,
        effect_bindings={"catalog": ("catalog-apply", target)})
    with NativeMutationFence.legacy_catalog_write(tmp_path):
        pass  # Install the native catalog lock before owner activation.
    fence = NativeMutationFence(owner, tmp_path)
    grant = fence.grant(canonical_contract=contract, generation=1, effects=("catalog",), target_paths=(target,))
    with fence.transaction(grant, canonical_contract=contract, target_paths=(target,)) as journal:
        journal.begin_effect("catalog", target, b"before", b"after")
        target.write_bytes(b"after")
        journal.observe_bytes("catalog", b"after")
    marker = fence._catalog_cutover_path.read_bytes()
    fence.advance_recovery_epoch("epoch-2", "a" * 64, "b" * 64)
    recovered = NativeMutationFence(replace(owner, epoch="epoch-2"), tmp_path)
    recovered.advance_recovery_epoch("epoch-3", "a" * 64, "b" * 64)
    assert recovered._catalog_cutover_path.read_bytes() == marker
    latest = NativeMutationFence(replace(owner, epoch="epoch-3"), tmp_path)
    latest._catalog_cutover_path.write_bytes(marker.replace(b'"generation":1', b'"generation":2'))
    with pytest.raises(PropagationFenceError, match="unindexed_journal"):
        latest.advance_recovery_epoch("epoch-4", "a" * 64, "b" * 64)


def test_recovery_refuses_journal_missing_from_restored_index(tmp_path):
    fence, target = catalog_fence(tmp_path)
    with fence.transaction(_grant(fence, target), canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
        journal.begin_effect("catalog", target, b"before", b"after")
        target.write_bytes(b"after")
        journal.observe_bytes("catalog", b"after")
    with fence._lock():
        index = fence._read_index()
        reservation = next(iter(index["operations"]))
        orphan = fence.journal_root / "catalog-1.orphan.json"
        fence._held().write(orphan, fence._held().read(fence._operation_path(reservation), private=True), private=True)
    owner = replace(fence.owner, recovery_quiescent=lambda *_: True, recovery_authority=lambda *_: True)
    with pytest.raises(PropagationFenceError, match="unindexed_journal"):
        NativeMutationFence(owner, tmp_path / "journal").advance_recovery_epoch("epoch-2", "a" * 64, "b" * 64)


def test_recovery_refuses_missing_native_index(tmp_path):
    fence, _ = catalog_fence(tmp_path)
    owner = replace(fence.owner, recovery_quiescent=lambda *_: True, recovery_authority=lambda *_: True)
    with pytest.raises(PropagationFenceError, match="unsafe_journal"):
        NativeMutationFence(owner, tmp_path / "journal").advance_recovery_epoch("epoch-2", "a" * 64, "b" * 64)


def test_completed_noop_does_not_prove_unknown_target_custody(tmp_path):
    fence, target = catalog_fence(tmp_path)
    with fence.transaction(_grant(fence, target), canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
        journal.begin_effect("catalog", target, b"before", b"before")
        journal.observe_bytes("catalog", target.read_bytes())
    owner = replace(fence.owner, recovery_quiescent=lambda *_: True,
                    recovery_authority=lambda *_: False)  # target is unreachable to the protected authority
    with pytest.raises(PropagationFenceError, match="recovery_not_approved"):
        NativeMutationFence(owner, tmp_path / "journal").advance_recovery_epoch("epoch-2", "a" * 64, "b" * 64)
