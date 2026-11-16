"""Native owner recovery snapshots share the mutation gate."""

import os
from dataclasses import replace
from pathlib import Path

import pytest

from anvil_serving.control_plane.propagation import effect_scope_digest, parse_contract
from anvil_serving.propagation_fencing import NativeMutationFence, PropagationFenceError, TrustedNativeOwner
from anvil_serving.commands.workflows_recovery import snapshot_journal
from tests.test_propagation_fencing import _CONTRACT, _fence as catalog_fence, _grant, _trusted_test_clock


pytestmark = pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="native no-follow open is required")


def _fence(tmp_path: Path) -> NativeMutationFence:
    tmp_path.chmod(0o700)
    owner = TrustedNativeOwner("owner-1", "resource-1", tmp_path)
    return NativeMutationFence(owner, tmp_path / "ignored")


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
