"""Controlled-file behavior checks for native propagation fencing."""
from __future__ import annotations

import hashlib
from dataclasses import replace
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import time

import pytest

from anvil_serving.control_plane.propagation import effect_scope_digest, parse_contract
from anvil_serving.propagation_fencing import (
    NativeMutationFence,
    PropagationFenceError,
    TrustedNativeOwner,
)


def _canonical_contract(generation: int = 1) -> bytes:
    digest = "a" * 64
    value = {
        "schema": "anvil-propagation/v1", "scope": "scope-1", "revision": "revision-1", "generation": generation,
        "approval_ref": "approval-1", "approval_digest": digest, "activation_ref": "activation-1", "activation_digest": digest,
        "inputs": {"catalog_digest": digest, "monitoring_inventory_digest": digest, "execution_profile_digest": digest, "artifact_digest": digest, "installation_inventory_digest": digest},
        "effect_set_digest": digest,
        "targets": [{"target_id": "target-1", "installation_id": "installation-1", "profile_id": "profile-1", "runtime_id": "runtime-1", "resource_keys": ["catalog-1"], "expected_identity_ref": "identity-1", "expected_identity_digest": digest, "checks": ["catalog-equal"], "effects": ["catalog-apply"]}],
        "execution_profile_ref": "profile-1", "execution_profile_digest": digest,
        "session_policy": {"preserve_active_conversations": True, "loaded_state_required": True, "idle_reload": False},
        "preview_policy": {"all_required_targets": True, "web_runtime_required": True, "monitoring_required": True},
        "issued_at": "2026-09-27T12:00:00Z", "deadline_at": "2026-09-28T12:00:00Z",
    }
    value["effect_set_digest"] = effect_scope_digest(value)
    return parse_contract(value).canonical


_CONTRACT = _canonical_contract()
_CONTRACT2 = _canonical_contract(2)


def _trusted_test_clock() -> datetime:
    return datetime(2026, 9, 27, 13, tzinfo=timezone.utc)


@pytest.fixture
def tmp_path(tmp_path):
    """Use the shared protected Windows tree, independent of pytest temp ACLs."""
    if sys.platform == "win32":
        from tests.bootstrap_windows_fixtures import windows_fixture_tree

        with windows_fixture_tree() as tree:
            yield tree.root
    else:
        os.chmod(tmp_path, 0o700)
        yield tmp_path


def _private_file(path: Path) -> None:
    if os.name != "nt":
        os.chmod(path, 0o600)


def _private_directory(path: Path) -> None:
    if os.name == "nt":
        from tests.bootstrap_windows_fixtures import WindowsFixtureTree
        WindowsFixtureTree(path)
    else:
        os.chmod(path, 0o700)


def _fence(tmp_path: Path) -> tuple[NativeMutationFence, Path]:
    target = tmp_path / "catalog.json"
    target.write_bytes(b"before")
    _private_file(target)
    owner = TrustedNativeOwner(
        "owner-1", "catalog-1", tmp_path, backup_root=tmp_path / "backups",
        clock=_trusted_test_clock,
        current_authority=lambda _digest, _generation, _epoch: True, effect_bindings={"catalog": ("catalog-apply", target), "pi-media": ("catalog-apply", target)},
    )
    return NativeMutationFence(owner, tmp_path / "journal"), target


def _grant(fence: NativeMutationFence, target: Path, generation: int = 1):
    return fence.grant(
        canonical_contract=_CONTRACT if generation == 1 else _CONTRACT2,
        generation=generation,
        effects=("catalog",),
        target_paths=(target,),
    )


