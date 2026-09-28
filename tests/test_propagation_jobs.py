"""Actual scoped controller dispatch, native effects and independent readback."""
from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sys
from threading import Barrier
import time

import pytest

from anvil_serving.control_plane.propagation import (
    ActiveIdentity, ApprovedAuthority, effect_scope_digest, parse_contract,
)
from anvil_serving.control_plane.propagation_jobs import (
    PropagationProfile, PropagationService, PropagationJobError, _hash,
)
from anvil_serving.control_plane.controller.propagation_store import PropagationIntentStore
from anvil_serving.control_plane.controller.propagation_job_store import JobStore, ExecutionProfile
from anvil_serving.control_plane.controller.propagation_supervisor import PropagationSupervisor
from tests.test_propagation_contracts import _contract, _receipt, _offline_row
from tests.test_propagation_contracts import _matches_declared_schema
from anvil_serving.control_plane.propagation import capability_declaration
from tests.test_controller import running_controller, _request, _authorization_policy


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="release execution owner is Linux")
DIGEST = "a" * 64


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _stamp(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class ControlledOwner:
    def __init__(self, root, count=1):
        self.root = root
        self.marker = root / "catalog"
        self.reads = []
        self.corrupt = None
        self.preview_reads = 0
        value = _contract(issued_at=_stamp(_now() - timedelta(minutes=1)), deadline_at=_stamp(_now() + timedelta(hours=1)))
        template = value["targets"][0]
        value["targets"] = [{**template, "target_id": f"target-{i:03}", "installation_id": f"installation-{i:03}", "resource_keys": [f"catalog-{i:03}"]} for i in range(count)]
        value["effect_set_digest"] = effect_scope_digest(value)
        self.contract = parse_contract(value)
        runner = root / "native.py"
        runner.write_text("import json,pathlib,sys\nj=json.load(sys.stdin)\np=pathlib.Path(sys.argv[1])\np.write_text('accepted')\ne=[{**item,'state':'applied'} for item in j['planned_effects']]\nprint(json.dumps({'outcome':'applied','native_effects':e,'quiescent':True}))\n")
        profile = ExecutionProfile("profile-1", DIGEST, (sys.executable, str(runner), str(self.marker)),
            hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
            artifact_pins=((str(runner), hashlib.sha256(runner.read_bytes()).hexdigest()),), budget_seconds=10, heartbeat_seconds=1)
        self.jobs = JobStore(root / "propagation.sqlite", {"profile-1": profile})
        self.intents = PropagationIntentStore(root / "propagation.sqlite")
        self.supervisor = PropagationSupervisor(self.jobs, reconcile=self.reconcile)
        self.profile = PropagationProfile("profile-1", DIGEST, "executor-1",
            lambda ref: self.contract.canonical if ref == "approval-1" else b"{}",
            lambda ref: ApprovedAuthority("approval-1", DIGEST, self.contract.digest) if ref == "approval-1" else None,
            lambda: ActiveIdentity("activation-1", DIGEST), self.preview, self.observe)
        self.service = PropagationService(self.intents, self.jobs, self.supervisor, self.profile)

    def reconcile(self, job, result):
        digests = [_hash(["effect/v1", self.contract.digest, target["target_id"], effect])
                   for target in self.contract.value["targets"] for effect in target["effects"]]
        expected = [{"effect_id": "effect-" + digest, "state": "applied", "receipt_ref": "native-" + digest} for digest in digests]
        return "applied" if self.marker.read_text() == "accepted" and result["native_effects"] == expected else "uncertain"

    def preview(self, raw):
        self.preview_reads += 1
        rows = []
        for target in parse_contract(raw).value["targets"]:
            rows.append({**{k: target[k] for k in ("target_id", "installation_id", "profile_id", "runtime_id")},
                "ready": True, "pending_reason": None, "observed_at": _stamp(_now()),
                "observed_digest": DIGEST, "permitted_effects": target["effects"]})
        return {"observed_at": _stamp(_now()), "targets": rows}

    def observe(self, raw, job, kind, verification_id):
        contract = parse_contract(raw)
        self.reads.append((kind, verification_id))
        applied = self.marker.exists()
        exact = applied and self.marker.read_text() == "accepted"
        stamp = _stamp(_now())
        outcomes, checks, receipts = [], [], []
        for target in contract.value["targets"]:
            row = {**_offline_row(), **{k: target[k] for k in ("target_id", "installation_id", "profile_id", "runtime_id")},
                   "check_set_digest": _hash(target["checks"])}
            reason = None if exact else "verification-failed" if applied else "job-running"
            if applied:
                row.update(applied=True, verified=exact, applied_revision="revision-1", verified_revision="revision-1" if exact else None,
                    outcome="success" if exact else "failed", observed_at=stamp, last_contact_at=stamp,
                    age_seconds=0, freshness="fresh", pending_reason=reason,
                    session_state={"files": "accepted", "new_session": "accepted", "existing_session": "accepted"})
                receipts.append(_receipt(contract_digest=contract.digest, job_id=job["job_id"], effect_id=job["operation_id"],
                    target_id=target["target_id"], installation_id=target["installation_id"], observed_at=stamp,
                    check_set_digest=_hash(target["checks"]), verified=exact, outcome="success" if exact else "failed",
                    failure_code=None if exact else "verification-failed"))
            else:
                row["pending_reason"] = reason
            outcomes.append(row)
            checks.extend({"target_id": target["target_id"], "check_id": check, "outcome": "success" if exact else "failed" if applied else "pending",
                           "pending_reason": reason, "observed_at": stamp if applied else None,
                           "evidence_ref": "readback-1" if applied else None, "evidence_digest": DIGEST if applied else None} for check in target["checks"])
        result = {"contract_digest": contract.digest, "job_id": job["job_id"], "operation_id": job["operation_id"],
                  "kind": kind, "verification_id": verification_id, "observed_at": stamp,
                  "changed": int(kind == "convergence" and not exact), "reloads": 0,
                  "outcomes": outcomes, "checks": checks, "receipts": receipts}
        if self.corrupt:
            self.corrupt(result)
        return result

    def accept(self):
        return self.service.handle("propagation.accept.v1", {"approval_ref": "approval-1", "request_id": "request-1"}, caller_id="admission")

    def submit(self):
        accepted = self.accept()
        preview = self.service.preview(accepted["intent_id"])
        operation = _hash(["effect/v1", accepted["contract_digest"], "fleet", "apply"])
        submitted = self.service.submit(accepted["intent_id"], preview["preview_digest"], operation)
        for _ in range(200):
            status = self.supervisor.observe(submitted["job_id"])
            if status["state"] != "running":
                assert status["state"] == "applied", status
                return accepted, submitted, preview
            time.sleep(.02)
        pytest.fail("native child did not settle")


def test_actual_http_preserves_caller_and_composes_per_server(tmp_path):
    owner = ControlledOwner(tmp_path)
    policy = _authorization_policy(tmp_path, [
        {"id": "admission", "scopes": ["propagation:admission"], "credential_env": "ADMISSION"},
        {"id": "dispatcher", "scopes": ["propagation:dispatch"], "credential_env": "DISPATCH"},
        {"id": "reader", "scopes": ["propagation:status"], "credential_env": "READER"},
    ])
    env = {"ANVIL_CONTROLLER_TOKEN": "synthetic-legacy-controller", "ADMISSION": "synthetic-admission", "DISPATCH": "synthetic-dispatch", "READER": "synthetic-reader"}
    with running_controller(env=env, authorization_policy=policy, propagation_service=owner.service) as (host, port):
        def call(name, args, token):
            status, _, body, _ = _request(host, port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}, {"Authorization": "Bearer " + token})
            return status, body
        _, body = call("propagation.accept.v1", {"approval_ref": "approval-1", "request_id": "http-1"}, env["ADMISSION"])
        accepted = body["result"]["structuredContent"]
        assert accepted["ok"], body
        result = accepted["data"]
        with owner.intents._connection() as db:
            assert db.execute("SELECT caller_id FROM propagation_request_tombstones WHERE request_id='http-1'").fetchone()[0] == "admission"
        _, denied = call("propagation.accept.v1", {"approval_ref": "approval-1", "request_id": "wrong"}, env["READER"])
        assert "result" not in denied or denied["result"].get("isError")
        _, body = call("propagation.dispatch.pending.v1", {"cursor": None}, env["DISPATCH"])
        assert body["result"]["structuredContent"]["data"]["intents"][0]["intent_id"] == result["intent_id"]
        _, body = call("propagation.dispatch.record.v1", result, env["DISPATCH"])
        assert body["result"]["structuredContent"]["data"]["recorded"] is True
    # A separate server with no constructor service retains unavailable declarations.
    with running_controller(env=env, authorization_policy=policy) as (host, port):
        _, _, body, _ = _request(host, port, "POST", "/tools/call", {"name": "propagation_capabilities", "arguments": {}}, {"Authorization": "Bearer " + env["READER"]})
        assert all(not op["available"] for op in body["data"]["operations"])


