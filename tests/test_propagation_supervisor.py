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


DIGEST = "a" * 64


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _profile(argv: tuple[str, ...], **extra: object) -> ExecutionProfile:
    with open(sys.executable, "rb", buffering=0) as handle:
        executable = hashlib.file_digest(handle, "sha256").hexdigest()
    return ExecutionProfile("profile-1", DIGEST, argv, executable, **extra)


def _contract(deadline: datetime, now: datetime | None = None) -> dict[str, object]:
    now = _now() if now is None else now
    value: dict[str, object] = {
        "schema": "anvil-propagation/v1", "scope": "scope-1", "revision": "revision-1", "generation": 1,
        "approval_ref": "approval-1", "approval_digest": DIGEST, "activation_ref": "activation-1", "activation_digest": DIGEST,
        "inputs": {"catalog_digest": DIGEST, "monitoring_inventory_digest": DIGEST, "execution_profile_digest": DIGEST, "artifact_digest": DIGEST, "installation_inventory_digest": DIGEST},
        "effect_set_digest": "", "targets": [{"target_id": "target-1", "installation_id": "installation-1", "profile_id": "profile-1", "runtime_id": "runtime-1", "resource_keys": ["catalog-1"], "expected_identity_ref": "identity-1", "expected_identity_digest": DIGEST, "checks": ["catalog-equal"], "effects": ["catalog-apply"]}],
        "execution_profile_ref": "profile-1", "execution_profile_digest": DIGEST,
        "session_policy": {"preserve_active_conversations": True, "loaded_state_required": True, "idle_reload": False},
        "preview_policy": {"all_required_targets": True, "web_runtime_required": True, "monitoring_required": True},
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "deadline_at": deadline.strftime("%Y-%m-%dT%H:%M:%SZ"),
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
    now = _now()
    contract = _contract(now + timedelta(minutes=5), now)
    return store, store.submit(_binding(contract), now)[0]


def _wait(supervisor: PropagationSupervisor, job_id: str) -> dict[str, object]:
    for _ in range(200):
        result = supervisor.observe(job_id)
        if result["state"] in {"applied", "failed", "cancelled", "recovery_required"}:
            return result
        time.sleep(0.02)
    raise AssertionError("supervisor did not settle")


def test_delayed_collection_captures_a_fresh_execution_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    collection_time = _now()
    execution_time = collection_time + timedelta(minutes=6)

    class ControlledDateTime:
        current = collection_time

        @classmethod
        def now(cls, tz=None):
            assert tz is timezone.utc
            return cls.current

    monkeypatch.setattr(sys.modules[__name__], "datetime", ControlledDateTime)
    assert _now() == collection_time
    ControlledDateTime.current = execution_time
    store, submitted = _submitted(tmp_path, "pass")
    assert submitted["intent_id"] == "intent-1"
    contract = json.loads(store.lookup_internal(submitted["job_id"])["canonical_contract"])
    assert contract["issued_at"] == execution_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert contract["deadline_at"] == (execution_time + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_gated_effect_runs_only_after_durable_registration(tmp_path: Path):
    marker = tmp_path / "effect"
    script = "import json,pathlib,sys; c=json.load(sys.stdin); pathlib.Path(sys.argv[1]).write_text('ran'); e=[{**item,'state':'applied'} for item in c['planned_effects']]; print(json.dumps({'outcome':'applied','native_effects':e,'quiescent':True}))"
    profile = _profile((sys.executable, "-c", script, str(marker)))
    store = JobStore(tmp_path / "jobs.sqlite3", {profile.profile_id: profile})
    now = _now()
    binding = _binding(_contract(now + timedelta(minutes=5), now))
    job, duplicate = store.submit(binding, now)
    assert not duplicate
    replay, duplicate = store.submit(binding, now)
    assert duplicate and replay["job_id"] == job["job_id"]
    supervisor = PropagationSupervisor(store, reconcile=lambda _job, result: result["outcome"])
    supervisor.launch(job["job_id"])
    result = _wait(supervisor, job["job_id"])
    assert marker.read_text() == "ran"
    assert result["state"] == "applied" and result["convergence_ref"]
    changed = dict(binding)
    changed["operation_id"] = "operation-2"
    with pytest.raises(PropagationJobError, match="job_conflict"):
        store.submit(changed, now)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_unregistered_child_refuses_before_effect_gate(tmp_path: Path):
    marker = tmp_path / "effect"
    script = "import json,pathlib,sys; json.load(sys.stdin); pathlib.Path(" + repr(str(marker)) + ").write_text('ran')"
    store, job = _submitted(tmp_path, script, budget_seconds=1)
    # Recreate the submitted profile with a marker argv before custody; a direct
    # child cannot reach the fixed profile without the registered CAS.
    token = store.prepare_launch(job["job_id"], _now())
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
    assert second.observe(job["job_id"])["state"] == "running"
    with pytest.raises(PropagationJobError, match="launch_reconciliation_required"):
        second.launch(job["job_id"])
    first.cancel(job["job_id"])


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_fresh_supervisor_keeps_matching_live_child_and_reconciles_result(tmp_path: Path):
    script = "import json,sys,time; c=json.load(sys.stdin); time.sleep(.3); print(json.dumps({'outcome':'applied','native_effects':[{**item,'state':'applied'} for item in c['planned_effects']],'quiescent':True}))"
    store, job = _submitted(tmp_path, script, budget_seconds=3)
    first = PropagationSupervisor(store, reconcile=lambda _job, result: result["outcome"])
    first.launch(job["job_id"])
    fresh = PropagationSupervisor(JobStore(store.path), reconcile=lambda _job, result: result["outcome"])
    assert fresh.observe(job["job_id"])["state"] == "running"
    first._children[job["job_id"]].wait(timeout=3)
    assert _wait(fresh, job["job_id"])["state"] == "applied"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_restart_rechecks_durable_result_after_native_child_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    release, effects = tmp_path / "release", tmp_path / "effects"
    script = (
        "import json,pathlib,sys,time; c=json.load(sys.stdin); root=pathlib.Path(" + repr(str(tmp_path)) + "); "
        "(root/'started').write_text('yes');\n"
        "while not (root/'release').exists(): time.sleep(.01)\n"
        "with (root/'effects').open('a') as handle: handle.write('one\\n')\n"
        "print(json.dumps({'outcome':'applied','native_effects':[{**item,'state':'applied'} for item in c['planned_effects']],'quiescent':True}))"
    )
    store, job = _submitted(tmp_path, script, budget_seconds=5, heartbeat_seconds=1)
    first = PropagationSupervisor(store, reconcile=lambda _job, result: result["outcome"])
    first.launch(job["job_id"])
    for _ in range(100):
        if (tmp_path / "started").exists():
            break
        time.sleep(0.01)
    assert (tmp_path / "started").exists()
    fresh_store = JobStore(store.path)
    fresh = PropagationSupervisor(fresh_store, reconcile=lambda _job, result: result["outcome"])
    original = fresh_store.completed_child_result
    lookups = 0

    def complete_after_snapshot(job_id: str, identity: dict[str, object]) -> dict[str, object] | None:
        nonlocal lookups
        lookups += 1
        if lookups == 1:
            assert original(job_id, identity) is None
            release.write_text("go")
            first._children[job["job_id"]].wait(timeout=3)
            assert original(job_id, identity)["outcome"] == "applied"
            return None
        return original(job_id, identity)

    monkeypatch.setattr(fresh_store, "completed_child_result", complete_after_snapshot)
    assert fresh.observe(job["job_id"])["state"] == "applied"
    assert lookups == 2
    assert fresh.observe(job["job_id"])["state"] == "applied"
    assert effects.read_text() == "one\n"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_restart_refreshes_registered_snapshot_before_reconciling_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    marker = tmp_path / "effect"
    script = (
        "import json,pathlib,sys; c=json.load(sys.stdin); pathlib.Path(" + repr(str(marker)) + ").write_text('one'); "
        "print(json.dumps({'outcome':'applied','native_effects':[{**item,'state':'applied'} for item in c['planned_effects']],'quiescent':True}))"
    )
    store, job = _submitted(tmp_path, script, budget_seconds=5)
    token = store.prepare_launch(job["job_id"])
    process = subprocess.Popen(
        (sys.executable, "-m", "anvil_serving.control_plane.controller.propagation_supervisor", "--child", store.path, job["job_id"]),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        identity = supervisor_module._identity(process.pid)
        assert identity is not None
        store.register_launch(job["job_id"], token, identity)
        fresh_store = JobStore(store.path)
        fresh = PropagationSupervisor(fresh_store, reconcile=lambda _job, result: result["outcome"])
        original = fresh_store.lookup_internal
        snapshots = 0

        def registered_snapshot(job_id: str) -> dict[str, object]:
            nonlocal snapshots
            snapshots += 1
            if snapshots == 1:
                stale = original(job_id)
                assert stale["state"] == "registered"
                assert process.stdin is not None
                process.stdin.write(token.encode("ascii"))
                process.stdin.close()
                process.stdin = None
                process.wait(timeout=3)
                assert fresh_store.completed_child_result(job_id, identity)["outcome"] == "applied"
                return stale
            return original(job_id)

        monkeypatch.setattr(fresh_store, "lookup_internal", registered_snapshot)
        assert fresh.observe(job["job_id"])["state"] == "applied"
        assert snapshots >= 2
        assert fresh.observe(job["job_id"])["state"] == "applied"
        assert marker.read_text() == "one"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_fresh_supervisor_adopts_verified_pidfd_for_cancellation(tmp_path: Path):
    effects = tmp_path / "effects"
    script = (
        "import json,pathlib,sys,time; c=json.load(sys.stdin); root=pathlib.Path(" + repr(str(tmp_path)) + ")\n"
        "with (root/'effects').open('a') as handle: handle.write('one\\n'); (root/'first').write_text('yes')\n"
        "while not (root/'release').exists(): time.sleep(.01)\n"
        "with (root/'effects').open('a') as handle: handle.write('two\\n')\n"
        "print(json.dumps({'outcome':'applied','native_effects':[{**item,'state':'applied'} for item in c['planned_effects']],'quiescent':True}))"
    )
    store, job = _submitted(tmp_path, script, budget_seconds=5, heartbeat_seconds=1)
    first = PropagationSupervisor(store, reconcile=lambda _job, result: result["outcome"])
    first.launch(job["job_id"])
    try:
        for _ in range(100):
            if (tmp_path / "first").exists():
                break
            time.sleep(0.01)
        assert (tmp_path / "first").exists()
        fresh = PropagationSupervisor(JobStore(store.path), reconcile=lambda _job, result: result["outcome"])
        assert fresh.cancel(job["job_id"])["state"] == "requested"
        assert job["job_id"] not in fresh._pidfds
        assert _wait(fresh, job["job_id"])["state"] == "cancelled"
        assert effects.read_text() == "one\n"
        assert job["job_id"] not in fresh._pidfds
    finally:
        (tmp_path / "release").write_text("cleanup")
        process = first._children[job["job_id"]]
        if process.poll() is None:
            process.wait(timeout=3)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_cancel_reconciles_completed_child_before_identity_recovery(tmp_path: Path):
    marker = tmp_path / "effect"
    script = (
        "import json,pathlib,sys; c=json.load(sys.stdin); pathlib.Path(" + repr(str(marker)) + ").write_text('one'); "
        "print(json.dumps({'outcome':'applied','native_effects':[{**item,'state':'applied'} for item in c['planned_effects']],'quiescent':True}))"
    )
    store, job = _submitted(tmp_path, script, budget_seconds=4)
    first = PropagationSupervisor(store)
    first.launch(job["job_id"])
    first._children[job["job_id"]].wait(timeout=3)
    fresh = PropagationSupervisor(JobStore(store.path), reconcile=lambda _job, result: result["outcome"])
    assert fresh.cancel(job["job_id"])["state"] == "confirmed"
    assert fresh.observe(job["job_id"])["state"] == "applied"
    assert marker.read_text() == "one"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_cancellation_between_flag_check_and_child_custody_is_quiescent():
    profile = _profile((sys.executable, "-c", "pass"))
    children = supervisor_module.Children()
    cancelled = False

    def prepare_child() -> list[dict[str, str]]:
        nonlocal cancelled
        cancelled = True
        raise PropagationJobError("launch_reconciliation_required")

    try:
        result = supervisor_module._run_profile(
            profile, {}, lambda: cancelled, children, lambda: None, prepare_child, lambda _identity: None,
        )
    finally:
        children.close()
    assert result == {"outcome": "cancelled", "native_effects": [], "quiescent": True}


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_durable_cancel_before_signal_refuses_pre_popen_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    marker, entered = tmp_path / "effect", tmp_path / "prepare-entered"
    store, job = _submitted(tmp_path, "import pathlib; pathlib.Path(" + repr(str(marker)) + ").write_text('effect')")
    token = store.prepare_launch(job["job_id"])
    shim = (
        "import pathlib,sys,time; "
        "from anvil_serving.control_plane.controller.propagation_job_store import JobStore; "
        "from anvil_serving.control_plane.controller.propagation_supervisor import _child; "
        "original=JobStore.prepare_profile_child\n"
        "def wait_for_cancel(self, job_id, identity, *args):\n"
        " pathlib.Path(" + repr(str(entered)) + ").write_text('entered')\n"
        " deadline=time.monotonic()+3\n"
        " while self.lookup_internal(job_id)['state'] != 'cancellation_requested' and time.monotonic() < deadline: time.sleep(.001)\n"
        " return original(self, job_id, identity, *args)\n"
        "JobStore.prepare_profile_child=wait_for_cancel\n"
        "raise SystemExit(_child(['--child',sys.argv[1],sys.argv[2]]))"
    )
    child = subprocess.Popen(
        (sys.executable, "-c", shim, store.path, job["job_id"]),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        identity = supervisor_module._identity(child.pid)
        assert identity is not None and child.stdin is not None
        store.register_launch(job["job_id"], token, identity)
        child.stdin.write(token.encode("ascii"))
        child.stdin.close()
        child.stdin = None
        for _ in range(300):
            if entered.exists():
                break
            time.sleep(0.01)
        assert entered.exists()
        fresh = PropagationSupervisor(JobStore(store.path), reconcile=lambda _job, result: "cancelled" if result["outcome"] == "cancelled" else "uncertain")
        original_identity = supervisor_module._identity

        def after_cancel_before_signal(pid: int) -> dict[str, object] | None:
            child.wait(timeout=3)
            return original_identity(pid)

        monkeypatch.setattr(supervisor_module, "_identity", after_cancel_before_signal)
        assert fresh.cancel(job["job_id"])["state"] == "confirmed"
        assert fresh.observe(job["job_id"])["state"] == "cancelled"
        assert not marker.exists()
        assert store.pending_effects(job["job_id"]) == []
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_durable_cancel_before_begin_execution_is_recoverable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    marker, entered = tmp_path / "effect", tmp_path / "begin-entered"
    store, job = _submitted(tmp_path, "import pathlib; pathlib.Path(" + repr(str(marker)) + ").write_text('effect')")
    token = store.prepare_launch(job["job_id"])
    shim = (
        "import pathlib,sys,time; "
        "from anvil_serving.control_plane.controller.propagation_job_store import JobStore; "
        "from anvil_serving.control_plane.controller.propagation_supervisor import _child; "
        "original=JobStore.begin_execution\n"
        "def wait_for_cancel(self, job_id, token, identity, *args):\n"
        " pathlib.Path(" + repr(str(entered)) + ").write_text('entered')\n"
        " deadline=time.monotonic()+3\n"
        " while self.lookup_internal(job_id)['state'] != 'cancellation_requested' and time.monotonic() < deadline: time.sleep(.001)\n"
        " return original(self, job_id, token, identity, *args)\n"
        "JobStore.begin_execution=wait_for_cancel\n"
        "raise SystemExit(_child(['--child',sys.argv[1],sys.argv[2]]))"
    )
    child = subprocess.Popen(
        (sys.executable, "-c", shim, store.path, job["job_id"]),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        identity = supervisor_module._identity(child.pid)
        assert identity is not None and child.stdin is not None
        store.register_launch(job["job_id"], token, identity)
        child.stdin.write(token.encode("ascii"))
        child.stdin.close()
        child.stdin = None
        for _ in range(300):
            if entered.exists():
                break
            time.sleep(0.01)
        assert entered.exists()
        fresh = PropagationSupervisor(JobStore(store.path), reconcile=lambda _job, result: "cancelled" if result["outcome"] == "cancelled" else "uncertain")
        original_identity = supervisor_module._identity

        def after_cancel_before_signal(pid: int) -> dict[str, object] | None:
            child.wait(timeout=3)
            return original_identity(pid)

        monkeypatch.setattr(supervisor_module, "_identity", after_cancel_before_signal)
        assert fresh.cancel(job["job_id"])["state"] == "confirmed"
        assert fresh.observe(job["job_id"])["state"] == "cancelled"
        assert not marker.exists()
        assert store.pending_effects(job["job_id"]) == []
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)


def test_pre_profile_cancellation_refuses_an_existing_child_reservation(tmp_path: Path):
    store, job = _submitted(tmp_path, "pass")
    now = _now()
    identity = {"pid": 1234, "start_ticks": "ticks-1", "boot_id": "boot-1"}
    token = store.prepare_launch(job["job_id"], now)
    store.register_launch(job["job_id"], token, identity, now)
    store.begin_execution(job["job_id"], token, identity, now)
    store.prepare_profile_child(job["job_id"], identity, now)
    store.request_cancel(job["job_id"], now)
    with pytest.raises(PropagationJobError, match="launch_reconciliation_required"):
        store.record_pre_profile_cancellation(job["job_id"], identity, now)
    assert store.pending_effects(job["job_id"])
    assert store.lookup_internal(job["job_id"])["state"] == "cancellation_requested"


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
def test_native_crash_with_empty_custody_has_supervisor_quiescence(tmp_path: Path):
    store, job = _submitted(tmp_path, "import json,sys; json.load(sys.stdin); raise SystemExit(1)")
    supervisor = PropagationSupervisor(store, reconcile=lambda _job, result: result["outcome"])
    supervisor.launch(job["job_id"])
    assert _wait(supervisor, job["job_id"])["state"] == "recovery_required"
    observed = store.lookup_internal(job["job_id"])
    result = store.completed_child_result(
        job["job_id"], {key: observed[key] for key in ("pid", "start_ticks", "boot_id")},
    )
    assert result["outcome"] == "uncertain"
    assert result["quiescent"] is True


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_unknown_descendant_custody_never_claims_quiescence():
    class UnknownChildren:
        def scan(self):
            return [object()], False

        def cleanup(self, _timeout: float):
            return True, False, False

    now = _now()
    result = supervisor_module._run_profile(
        _profile((sys.executable, "-c", "import json,sys; json.load(sys.stdin); print('{\"outcome\":\"applied\",\"native_effects\":[],\"quiescent\":true}')")),
        {"job_id": "job-1", "operation_id": "operation-1", "intent_id": "intent-1", "contract_digest": DIGEST,
         "resources": [], "deadline_at": (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "canonical_contract": b"{}"},
        lambda: False, UnknownChildren(), lambda: None, lambda: [], lambda _identity: None,
    )
    assert result == {"outcome": "uncertain", "native_effects": [], "quiescent": False}


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
    profile = _profile((sys.executable, "-c", "pass"))
    store = JobStore(tmp_path / "jobs.sqlite3", {profile.profile_id: profile})
    now = _now()
    original = _binding(_contract(now + timedelta(minutes=5), now))
    job, _ = store.submit(original, now)
    binding = _binding(_contract(now + timedelta(minutes=5), now))
    binding["intent_id"] = "intent-2"
    contract = binding["canonical_contract"]
    assert isinstance(contract, dict)
    contract["scope"], contract["revision"] = "scope-2", "revision-2"
    contract["effect_set_digest"] = effect_scope_digest(contract)
    with pytest.raises(PropagationJobError, match="resource_conflict"):
        store.submit(binding, now)
    replay, duplicate = store.submit(original, now + timedelta(days=1))
    assert duplicate and replay["job_id"] == job["job_id"]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_contract_deadline_stops_a_late_profile_result(tmp_path: Path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    profile = _profile((sys.executable, "-c", "import json,sys,time; json.load(sys.stdin); time.sleep(2); print(json.dumps({'outcome':'applied','native_effects':[],'quiescent':True}))"), budget_seconds=5)
    store = JobStore(tmp_path / "jobs.sqlite3", {profile.profile_id: profile})
    contract = _contract(now + timedelta(seconds=1), now)
    job, _ = store.submit(_binding(contract), now)
    supervisor = PropagationSupervisor(store, reconcile=lambda _job, result: result["outcome"])
    supervisor.launch(job["job_id"])
    assert _wait(supervisor, job["job_id"])["state"] == "recovery_required"


def test_planned_effect_ids_are_bound_to_the_full_contract():
    now = _now()
    first = _contract(now + timedelta(minutes=5), now)
    second = _contract(now + timedelta(minutes=5), now)
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
    now = _now()
    contract = _contract(now + timedelta(minutes=5), now)
    source = contract["targets"][0]
    contract["targets"] = [{**source, "target_id": f"target-{index:02d}", "installation_id": f"installation-{index:02d}", "resource_keys": [f"catalog-{index:02d}"]} for index in range(24)]
    contract["effect_set_digest"] = effect_scope_digest(contract)
    binding = _binding(contract)
    binding["resources"] = [f"catalog-{index:02d}" for index in range(24)]
    job, _ = store.submit(binding, now)
    supervisor = PropagationSupervisor(store, reconcile=lambda _job, result: result["outcome"])
    supervisor.launch(job["job_id"])
    assert _wait(supervisor, job["job_id"])["state"] == "applied"
    assert marker.read_text() == "24"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native supervisor")
def test_insufficient_semantic_result_capacity_refuses_before_effects(tmp_path: Path):
    marker = tmp_path / "effect"
    profile = _profile((sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('ran')", str(marker)), output_limit=5000)
    store = JobStore(tmp_path / "jobs.sqlite3", {profile.profile_id: profile})
    now = _now()
    contract = _contract(now + timedelta(minutes=5), now)
    source = contract["targets"][0]
    contract["targets"] = [{**source, "target_id": f"target-{index:02d}", "installation_id": f"installation-{index:02d}", "resource_keys": [f"catalog-{index:02d}"]} for index in range(24)]
    contract["effect_set_digest"] = effect_scope_digest(contract)
    binding = _binding(contract)
    binding["resources"] = [f"catalog-{index:02d}" for index in range(24)]
    job, _ = store.submit(binding, now)
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
