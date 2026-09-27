"""Real-process checks for the deliberately small native propagation supervisor."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

import anvil_serving.control_plane.controller.propagation_supervisor as supervisor_module
from anvil_serving.control_plane.controller.propagation_job_store import ExecutionProfile, JobStore, PropagationJobError
from anvil_serving.control_plane.controller.propagation_supervisor import PropagationSupervisor
from anvil_serving.control_plane.propagation import effect_scope_digest, parse_contract


NOW = datetime.now(timezone.utc).replace(microsecond=0)
DIGEST = "a" * 64


def _profile(argv: tuple[str, ...], **extra: object) -> ExecutionProfile:
    with open(sys.executable, "rb", buffering=0) as handle:
        executable = hashlib.file_digest(handle, "sha256").hexdigest()
    return ExecutionProfile("profile-1", DIGEST, argv, executable, **extra)


def _contract(deadline: datetime) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "anvil-propagation/v1", "scope": "scope-1", "revision": "revision-1", "generation": 1,
        "approval_ref": "approval-1", "approval_digest": DIGEST, "activation_ref": "activation-1", "activation_digest": DIGEST,
        "inputs": {"catalog_digest": DIGEST, "monitoring_inventory_digest": DIGEST, "execution_profile_digest": DIGEST, "artifact_digest": DIGEST, "installation_inventory_digest": DIGEST},
        "effect_set_digest": "", "targets": [{"target_id": "target-1", "installation_id": "installation-1", "profile_id": "profile-1", "runtime_id": "runtime-1", "resource_keys": ["catalog-1"], "expected_identity_ref": "identity-1", "expected_identity_digest": DIGEST, "checks": ["catalog-equal"], "effects": ["catalog-apply"]}],
        "execution_profile_ref": "profile-1", "execution_profile_digest": DIGEST,
        "session_policy": {"preserve_active_conversations": True, "loaded_state_required": True, "idle_reload": False},
        "preview_policy": {"all_required_targets": True, "web_runtime_required": True, "monitoring_required": True},
        "issued_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "deadline_at": deadline.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    value["effect_set_digest"] = effect_scope_digest(value)
    return value


def _binding(contract: dict[str, object]) -> dict[str, object]:
    return {"intent_id": "intent-1", "operation_id": "operation-1", "canonical_contract": contract,
            "preview_digest": "b" * 64, "profile_id": "profile-1", "profile_digest": DIGEST,
            "resources": ["catalog-1"], "deadline": contract["deadline_at"]}


def _submitted(tmp_path: Path, script: str, **profile_extra: object) -> tuple[JobStore, dict[str, object]]:
    profile = _profile((sys.executable, "-c", script), **profile_extra)
    store = JobStore(tmp_path / "jobs.sqlite3", {profile.profile_id: profile})
    contract = _contract(NOW + timedelta(minutes=5))
    return store, store.submit(_binding(contract), NOW)[0]


def _wait(supervisor: PropagationSupervisor, job_id: str) -> dict[str, object]:
    for _ in range(200):
        result = supervisor.observe(job_id)
        if result["state"] in {"applied", "failed", "cancelled", "recovery_required"}:
            return result
        time.sleep(0.02)
    raise AssertionError("supervisor did not settle")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_gated_effect_runs_only_after_durable_registration(tmp_path: Path):
    marker = tmp_path / "effect"
    script = "import json,pathlib,sys; c=json.load(sys.stdin); pathlib.Path(sys.argv[1]).write_text('ran'); e=[{**item,'state':'applied'} for item in c['planned_effects']]; print(json.dumps({'outcome':'applied','native_effects':e,'quiescent':True}))"
    profile = _profile((sys.executable, "-c", script, str(marker)))
    store = JobStore(tmp_path / "jobs.sqlite3", {profile.profile_id: profile})
    job, duplicate = store.submit(_binding(_contract(NOW + timedelta(minutes=5))), NOW)
    assert not duplicate
    replay, duplicate = store.submit(_binding(_contract(NOW + timedelta(minutes=5))), NOW)
    assert duplicate and replay["job_id"] == job["job_id"]
    supervisor = PropagationSupervisor(store, reconcile=lambda _job, result: result["outcome"])
    supervisor.launch(job["job_id"])
    result = _wait(supervisor, job["job_id"])
    assert marker.read_text() == "ran"
    assert result["state"] == "applied" and result["convergence_ref"]
    changed = _binding(_contract(NOW + timedelta(minutes=5)))
    changed["operation_id"] = "operation-2"
    with pytest.raises(PropagationJobError, match="job_conflict"):
        store.submit(changed, NOW)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_unregistered_child_refuses_before_effect_gate(tmp_path: Path):
    marker = tmp_path / "effect"
    script = "import json,pathlib,sys; json.load(sys.stdin); pathlib.Path(" + repr(str(marker)) + ").write_text('ran')"
    store, job = _submitted(tmp_path, script, budget_seconds=1)
    # Recreate the submitted profile with a marker argv before custody; a direct
    # child cannot reach the fixed profile without the registered CAS.
    token = store.prepare_launch(job["job_id"], NOW)
    completed = subprocess.run((sys.executable, "-m", "anvil_serving.control_plane.controller.propagation_supervisor", "--child", store.path, job["job_id"]), input=token.encode(), stdout=subprocess.PIPE, check=False)
    assert completed.returncode == 5
    assert not marker.exists()
    assert store.lookup_internal(job["job_id"])["state"] == "launch_custody"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_restart_or_lost_ack_requires_recovery_not_relaunch(tmp_path: Path):
    script = "import json,sys,time; json.load(sys.stdin); time.sleep(2); print(json.dumps({'outcome':'applied','native_effects':[],'quiescent':True}))"
    store, job = _submitted(tmp_path, script, budget_seconds=3)
    first = PropagationSupervisor(store, reconcile=lambda _job, result: result["outcome"])
    first.launch(job["job_id"])
    second = PropagationSupervisor(JobStore(store.path))
    assert second.observe(job["job_id"])["state"] == "recovery_required"
    with pytest.raises(PropagationJobError, match="launch_reconciliation_required"):
        second.launch(job["job_id"])
    first.cancel(job["job_id"])


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_cancel_is_only_confirmed_after_child_reports_quiescence(tmp_path: Path):
    script = "import json,sys,time; json.load(sys.stdin); time.sleep(5); print(json.dumps({'outcome':'applied','native_effects':[],'quiescent':True}))"
    store, job = _submitted(tmp_path, script, budget_seconds=8)
    supervisor = PropagationSupervisor(store, reconcile=lambda _job, result: result["outcome"])
    supervisor.launch(job["job_id"])
    for _ in range(100):
        supervisor.observe(job["job_id"])
        if store.lookup_internal(job["job_id"])["state"] == "executing":
            break
        time.sleep(0.01)
    assert store.lookup_internal(job["job_id"])["state"] == "executing"
    assert supervisor.cancel(job["job_id"])["state"] == "requested"
    assert _wait(supervisor, job["job_id"])["state"] == "cancelled"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_oversized_or_untruthful_result_stays_recovery_required(tmp_path: Path):
    script = "import sys,json; json.load(sys.stdin); sys.stdout.write('x'*70000)"
    store, job = _submitted(tmp_path, script, output_limit=4096)
    supervisor = PropagationSupervisor(store, reconcile=lambda _job, result: result["outcome"])
    supervisor.launch(job["job_id"])
    assert _wait(supervisor, job["job_id"])["state"] == "recovery_required"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_detached_profile_descendant_prevents_success(tmp_path: Path):
    script = "import json,os,sys,time; json.load(sys.stdin); child=os.fork(); child or (os.setsid(),time.sleep(5),os._exit(0)); child and print(json.dumps({'outcome':'applied','native_effects':[],'quiescent':True}))"
    store, job = _submitted(tmp_path, script, budget_seconds=1)
    supervisor = PropagationSupervisor(store, reconcile=lambda _job, result: result["outcome"])
    supervisor.launch(job["job_id"])
    assert _wait(supervisor, job["job_id"])["state"] == "recovery_required"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_failed_native_result_requires_matching_owner_reconciliation(tmp_path: Path):
    script = "import json,sys; json.load(sys.stdin); print(json.dumps({'outcome':'failed','native_effects':[],'quiescent':True})); raise SystemExit(1)"
    store, job = _submitted(tmp_path, script)
    supervisor = PropagationSupervisor(store, reconcile=lambda _job, result: result["outcome"])
    supervisor.launch(job["job_id"])
    assert _wait(supervisor, job["job_id"])["state"] == "failed"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_child_heartbeat_and_deadline_are_real_observations(tmp_path: Path):
    script = "import json,sys,time; json.load(sys.stdin); time.sleep(3); print(json.dumps({'outcome':'applied','native_effects':[],'quiescent':True}))"
    store, job = _submitted(tmp_path, script, budget_seconds=2, heartbeat_seconds=1)
    supervisor = PropagationSupervisor(store, reconcile=lambda _job, result: result["outcome"])
    supervisor.launch(job["job_id"])
    for _ in range(100):
        if store.lookup_internal(job["job_id"])["state"] == "executing":
            break
        time.sleep(0.01)
    initial = store.lookup_internal(job["job_id"])["heartbeat_at"]
    time.sleep(1.1)
    assert store.lookup_internal(job["job_id"])["heartbeat_at"] > initial
    assert _wait(supervisor, job["job_id"])["state"] == "recovery_required"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_leader_death_never_implies_quiescence(tmp_path: Path):
    script = "import json,sys,time; json.load(sys.stdin); time.sleep(1)"
    store, job = _submitted(tmp_path, script)
    supervisor = PropagationSupervisor(store, reconcile=lambda _job, result: result["outcome"])
    supervisor.launch(job["job_id"])
    for _ in range(100):
        if store.lookup_internal(job["job_id"])["state"] == "executing":
            break
        time.sleep(0.01)
    supervisor._children[job["job_id"]].kill()
    assert _wait(supervisor, job["job_id"])["state"] == "recovery_required"


def test_non_linux_refuses_native_supervision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    profile = _profile((sys.executable, "-c", "pass"))
    monkeypatch.setattr(supervisor_module.sys, "platform", "win32")
    with pytest.raises(PropagationJobError, match="supervision_unavailable"):
        PropagationSupervisor(JobStore(tmp_path / "jobs.sqlite3", {profile.profile_id: profile}))


def test_resource_union_is_atomic_and_duplicate_precedes_deadline(tmp_path: Path):
    store, job = _submitted(tmp_path, "pass")
    binding = _binding(_contract(NOW + timedelta(minutes=5)))
    binding["intent_id"] = "intent-2"
    contract = binding["canonical_contract"]
    assert isinstance(contract, dict)
    contract["scope"], contract["revision"] = "scope-2", "revision-2"
    contract["effect_set_digest"] = effect_scope_digest(contract)
    with pytest.raises(PropagationJobError, match="resource_conflict"):
        store.submit(binding, NOW)
    replay, duplicate = store.submit(_binding(_contract(NOW + timedelta(minutes=5))), NOW + timedelta(days=1))
    assert duplicate and replay["job_id"] == job["job_id"]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_contract_deadline_stops_a_late_profile_result(tmp_path: Path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    profile = _profile((sys.executable, "-c", "import json,sys,time; json.load(sys.stdin); time.sleep(2); print(json.dumps({'outcome':'applied','native_effects':[],'quiescent':True}))"), budget_seconds=5)
    store = JobStore(tmp_path / "jobs.sqlite3", {profile.profile_id: profile})
    contract = _contract(now + timedelta(seconds=1))
    job, _ = store.submit(_binding(contract), now)
    supervisor = PropagationSupervisor(store, reconcile=lambda _job, result: result["outcome"])
    supervisor.launch(job["job_id"])
    assert _wait(supervisor, job["job_id"])["state"] == "recovery_required"


def test_planned_effect_ids_are_bound_to_the_full_contract():
    first = _contract(NOW + timedelta(minutes=5))
    second = _contract(NOW + timedelta(minutes=5))
    second["scope"], second["revision"] = "scope-2", "revision-2"
    second["effect_set_digest"] = effect_scope_digest(second)
    first_contract, second_contract = parse_contract(first), parse_contract(second)
    assert JobStore._planned_effects(first_contract.digest, first_contract.canonical) != JobStore._planned_effects(second_contract.digest, second_contract.canonical)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_complete_multi_target_result_uses_private_ledger_reference(tmp_path: Path):
    marker = tmp_path / "effect"
    script = "import json,pathlib,sys; c=json.load(sys.stdin); pathlib.Path(sys.argv[1]).write_text(str(len(c['planned_effects']))); print(json.dumps({'outcome':'applied','native_effects':[{**item,'state':'applied'} for item in c['planned_effects']],'quiescent':True}))"
    profile = _profile((sys.executable, "-c", script, str(marker)))
    store = JobStore(tmp_path / "jobs.sqlite3", {profile.profile_id: profile})
    contract = _contract(NOW + timedelta(minutes=5))
    source = contract["targets"][0]
    contract["targets"] = [{**source, "target_id": f"target-{index:02d}", "installation_id": f"installation-{index:02d}", "resource_keys": [f"catalog-{index:02d}"]} for index in range(24)]
    contract["effect_set_digest"] = effect_scope_digest(contract)
    binding = _binding(contract)
    binding["resources"] = [f"catalog-{index:02d}" for index in range(24)]
    job, _ = store.submit(binding, NOW)
    supervisor = PropagationSupervisor(store, reconcile=lambda _job, result: result["outcome"])
    supervisor.launch(job["job_id"])
    assert _wait(supervisor, job["job_id"])["state"] == "applied"
    assert marker.read_text() == "24"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_insufficient_semantic_result_capacity_refuses_before_effects(tmp_path: Path):
    marker = tmp_path / "effect"
    profile = _profile((sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('ran')", str(marker)), output_limit=5000)
    store = JobStore(tmp_path / "jobs.sqlite3", {profile.profile_id: profile})
    contract = _contract(NOW + timedelta(minutes=5))
    source = contract["targets"][0]
    contract["targets"] = [{**source, "target_id": f"target-{index:02d}", "installation_id": f"installation-{index:02d}", "resource_keys": [f"catalog-{index:02d}"]} for index in range(24)]
    contract["effect_set_digest"] = effect_scope_digest(contract)
    binding = _binding(contract)
    binding["resources"] = [f"catalog-{index:02d}" for index in range(24)]
    job, _ = store.submit(binding, NOW)
    supervisor = PropagationSupervisor(store, reconcile=lambda _job, result: result["outcome"])
    supervisor.launch(job["job_id"])
    assert _wait(supervisor, job["job_id"])["state"] == "recovery_required"
    assert not marker.exists()


def test_native_result_rejects_non_ascii_or_control_opaque_fields():
    for opaque in ("receipt-\u00e9", "receipt\n1"):
        raw = json.dumps({"outcome": "applied", "native_effects": [{"effect_id": "effect-1", "state": "applied", "receipt_ref": opaque}], "quiescent": True}).encode("utf-8")
        assert supervisor_module._profile_result(raw)["outcome"] == "uncertain"
    for state in ([], {}):
        raw = json.dumps({"outcome": "applied", "native_effects": [{"effect_id": "effect-1", "state": state, "receipt_ref": "receipt-1"}], "quiescent": True}).encode("utf-8")
        assert supervisor_module._profile_result(raw)["outcome"] == "uncertain"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_restart_reconciles_completed_child_result_without_relaunch(tmp_path: Path):
    script = "import json,sys; c=json.load(sys.stdin); print(json.dumps({'outcome':'applied','native_effects':[{**item,'state':'applied'} for item in c['planned_effects']],'quiescent':True}))"
    store, job = _submitted(tmp_path, script)
    first = PropagationSupervisor(store, reconcile=lambda _job, result: result["outcome"])
    first.launch(job["job_id"])
    first._children[job["job_id"]].wait(timeout=2)
    recovered = PropagationSupervisor(JobStore(store.path), reconcile=lambda _job, result: result["outcome"])
    assert recovered.observe(job["job_id"])["state"] == "applied"