def test_lost_submit_ack_and_expired_preview_resolve_original_job(tmp_path):
    owner = ControlledOwner(tmp_path)
    accepted, job, preview = owner.submit()
    count = owner.preview_reads
    late = PropagationService(owner.intents, owner.jobs, PropagationSupervisor(JobStore(owner.jobs.path)), replace(owner.profile, now=lambda: _now() + timedelta(days=2)))
    duplicate = late.submit(accepted["intent_id"], preview["preview_digest"], job["operation_id"])
    assert duplicate == {**job, "state": "existing"}
    assert owner.preview_reads == count and owner.marker.read_text() == "accepted"
    with pytest.raises(PropagationJobError, match="operation_identity_mismatch"):
        late.submit(accepted["intent_id"], preview["preview_digest"], "different-operation")


def test_fresh_verify_and_same_pass_convergence_detect_changed_bytes(tmp_path):
    owner = ControlledOwner(tmp_path)
    accepted, job, _ = owner.submit()
    verified = owner.service.verify(accepted["intent_id"], job["job_id"])
    assert verified["all_targets_verified"]
    assert verified["verification_id"] == owner.jobs.lookup(job["job_id"])["convergence_ref"]
    converged = owner.service.convergence(accepted["intent_id"], verified["verification_id"])
    assert converged["all_targets_verified"] and converged["changed"] == converged["reloads"] == 0
    assert owner.reads[-1] == ("convergence", verified["verification_id"])
    owner.marker.write_text("concurrent-drift")
    with pytest.raises(PropagationJobError, match="verification_failed"):
        owner.service.convergence(accepted["intent_id"], verified["verification_id"])
    assert owner.marker.read_text() == "concurrent-drift"