def test_fence_persists_before_write_backup_and_verified_readback(tmp_path):
    fence, target = _fence(tmp_path)
    with fence.transaction(_grant(fence, target), canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
        journal.begin_effect("catalog", target, target.read_bytes(), b"after")
        target.write_bytes(b"after")
        journal.observe_bytes("catalog", target.read_bytes())

    state = fence.journal()
    assert state["status"] == "completed"
    assert state["effects"]["catalog"] == {
        "path": str(target.resolve()),
        "before_digest": hashlib.sha256(b"before").hexdigest(),
        "desired_digest": hashlib.sha256(b"after").hexdigest(),
        "backup_id": None,
        "state": "verified",
        "observed_digest": hashlib.sha256(b"after").hexdigest(),
    }


def test_stale_or_reused_generation_fails_under_the_shared_lock(tmp_path):
    fence, target = _fence(tmp_path)
    with fence.transaction(_grant(fence, target, 2), canonical_contract=_CONTRACT2, target_paths=(target,)) as journal:
        journal.begin_effect("catalog", target, b"before", b"after")
        target.write_bytes(b"after")
        journal.observe_bytes("catalog", b"after")
    with pytest.raises(PropagationFenceError, match="stale_generation"):
        with fence.transaction(_grant(fence, target, 1), canonical_contract=_CONTRACT, target_paths=(target,)):
            pass
    with pytest.raises(PropagationFenceError, match="already_completed"):
        with fence.transaction(_grant(fence, target, 2), canonical_contract=_CONTRACT2, target_paths=(target,)):
            pass


def test_catalog_cutover_retires_direct_cli_mcp_and_scheduled_writes(tmp_path, monkeypatch):
    from anvil_serving import client_catalog_sync

    value = parse_contract(_CONTRACT).value
    value["targets"][0]["resource_keys"] = ["client-catalog"]
    value["effect_set_digest"] = effect_scope_digest(value)
    contract = parse_contract(value).canonical
    target = tmp_path / "catalog.json"
    target.write_bytes(b"before")
    _private_file(target)
    owner = TrustedNativeOwner(
        "owner-1", "client-catalog", tmp_path, clock=_trusted_test_clock,
        current_authority=lambda *_args: True,
        effect_bindings={"catalog": ("catalog-apply", target)},
    )
    fence = NativeMutationFence(owner, tmp_path)
    legacy_lock = tmp_path / ".config/anvil-serving/pi/catalog.lock"
    legacy_lock.parent.mkdir(parents=True, mode=0o700)
    for directory in (tmp_path / ".config", legacy_lock.parent.parent, legacy_lock.parent):
        _private_directory(directory)
    legacy_lock.write_bytes(b"")
    _private_file(legacy_lock)
    with NativeMutationFence.legacy_catalog_write(tmp_path):
        with NativeMutationFence.legacy_catalog_write(tmp_path):
            pass
        with pytest.raises(PropagationFenceError, match="operation_in_progress"):
            fence.activate_catalog_cutover(contract)
    with pytest.raises(PropagationFenceError, match="untrusted_grant"):
        with fence.transaction(None, canonical_contract=contract, target_paths=(target,)):
            pytest.fail("untrusted writer entered")
    assert not fence._catalog_cutover_path.exists()
    grant = fence.grant(canonical_contract=contract, generation=1, effects=("catalog",), target_paths=(target,))
    with fence.transaction(grant, canonical_contract=contract, target_paths=(target,)):
        assert fence._catalog_cutover_path.exists()
    fence.activate_catalog_cutover(contract)
    with pytest.raises(PropagationFenceError, match="stale_generation"):
        with NativeMutationFence.legacy_catalog_write(tmp_path):
            pytest.fail("retired writer entered")

    monkeypatch.setenv("HOME", str(tmp_path))
    if os.name == "nt":
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
    touched = []
    monkeypatch.setattr(client_catalog_sync, "_sync_clients", lambda **_kwargs: touched.append("catalog"))
    monkeypatch.setattr(client_catalog_sync, "_sync_pi_media", lambda **_kwargs: touched.append("media"))
    with pytest.raises(PropagationFenceError, match="stale_generation"):
        client_catalog_sync.sync_clients(base_url="http://127.0.0.1:8000/v1", dry_run=False, confirm=True)
    with pytest.raises(PropagationFenceError, match="stale_generation"):
        client_catalog_sync.sync_pi_media(withdraw=True, dry_run=False, confirm=True)
    assert touched == []
    client_catalog_sync.sync_clients(base_url="http://127.0.0.1:8000/v1", dry_run=True)
    assert touched == ["catalog"]
    fence._catalog_cutover_path.unlink()  # Simulate an incomplete local restore.
    with pytest.raises(PropagationFenceError, match="stale_generation"):
        with fence.resume(grant, canonical_contract=contract, target_paths=(target,)):
            pytest.fail("restored writer entered without cutover")


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
        with fence.transaction(_grant(fence, target, 2), canonical_contract=_CONTRACT2, target_paths=(target,)):
            pass


def test_dead_process_reservation_cannot_be_released_by_timeout_or_supersession(tmp_path):
    fence, target = _fence(tmp_path)
    script = """
import os
from datetime import datetime, timezone
from pathlib import Path
from anvil_serving.propagation_fencing import NativeMutationFence, TrustedNativeOwner
root = Path(os.environ['FENCE_ROOT'])
target = root / 'catalog.json'
fence = NativeMutationFence(TrustedNativeOwner(
    'owner-1', 'catalog-1', root, backup_root=root / 'backups',
    clock=lambda: datetime(2026, 9, 27, 13, tzinfo=timezone.utc),
    current_authority=lambda _digest, _generation, _epoch: True, effect_bindings={"catalog": ("catalog-apply", target)},
), root / 'journal')
contract = os.environ['FENCE_CONTRACT'].encode('ascii')
grant = fence.grant(canonical_contract=contract, generation=1, effects=('catalog',), target_paths=(target,))
with fence.transaction(grant, canonical_contract=contract, target_paths=(target,)) as journal:
    journal.begin_effect('catalog', target, target.read_bytes(), b'after')
    target.write_bytes(b'after')
    os._exit(17)
"""
    env = {"FENCE_ROOT": str(tmp_path), "FENCE_CONTRACT": _CONTRACT.decode("ascii"), "PYTHONPATH": str(Path(__file__).parents[1])}
    completed = subprocess.run([sys.executable, "-c", script], env=env, check=False)
    assert completed.returncode == 17
    assert fence.journal()["status"] == "active"
    with pytest.raises(PropagationFenceError, match="unresolved_reservation"):
        with fence.transaction(_grant(fence, target, 2), canonical_contract=_CONTRACT2, target_paths=(target,)):
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
from datetime import datetime, timezone
from pathlib import Path
import time
from anvil_serving.propagation_fencing import NativeMutationFence, TrustedNativeOwner
root = Path(os.environ['FENCE_ROOT'])
target = root / 'catalog.json'
fence = NativeMutationFence(TrustedNativeOwner(
    'owner-1', 'catalog-1', root, backup_root=root / 'backups',
    clock=lambda: datetime(2026, 9, 27, 13, tzinfo=timezone.utc),
    current_authority=lambda _digest, _generation, _epoch: True, effect_bindings={"catalog": ("catalog-apply", target)},
), root / 'journal')
contract = os.environ['FENCE_CONTRACT'].encode('ascii')
grant = fence.grant(canonical_contract=contract, generation=1, effects=('catalog',), target_paths=(target,))
with fence.transaction(grant, canonical_contract=contract, target_paths=(target,)) as journal:
    journal.begin_effect('catalog', target, target.read_bytes(), b'after')
    (root / 'ready').write_text('ready')
    time.sleep(5)
"""
    env = {"FENCE_ROOT": str(tmp_path), "FENCE_CONTRACT": _CONTRACT.decode("ascii"), "PYTHONPATH": str(Path(__file__).parents[1])}
    process = subprocess.Popen([sys.executable, "-c", script], env=env)
    try:
        for _ in range(100):
            if ready.exists():
                break
            time.sleep(0.01)
        assert ready.exists()
        with pytest.raises(PropagationFenceError, match="operation_in_progress"):
            with fence.transaction(_grant(fence, target, 2), canonical_contract=_CONTRACT2, target_paths=(target,)):
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


def test_canonical_custody_rejects_alternate_journal_roots_and_target_substitution(tmp_path):
    fence, target = _fence(tmp_path)
    other = NativeMutationFence(fence.owner, tmp_path / "elsewhere")
    assert other.journal_root == fence.journal_root
    grant = _grant(fence, target)
    substituted = tmp_path / "other-catalog.json"
    substituted.write_bytes(b"before")
    with fence.transaction(grant, canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
        with pytest.raises(PropagationFenceError, match="effect_not_granted"):
            journal.begin_effect("catalog", substituted, b"before", b"after")
        journal.begin_effect("catalog", target, b"before", b"after")
        target.write_bytes(b"after")
        journal.observe_bytes("catalog", b"after")


def test_later_generation_keeps_completed_operation_and_original_backup_identity(tmp_path):
    fence, target = _fence(tmp_path)
    first = _grant(fence, target)
    with fence.transaction(first, canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
        journal.begin_effect("catalog", target, b"before", b"after")
        target.write_bytes(b"after")
        journal.observe_bytes("catalog", b"after")
    original = fence.lookup(first)
    assert original and original["effects"]["catalog"]["backup_id"] is None
    second = _grant(fence, target, 2)
    with fence.transaction(second, canonical_contract=_CONTRACT2, target_paths=(target,)) as journal:
        journal.begin_effect("catalog", target, b"after", b"later")
        target.write_bytes(b"later")
        journal.observe_bytes("catalog", b"later")
    assert fence.lookup(first) == original
    with pytest.raises(PropagationFenceError, match="already_completed"):
        with fence.transaction(first, canonical_contract=_CONTRACT, target_paths=(target,)):
            pass


def test_grant_refuses_malformed_or_contract_generation_mismatch_before_lock(tmp_path):
    fence, target = _fence(tmp_path)
    with pytest.raises(PropagationFenceError, match="malformed_grant"):
        fence.grant(canonical_contract=b'{"generation":1}', generation=1, effects=("catalog",), target_paths=(target,))
    with pytest.raises(PropagationFenceError, match="grant_generation_mismatch"):
        fence.grant(canonical_contract=_CONTRACT2, generation=1, effects=("catalog",), target_paths=(target,))


def test_journal_write_is_portable_when_fchmod_is_not_available(tmp_path, monkeypatch):
    import anvil_serving.propagation_fencing as fencing

    monkeypatch.delattr(fencing.os, "fchmod", raising=False)
    fence, target = _fence(tmp_path)
    with fence.transaction(_grant(fence, target), canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
        journal.begin_effect("catalog", target, b"before", b"after")
        target.write_bytes(b"after")
        journal.observe_bytes("catalog", b"after")
    assert fence.journal()["status"] == "completed"


def test_new_effects_require_catalog_effect_and_current_trusted_clock(tmp_path):
    import json
    from datetime import datetime, timezone

    target = tmp_path / "catalog.json"; target.write_bytes(b"before")
    expired_owner = TrustedNativeOwner(
        "owner-1", "catalog-1", tmp_path,
        backup_root=tmp_path / "backups",
        clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc),
        current_authority=lambda _digest, _generation, _epoch: True, effect_bindings={"catalog": ("catalog-apply", target)},
    )
    expired = NativeMutationFence(expired_owner, tmp_path / "ignored")
    grant = expired.grant(canonical_contract=_CONTRACT, generation=1, effects=("catalog",), target_paths=(target,))
    with pytest.raises(PropagationFenceError, match="approval_expired"):
        with expired.transaction(grant, canonical_contract=_CONTRACT, target_paths=(target,)):
            pass
    value = json.loads(_CONTRACT)
    value["targets"][0]["effects"] = ["monitoring-apply"]
    value["effect_set_digest"] = effect_scope_digest(value)
    monitoring_only = parse_contract(value).canonical
    effect_root = tmp_path / "catalog-effect"; effect_root.mkdir()
    fence, target = _fence(effect_root)
    with pytest.raises(PropagationFenceError, match="effect_not_approved"):
        fence.grant(canonical_contract=monitoring_only, generation=1, effects=("catalog",), target_paths=(target,))


def test_journal_parent_and_lookup_mapping_are_custody_bound(tmp_path):
    root = tmp_path / "root"; root.mkdir(mode=0o700)
    _private_directory(root)
    target = root / "a"; other = root / "b"; target.write_bytes(b"before"); other.write_bytes(b"before")
    _private_file(target); _private_file(other)
    fence = NativeMutationFence(TrustedNativeOwner(
        "owner-1", "catalog-1", root, backup_root=root / "backups",
        clock=_trusted_test_clock,
        current_authority=lambda _digest, _generation, _epoch: True, effect_bindings={"catalog": ("catalog-apply", target), "pi-media": ("catalog-apply", target)},
    ), root / "ignored")
    grant = fence.grant(canonical_contract=_CONTRACT, generation=1, effects=("catalog",), target_paths=(target,), effect_targets={"catalog": target})
    with fence.transaction(grant, canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
        journal.begin_effect("catalog", target, b"before", b"after")
        target.write_bytes(b"after")
        journal.observe_bytes("catalog", b"after")
    with pytest.raises(PropagationFenceError, match="grant_target_mismatch"):
        fence.grant(canonical_contract=_CONTRACT, generation=1, effects=("catalog",), target_paths=(other,), effect_targets={"catalog": other})
    outside = tmp_path / "outside"; outside.mkdir()
    import shutil
    shutil.rmtree(root / ".config" / "anvil-serving")
    (root / ".config" / "anvil-serving").symlink_to(outside, target_is_directory=True)
    fresh = NativeMutationFence(TrustedNativeOwner(
        "owner-1", "catalog-1", root, backup_root=root / "backups",
        clock=_trusted_test_clock,
        current_authority=lambda _digest, _generation, _epoch: True, effect_bindings={"catalog": ("catalog-apply", target), "pi-media": ("catalog-apply", target)},
    ), root / "ignored")
    next_contract = _CONTRACT2
    next_grant = fresh.grant(canonical_contract=next_contract, generation=2, effects=("catalog",), target_paths=(target,))
    with pytest.raises(PropagationFenceError, match="unsafe_custody_path"):
        with fresh.transaction(next_grant, canonical_contract=next_contract, target_paths=(target,)):
            pass


def test_backup_manifest_rejects_post_manifest_tampering(tmp_path):
    import json

    fence, target = _fence(tmp_path)
    bundle = fence.owner.backup_root / "backup-1"; bundle.mkdir(parents=True)
    _private_directory(fence.owner.backup_root)
    _private_directory(bundle)
    backup = bundle / "00-catalog.json"
    backup.write_bytes(b"before")
    _private_file(backup)
    (bundle / "manifest.json").write_text(json.dumps({"files": [{"source": str(target.resolve()), "backup": backup.name, "existed": True, "sha256": hashlib.sha256(b"before").hexdigest()}]}))
    _private_file(bundle / "manifest.json")
    # The backup matched when its manifest was written.  A later replacement
    # must not be accepted merely because the manifest still names this path.
    backup.write_bytes(b"tampered")
    _private_file(backup)
    with pytest.raises(PropagationFenceError, match="backup_binding_mismatch"):
        with fence.transaction(_grant(fence, target), canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
            journal.begin_effect("catalog", target, b"before", b"after")
            journal.bind_backup("catalog", bundle)


def test_observation_requires_the_actual_held_path_readback(tmp_path):
    fence, target = _fence(tmp_path)
    with pytest.raises(PropagationFenceError, match="external_drift"):
        with fence.transaction(_grant(fence, target), canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
            journal.begin_effect("catalog", target, b"before", b"after")
            target.write_bytes(b"after")
            # A caller-supplied observation cannot certify a different file view.
            journal.observe_bytes("catalog", b"forged")
    state = fence.journal()
    assert state["status"] == "recovery_required"
    assert state["effects"]["catalog"]["desired_digest"] == hashlib.sha256(b"after").hexdigest()


@pytest.mark.skipif(os.name == "nt", reason="synthetic directory symlink needs developer-mode privileges")
def test_fenced_write_refuses_target_parent_swap_without_touching_redirect(tmp_path):
    owned = tmp_path / "owned"; owned.mkdir(mode=0o700)
    client = owned / "client"; client.mkdir(mode=0o700)
    target = client / "catalog.json"; target.write_bytes(b"before"); _private_file(target)
    outside = tmp_path / "outside"; outside.mkdir(mode=0o700)
    redirected = outside / target.name; redirected.write_bytes(b"outside-before"); _private_file(redirected)
    fence = NativeMutationFence(TrustedNativeOwner(
        "owner-1", "catalog-1", owned, backup_root=owned / "backups",
        clock=_trusted_test_clock,
        current_authority=lambda _digest, _generation, _epoch: True, effect_bindings={"catalog": ("catalog-apply", target), "pi-media": ("catalog-apply", target)},
    ), owned / "ignored")
    grant = fence.grant(canonical_contract=_CONTRACT, generation=1, effects=("catalog",), target_paths=(target,))
    with pytest.raises(PropagationFenceError):
        with fence.transaction(grant, canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
            client.rename(owned / "original-client")
            client.symlink_to(outside, target_is_directory=True)
            journal.begin_effect("catalog", target, b"before", b"after")
            journal.write("catalog", b"after")
    assert redirected.read_bytes() == b"outside-before"
    assert (owned / "original-client" / "catalog.json").read_bytes() == b"before"


def test_missing_target_parent_refuses_without_creating_a_client_directory(tmp_path):
    fence, _ = _fence(tmp_path)
    missing = tmp_path / "not-enrolled" / "catalog.json"
    fence = NativeMutationFence(TrustedNativeOwner("owner-1", "catalog-1", tmp_path,
        clock=_trusted_test_clock, current_authority=lambda _digest, _generation, _epoch: True, effect_bindings={"catalog": ("catalog-apply", missing)}), tmp_path / "ignored")
    grant = fence.grant(
        canonical_contract=_CONTRACT, generation=1, effects=("catalog",), target_paths=(missing,),
    )
    with pytest.raises(PropagationFenceError, match="unsafe_custody_path"):
        with fence.transaction(grant, canonical_contract=_CONTRACT, target_paths=(missing,)):
            pass
    assert not missing.parent.exists()


def test_fenced_body_oserror_is_not_misclassified_as_custody_failure(tmp_path):
    fence, target = _fence(tmp_path)

    class ControlledWriteError(OSError):
        pass

    with pytest.raises(ControlledWriteError, match="controlled write failure"):
        with fence.transaction(_grant(fence, target), canonical_contract=_CONTRACT, target_paths=(target,)):
            raise ControlledWriteError("controlled write failure")
    assert fence.journal()["status"] == "recovery_required"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native reparse/rename custody")
def test_windows_fence_refuses_reparse_parent_and_pins_held_parent(tmp_path):
    from tests.bootstrap_windows_fixtures import WindowsFixtureTree

    tree = WindowsFixtureTree(tmp_path)
    root = tmp_path / "owner"; root.mkdir(); tree.establish_full_control(root)
    parent = root / "client"; parent.mkdir(); tree.establish_full_control(parent)
    target = parent / "catalog.json"; target.write_bytes(b"before"); tree.establish_full_control(target)
    outside = tmp_path / "outside"; outside.mkdir(); tree.establish_full_control(outside)
    redirected = outside / target.name; redirected.write_bytes(b"outside-before"); tree.establish_full_control(redirected)
    fence = NativeMutationFence(TrustedNativeOwner(
        "owner-1", "catalog-1", root, backup_root=root / "backups",
        clock=_trusted_test_clock,
        current_authority=lambda _digest, _generation, _epoch: True, effect_bindings={"catalog": ("catalog-apply", target), "pi-media": ("catalog-apply", target)},
    ), root / "ignored")
    grant = fence.grant(canonical_contract=_CONTRACT, generation=1, effects=("catalog",), target_paths=(target,))
    with fence.transaction(grant, canonical_contract=_CONTRACT, target_paths=(target,)):
        with pytest.raises(PermissionError):
            parent.rename(root / "renamed-client")
    parent.rename(root / "original-client")
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(parent), str(outside)],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0
    try:
        with pytest.raises(PropagationFenceError, match="unsafe_custody_path"):
            with fence.transaction(grant, canonical_contract=_CONTRACT, target_paths=(target,)):
                pass
        assert redirected.read_bytes() == b"outside-before"
    finally:
        parent.rmdir()


def test_wrong_in_root_backup_root_refuses_before_lock_or_effects(tmp_path):
    from anvil_serving.client_catalog_sync import sync_pi_media

    fence, target = _fence(tmp_path)
    target.write_text('{"mcpServers":{"anvil-media-mcp":{"url":"https://retired.example"}}}', encoding="utf-8")
    grant = fence.grant(
        canonical_contract=_CONTRACT, generation=1, effects=("pi-media",), target_paths=(target,),
    )
    wrong_root = tmp_path / "other-backups"
    before = target.read_bytes()
    with pytest.raises(PropagationFenceError, match="backup_root_mismatch"):
        sync_pi_media(
            mcp_config=str(target), backup_root=str(wrong_root), withdraw=True,
            dry_run=False, confirm=True, fence=fence, grant=grant,
            canonical_contract=_CONTRACT,
        )
    assert target.read_bytes() == before
    assert not wrong_root.exists()
    assert not fence.journal_root.exists()


def test_expiry_is_rechecked_at_pre_replace_boundary(tmp_path):
    import json
    from datetime import datetime, timezone

    now = [datetime(2026, 9, 27, 12, tzinfo=timezone.utc)]
    target = tmp_path / "catalog.json"; target.write_bytes(b"before"); _private_file(target)
    owner = TrustedNativeOwner(
        "owner-1", "catalog-1", tmp_path, backup_root=tmp_path / "backups",
        clock=lambda: now[0],
        current_authority=lambda _digest, _generation, _epoch: True, effect_bindings={"catalog": ("catalog-apply", target)},
    )
    fence = NativeMutationFence(owner, tmp_path / "ignored")
    grant = fence.grant(canonical_contract=_CONTRACT, generation=1, effects=("catalog",), target_paths=(target,))
    bundle = owner.backup_root / "backup-1"; bundle.mkdir(parents=True)
    _private_directory(owner.backup_root); _private_directory(bundle)
    backup = bundle / "00-catalog.json"; backup.write_bytes(b"before"); _private_file(backup)
    manifest = {"files": [{"source": str(target), "backup": backup.name, "existed": True,
                             "sha256": hashlib.sha256(b"before").hexdigest()}]}
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _private_file(bundle / "manifest.json")
    with pytest.raises(PropagationFenceError, match="approval_expired"):
        with fence.transaction(grant, canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
            journal.begin_effect("catalog", target, b"before", b"after")
            journal.bind_backup("catalog", bundle)
            now[0] = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
            journal.write("catalog", b"after")
    assert target.read_bytes() == b"before"
    assert fence.journal()["status"] == "recovery_required"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native lock reparse custody")
def test_windows_fence_refuses_junction_at_canonical_lock_leaf(tmp_path):
    from tests.bootstrap_windows_fixtures import WindowsFixtureTree

    tree = WindowsFixtureTree(tmp_path)
    root = tmp_path / "owner-lock"; root.mkdir(); tree.establish_full_control(root)
    target = root / "catalog.json"; target.write_bytes(b"before"); tree.establish_full_control(target)
    outside = tmp_path / "outside-lock"; outside.mkdir(); tree.establish_full_control(outside)
    fence = NativeMutationFence(
        TrustedNativeOwner(
            "owner-1", "catalog-1", root, backup_root=root / "backups",
            clock=_trusted_test_clock,
        current_authority=lambda _digest, _generation, _epoch: True, effect_bindings={"catalog": ("catalog-apply", target), "pi-media": ("catalog-apply", target)},
        ),
        root / "ignored",
    )
    grant = fence.grant(
        canonical_contract=_CONTRACT, generation=1, effects=("catalog",), target_paths=(target,),
    )
    lock_parent = fence.journal_root
    lock_parent.mkdir(parents=True)
    for path in (root / ".anvil-serving", lock_parent):
        tree.establish_full_control(path)
    lock_leaf = lock_parent / "catalog-1.lock"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(lock_leaf), str(outside)],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0
    try:
        with pytest.raises(PropagationFenceError, match="unsafe_custody_path"):
            with fence.transaction(grant, canonical_contract=_CONTRACT, target_paths=(target,)):
                pass
        assert target.read_bytes() == b"before"
    finally:
        lock_leaf.rmdir()


def test_grant_cannot_expand_private_effect_or_target_bindings(tmp_path):
    fence, target = _fence(tmp_path)
    unrelated = tmp_path / "unrelated.json"
    unrelated.write_bytes(b"preserve")
    cases = [
        (("unapproved-effect",), (target,), None, "effect_not_approved"),
        (("catalog",), (unrelated,), None, "grant_target_mismatch"),
        (("catalog",), (target, unrelated), {"catalog": target}, "grant_target_mismatch"),
        (("catalog",), (target,), {"catalog": unrelated}, "grant_target_mismatch"),
    ]
    for effects, paths, mapping, code in cases:
        with pytest.raises(PropagationFenceError, match=code):
            fence.grant(canonical_contract=_CONTRACT, generation=1, effects=effects,
                        target_paths=paths, effect_targets=mapping)
    assert unrelated.read_bytes() == b"preserve"
    assert not fence.journal_root.exists()


def test_private_bindings_are_snapshotted_and_permission_kind_is_checked(tmp_path):
    target = tmp_path / "catalog.json"
    original = {"catalog": ("monitoring-apply", target)}
    owner = TrustedNativeOwner("owner-1", "catalog-1", tmp_path,
        clock=_trusted_test_clock, current_authority=lambda _digest, _generation, _epoch: True, effect_bindings=original)
    original["catalog"] = ("catalog-apply", target)
    fence = NativeMutationFence(owner, tmp_path / "ignored")
    with pytest.raises(TypeError):
        owner.effect_bindings["catalog"] = original["catalog"]
    with pytest.raises(PropagationFenceError, match="effect_not_approved"):
        fence.grant(canonical_contract=_CONTRACT, generation=1, effects=("catalog",), target_paths=(target,))
    empty = NativeMutationFence(TrustedNativeOwner("owner-1", "catalog-1", tmp_path,
        clock=_trusted_test_clock), tmp_path / "ignored")
    with pytest.raises(PropagationFenceError, match="effect_not_approved"):
        empty.grant(canonical_contract=_CONTRACT, generation=1, effects=("catalog",), target_paths=(target,))
    assert not fence.journal_root.exists()


def _interrupted_backup(fence, target, *, write=False):
    grant = _grant(fence, target)
    with pytest.raises(RuntimeError, match="interrupted"):
        with fence.transaction(grant, canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
            journal.begin_effect("catalog", target, b"before", b"after")
            assert journal.contract_digest == grant.contract_digest
            assert journal.generation == 1
            assert journal.effect_identity("catalog") == (target, hashlib.sha256(b"after").hexdigest())
            backup = journal.backup([target])
            journal.bind_backup("catalog", backup)
            if write:
                journal.write("catalog", b"after")
            raise RuntimeError("interrupted")
    return fence.journal()


def test_resume_reconciles_desired_bytes_after_expiry_without_new_write(tmp_path):
    fence, target = _fence(tmp_path)
    original = _interrupted_backup(fence, target, write=True)
    mtime = target.stat().st_mtime_ns
    checks = []
    def quiescent(reservation, digest, generation):
        checks.append((reservation, digest, generation))
        return (reservation, digest, generation) == (original["reservation_id"], original["contract_digest"], 1)
    owner = replace(fence.owner, recovery_quiescent=quiescent,
                    clock=lambda: datetime(2026, 9, 29, tzinfo=timezone.utc),
                    current_authority=lambda *args: False)
    restarted = NativeMutationFence(owner, tmp_path / "ignored")
    with restarted.resume(_grant(restarted, target), canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
        assert journal.is_verified("catalog")
        assert journal.reservation_id == original["reservation_id"]
    assert checks == [(original["reservation_id"], original["contract_digest"], 1)]
    assert target.stat().st_mtime_ns == mtime
    assert restarted.journal()["status"] == "completed"
    assert restarted.journal()["effects"]["catalog"]["backup_id"] == original["effects"]["catalog"]["backup_id"]


def test_resume_retries_original_effect_only_with_current_authority_and_backup(tmp_path):
    fence, target = _fence(tmp_path)
    original = _interrupted_backup(fence, target)
    owner = replace(fence.owner, recovery_quiescent=lambda *args: True)
    restarted = NativeMutationFence(owner, tmp_path / "ignored")
    with restarted.resume(_grant(restarted, target), canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
        assert not journal.is_verified("catalog")
        journal.write("catalog", b"after")
        journal.observe_bytes("catalog", journal.read(target))
    assert target.read_bytes() == b"after"
    assert restarted.journal()["reservation_id"] == original["reservation_id"]
    assert set(restarted.journal()["effects"]) == {"catalog"}
    assert restarted.journal()["effects"]["catalog"]["backup_id"] == original["effects"]["catalog"]["backup_id"]


def test_resume_can_finish_preparation_only_when_original_bytes_remain(tmp_path):
    fence, target = _fence(tmp_path)
    with pytest.raises(RuntimeError, match="interrupted"):
        with fence.transaction(_grant(fence, target), canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
            journal.begin_effect("catalog", target, b"before", b"after")
            raise RuntimeError("interrupted")
    restarted = NativeMutationFence(replace(fence.owner, recovery_quiescent=lambda *args: True), tmp_path)
    with restarted.resume(_grant(restarted, target), canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
        assert not journal.backup_bound("catalog")
        journal.begin_effect("catalog", target, journal.read(target), b"after")
        journal.bind_backup("catalog", journal.backup([target]))
        journal.write("catalog", b"after")
        journal.observe_bytes("catalog", b"after")
    assert restarted.journal()["status"] == "completed"
    assert target.read_bytes() == b"after"


def test_resume_rejects_unbacked_preparation_if_original_bytes_drift(tmp_path):
    fence, target = _fence(tmp_path)
    with pytest.raises(RuntimeError):
        with fence.transaction(_grant(fence, target), canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
            journal.begin_effect("catalog", target, b"before", b"after")
            raise RuntimeError("interrupted")
    target.write_bytes(b"unexpected")
    restarted = NativeMutationFence(replace(fence.owner, recovery_quiescent=lambda *args: True), tmp_path)
    with pytest.raises(PropagationFenceError, match="backup_required"):
        with restarted.resume(_grant(restarted, target), canonical_contract=_CONTRACT, target_paths=(target,)):
            pass
    assert target.read_bytes() == b"unexpected"


@pytest.mark.parametrize("failure", ["unknown-process", "expired", "stale", "drift", "backup"])
def test_resume_refuses_unknown_custody_expired_writes_drift_and_bad_backup(tmp_path, failure):
    fence, target = _fence(tmp_path)
    original = _interrupted_backup(fence, target)
    owner = replace(fence.owner, recovery_quiescent=lambda *args: failure != "unknown-process",
                    current_authority=lambda *args: failure != "stale")
    if failure == "expired":
        owner = replace(owner, clock=lambda: datetime(2026, 9, 29, tzinfo=timezone.utc))
    if failure == "drift":
        target.write_bytes(b"external")
    if failure == "backup":
        (Path(original["effects"]["catalog"]["backup_path"]) / "manifest.json").write_bytes(b"{}")
    restarted = NativeMutationFence(owner, tmp_path / "ignored")
    with pytest.raises(PropagationFenceError):
        with restarted.resume(_grant(restarted, target), canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
            journal.write("catalog", b"after")
    assert target.read_bytes() == (b"external" if failure == "drift" else b"before")
    assert restarted.journal()["status"] == "recovery_required"


def test_current_authority_is_rechecked_before_replacement(tmp_path):
    from anvil_serving.client_catalog_sync import _backup
    fence, target = _fence(tmp_path)
    current = [True]
    fence = NativeMutationFence(replace(fence.owner, current_authority=lambda *args: current[0]), tmp_path)
    with pytest.raises(PropagationFenceError, match="stale_generation"):
        with fence.transaction(_grant(fence, target), canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
            journal.begin_effect("catalog", target, b"before", b"after")
            backup = _backup([target], fence.owner.backup_root, "a" * 64, journal=journal)
            journal.bind_backup("catalog", backup)
            current[0] = False
            journal.write("catalog", b"after")
    assert target.read_bytes() == b"before"
    missing = NativeMutationFence(replace(fence.owner, current_authority=None), tmp_path)
    with pytest.raises(PropagationFenceError, match="owner_authority_unavailable"):
        with missing.transaction(_grant(missing, target), canonical_contract=_CONTRACT, target_paths=(target,)):
            pytest.fail("missing authority entered transaction")


def test_resume_does_not_reuse_verified_state_when_bytes_returned_to_before(tmp_path):
    from anvil_serving.client_catalog_sync import _backup
    fence, target = _fence(tmp_path)
    with pytest.raises(RuntimeError):
        with fence.transaction(_grant(fence, target), canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
            journal.begin_effect("catalog", target, b"before", b"after")
            journal.bind_backup("catalog", _backup([target], fence.owner.backup_root, "a" * 64, journal=journal))
            journal.write("catalog", b"after")
            journal.observe_bytes("catalog", b"after")
            raise RuntimeError("later interruption")
    target.write_bytes(b"before")
    fence = NativeMutationFence(replace(fence.owner, recovery_quiescent=lambda *args: True), tmp_path)
    with pytest.raises(PropagationFenceError, match="recovery_required"):
        with fence.resume(_grant(fence, target), canonical_contract=_CONTRACT, target_paths=(target,)) as journal:
            assert not journal.is_verified("catalog")
    assert fence.journal()["status"] == "recovery_required"
    assert target.read_bytes() == b"before"