def test_pagination_retains_snapshot_binding_and_all_original_targets(tmp_path):
    owner = ControlledOwner(tmp_path, count=12)
    accepted, job, _ = owner.submit()
    args = {"intent_id": accepted["intent_id"], "job_id": job["job_id"], "cursor": None}
    first = owner.service.handle("fleet.propagation.verify.v1", args, caller_id="worker-1")
    cursor = first["next_cursor"]
    assert cursor is not None
    with pytest.raises(PropagationJobError, match="snapshot_unavailable"):
        owner.service.handle("fleet.propagation.verify.v1", {**args, "cursor": cursor}, caller_id="worker-2")
    rows = list(first["outcomes"])
    read_count = len(owner.reads)
    while cursor:
        page = owner.service.handle("fleet.propagation.verify.v1", {**args, "cursor": cursor}, caller_id="worker-1")
        assert page["snapshot_id"] == first["snapshot_id"] and page["observed_at"] == first["observed_at"]
        rows.extend(page["outcomes"])
        cursor = page["next_cursor"]
    assert len(rows) == 12 and [row["target_id"] for row in rows] == sorted({row["target_id"] for row in rows})
    assert len(owner.reads) == read_count


@pytest.mark.parametrize("corrupt", [
    lambda x: x.update(job_id="different-job"),
    lambda x: x["outcomes"].pop(),
    lambda x: x["checks"].pop(),
    lambda x: x["outcomes"][0].update(outcome="failed"),
    lambda x: x["outcomes"][0].update(freshness="stale"),
    lambda x: x["receipts"][0].update(observed_at="2020-01-01T00:00:00Z"),
], ids=["job", "target", "check", "outcome", "freshness", "receipt-age"])
def test_invalid_owner_observations_cannot_enable_convergence(tmp_path, corrupt):
    owner = ControlledOwner(tmp_path)
    accepted, job, _ = owner.submit()
    owner.corrupt = corrupt
    with pytest.raises((PropagationJobError, ValueError)):
        owner.service.verify(accepted["intent_id"], job["job_id"])
    owner.corrupt = None
    expected = _hash(["convergence/v1", owner.contract.digest, 1])
    with pytest.raises(PropagationJobError, match="verification_incomplete"):
        owner.service.convergence(accepted["intent_id"], expected)


def test_http_idempotency_header_cannot_replay_verification_or_convergence(tmp_path):
    owner = ControlledOwner(tmp_path)
    accepted, job, _ = owner.submit()
    policy = _authorization_policy(tmp_path, [{"id": "worker", "scopes": ["propagation:activity"], "credential_env": "WORKER"}])
    env = {"ANVIL_CONTROLLER_TOKEN": "synthetic-controller", "WORKER": "synthetic-worker-token"}
    schemas = {op["name"]: op["result_schema"] for op in capability_declaration()["operations"]}
    with running_controller(env=env, authorization_policy=policy, propagation_service=owner.service) as (host, port):
        def call(verb, **identity):
            name = f"fleet.propagation.{verb}.v1"
            _, _, body, _ = _request(host, port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": name, "arguments": {"intent_id": accepted["intent_id"], "cursor": None, **identity}}},
                {"Authorization": "Bearer " + env["WORKER"], "Idempotency-Key": "same-http-retry"})
            result = body["result"]["structuredContent"]
            if result["ok"]:
                assert _matches_declared_schema(result["data"], schemas[name])
            return result
        first = call("verify", job_id=job["job_id"])["data"]
        second = call("verify", job_id=job["job_id"])["data"]
        assert first["snapshot_id"] != second["snapshot_id"]
        assert first["verification_id"] == second["verification_id"]
        assert call("convergence", verification_id=first["verification_id"])["data"]["all_targets_verified"]
        reads = len(owner.reads)
        owner.marker.write_text("changed-after-verified")
        failed = call("convergence", verification_id=first["verification_id"])
        assert not failed["ok"] and failed["error"]["code"] == "verification_failed"
        assert len(owner.reads) == reads + 1 and owner.marker.read_text() == "changed-after-verified"


def test_offline_target_stays_in_status_and_cannot_enable_convergence(tmp_path):
    owner = ControlledOwner(tmp_path, count=2)
    accepted, job, _ = owner.submit()
    def offline(result):
        target = result["outcomes"][-1]
        target.update({key: value for key, value in _offline_row().items()
                       if key not in {"target_id", "installation_id", "profile_id", "runtime_id", "check_set_digest"}})
        result["receipts"] = result["receipts"][:-1]
        for check in result["checks"]:
            if check["target_id"] == target["target_id"]:
                check.update(outcome="pending", pending_reason="offline", observed_at=None, evidence_ref=None, evidence_digest=None)
    owner.corrupt = offline
    status = owner.service.status(job["job_id"])
    assert status["target_count"] == len(status["outcomes"]) == 2
    assert status["outcomes"][-1]["pending_reason"] == "offline"
    verified = owner.service.verify(accepted["intent_id"], job["job_id"])
    assert not verified["all_targets_verified"]
    with pytest.raises(PropagationJobError, match="verification_incomplete"):
        owner.service.convergence(accepted["intent_id"], verified["verification_id"])


def test_stale_intent_is_fenced_before_native_job_submission(tmp_path):
    owner = ControlledOwner(tmp_path)
    accepted = owner.accept()
    preview = owner.service.preview(accepted["intent_id"])
    newer = owner.contract.value
    newer.update(generation=2, revision="revision-2", approval_ref="approval-2")
    newer["effect_set_digest"] = effect_scope_digest(newer)
    contract = parse_contract(newer)
    owner.intents.admit(contract.canonical,
        approval_lookup=lambda _: ApprovedAuthority("approval-2", DIGEST, contract.digest),
        active_identity=ActiveIdentity("activation-1", DIGEST), caller_id="admission", request_id="newer", now=_now())
    with pytest.raises(PropagationJobError, match="stale_generation"):
        owner.service.handle("fleet.propagation.submit.v1", {"intent_id": accepted["intent_id"],
            "preview_digest": preview["preview_digest"], "operation_id": _hash(["effect/v1", owner.contract.digest, "fleet", "apply"])}, caller_id="worker")
    assert not owner.marker.exists() and owner.jobs.lookup_by_intent(accepted["intent_id"]) is None


def test_readback_after_mutation_deadline_uses_new_observation(tmp_path):
    owner = ControlledOwner(tmp_path)
    accepted, job, _ = owner.submit()
    later = _now() + timedelta(days=2)
    def observe(*args):
        result = owner.observe(*args)
        result["observed_at"] = _stamp(later)
        for row in result["outcomes"]:
            row.update(observed_at=_stamp(later), last_contact_at=_stamp(later))
        for row in result["checks"] + result["receipts"]:
            row["observed_at"] = _stamp(later)
        return result
    service = PropagationService(owner.intents, owner.jobs, owner.supervisor,
        replace(owner.profile, now=lambda: later, observe=observe))
    verified = service.verify(accepted["intent_id"], job["job_id"])
    assert verified["all_targets_verified"]
    assert service.convergence(accepted["intent_id"], verified["verification_id"])["all_targets_verified"]


def test_durable_unlaunched_job_resolves_after_authority_expiry(tmp_path):
    owner = ControlledOwner(tmp_path)
    accepted = owner.accept()
    preview = owner.service.preview(accepted["intent_id"])
    operation = _hash(["effect/v1", owner.contract.digest, "fleet", "apply"])
    job, _ = owner.jobs.submit({"intent_id": accepted["intent_id"], "operation_id": operation,
        "canonical_contract": owner.contract.canonical, "preview_digest": preview["preview_digest"],
        "profile_id": "profile-1", "profile_digest": DIGEST, "resources": ["catalog-000"],
        "deadline": owner.contract.value["deadline_at"]})
    late = PropagationService(owner.intents, owner.jobs, owner.supervisor,
        replace(owner.profile, now=lambda: _now() + timedelta(days=2)))
    resolved = late.submit(accepted["intent_id"], preview["preview_digest"], operation)
    assert resolved["job_id"] == job["job_id"] and resolved["state"] == "existing"
    assert owner.jobs.lookup(job["job_id"])["never_launched"] and not owner.marker.exists()


def test_future_pending_check_cannot_enter_status(tmp_path):
    owner = ControlledOwner(tmp_path)
    accepted = owner.accept()
    operation = _hash(["effect/v1", owner.contract.digest, "fleet", "apply"])
    job, _ = owner.jobs.submit({"intent_id": accepted["intent_id"], "operation_id": operation,
        "canonical_contract": owner.contract.canonical, "preview_digest": DIGEST,
        "profile_id": "profile-1", "profile_digest": DIGEST, "resources": ["catalog-000"],
        "deadline": owner.contract.value["deadline_at"]})
    owner.corrupt = lambda result: result["checks"][0].update(observed_at=_stamp(_now() + timedelta(days=1)))
    with pytest.raises(PropagationJobError, match="stale_observation"):
        owner.service.status(job["job_id"])


def test_concurrent_duplicate_submit_returns_one_durable_job(tmp_path, monkeypatch):
    owner = ControlledOwner(tmp_path)
    accepted = owner.accept()
    preview = owner.service.preview(accepted["intent_id"])
    operation = _hash(["effect/v1", owner.contract.digest, "fleet", "apply"])
    barrier, launched = Barrier(2), []
    original = owner.supervisor.launch
    def launch(job_id):
        barrier.wait(timeout=5)
        result = original(job_id)
        launched.append(job_id)
        return result
    monkeypatch.setattr(owner.supervisor, "launch", launch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(owner.service.submit, accepted["intent_id"], preview["preview_digest"], operation) for _ in range(2)]
        jobs = [future.result(timeout=10) for future in futures]
    assert jobs[0]["job_id"] == jobs[1]["job_id"]
    assert sorted(job["state"] for job in jobs) == ["created", "existing"]
    assert launched == [jobs[0]["job_id"]]
    for _ in range(200):
        status = owner.supervisor.observe(jobs[0]["job_id"])
        if status["state"] != "running":
            break
        time.sleep(.02)
    assert status["state"] == "applied" and owner.marker.read_text() == "accepted"


def test_same_job_reconciliation_requires_durable_quiescence_and_exact_owner_readback(tmp_path):
    owner = ControlledOwner(tmp_path)
    owner.supervisor.reconcile = lambda job, result: "uncertain"
    accepted = owner.accept()
    preview = owner.service.preview(accepted["intent_id"])
    operation = _hash(["effect/v1", accepted["contract_digest"], "fleet", "apply"])
    submitted = owner.service.submit(accepted["intent_id"], preview["preview_digest"], operation)
    for _ in range(200):
        job = owner.supervisor.observe(submitted["job_id"])
        if job["state"] == "recovery_required":
            break
        time.sleep(.02)
    assert job["state"] == "recovery_required"
    original = owner.jobs.lookup_internal(job["job_id"])
    with pytest.raises(PropagationJobError, match="launch_reconciliation_required"):
        owner.jobs.reconcile_applied(job["job_id"], "0" * 64)
    assert owner.service.status(job["job_id"])["state"] == "recovery_required"
    owner.supervisor.reconcile = owner.reconcile
    assert owner.service.status(job["job_id"])["state"] == "applied"
    final = owner.jobs.lookup_internal(job["job_id"])
    assert final["operation_id"] == operation and final["result"] == original["result"]
    assert all(final[key] == original[key] for key in ("pid", "start_ticks", "boot_id", "job_id"))
    with pytest.raises(PropagationJobError, match="launch_reconciliation_required"):
        owner.jobs.reconcile_applied(job["job_id"], original["result"]["result_digest"])


def test_superseded_historical_apply_cannot_receive_fresh_acceptance(tmp_path):
    from anvil_serving.control_plane.controller.propagation_store import PropagationIntentError
    owner = ControlledOwner(tmp_path)
    accepted, job, _ = owner.submit()
    value = {**owner.contract.value, "generation": 2, "revision": "revision-2"}
    value["effect_set_digest"] = effect_scope_digest(value)
    newer = parse_contract(value)
    owner.intents.admit(newer.canonical,
        approval_lookup=lambda ref: ApprovedAuthority(ref, DIGEST, newer.digest),
        active_identity=ActiveIdentity("activation-1", DIGEST), caller_id="admission", request_id="request-2", now=_now())
    with pytest.raises(PropagationIntentError, match="stale_generation"):
        owner.service.verify(accepted["intent_id"], job["job_id"])
    assert owner.reads == []


def test_new_generation_waits_for_durable_job_custody(tmp_path):
    from anvil_serving.control_plane.controller.propagation_store import PropagationIntentError
    owner = ControlledOwner(tmp_path)
    accepted = owner.accept()
    operation = _hash(["effect/v1", owner.contract.digest, "fleet", "apply"])
    owner.jobs.submit({"intent_id": accepted["intent_id"], "operation_id": operation,
        "canonical_contract": owner.contract.canonical, "preview_digest": DIGEST,
        "profile_id": "profile-1", "profile_digest": DIGEST, "resources": ["catalog-000"],
        "deadline": owner.contract.value["deadline_at"]})
    value = {**owner.contract.value, "generation": 2, "revision": "revision-2"}
    value["targets"] = [{**value["targets"][0], "resource_keys": ["different-client-catalog"]}]
    value["effect_set_digest"] = effect_scope_digest(value)
    newer = parse_contract(value)
    with pytest.raises(PropagationIntentError, match="resource_conflict"):
        owner.intents.admit(newer.canonical,
            approval_lookup=lambda ref: ApprovedAuthority(ref, DIGEST, newer.digest),
            active_identity=ActiveIdentity("activation-1", DIGEST), caller_id="admission",
            request_id="request-2", now=_now())
    owner.intents.assert_current(accepted["intent_id"])


def test_owner_refuses_split_admission_and_job_ledgers(tmp_path):
    owner = ControlledOwner(tmp_path)
    other = PropagationIntentStore(tmp_path / "separate.sqlite")
    with pytest.raises(ValueError, match="share one database"):
        PropagationService(other, owner.jobs, owner.supervisor, owner.profile)


def test_old_job_cannot_reserve_after_new_generation_admission(tmp_path):
    owner = ControlledOwner(tmp_path)
    accepted = owner.accept()
    value = {**owner.contract.value, "generation": 2, "revision": "revision-2"}
    value["effect_set_digest"] = effect_scope_digest(value)
    newer = parse_contract(value)
    owner.intents.admit(newer.canonical,
        approval_lookup=lambda ref: ApprovedAuthority(ref, DIGEST, newer.digest),
        active_identity=ActiveIdentity("activation-1", DIGEST), caller_id="admission",
        request_id="request-2", now=_now())
    with pytest.raises(PropagationJobError, match="stale_generation"):
        owner.jobs.submit({"intent_id": accepted["intent_id"],
            "operation_id": _hash(["effect/v1", owner.contract.digest, "fleet", "apply"]),
            "canonical_contract": owner.contract.canonical, "preview_digest": DIGEST,
            "profile_id": "profile-1", "profile_digest": DIGEST, "resources": ["catalog-000"],
            "deadline": owner.contract.value["deadline_at"]})
