import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from anvil_serving.observability.dashboard.access import Access
from anvil_serving.observability.dashboard.contracts import ObservatoryError
from anvil_serving.workbench_app.projects import Projects, run_bounded
from anvil_serving.workbench_app.store import PrivateStore
from anvil_serving.workbench_app.pi_sessions import PiTaskBinding
from anvil_serving.workbench_app.task_artifacts import TaskArtifacts, TaskArtifactSandbox
from test_playground import identity


@pytest.fixture
def projects(tmp_path):
    config = {"projects": [{"id": "product", "label": "Product", "resource_id": "serve-a", "checkout": str(tmp_path), "anvil_binary": "/usr/bin/anvil", "runner_root": str(tmp_path / "runners")}], "pi": {"id": "isolated"}}
    access = Access([], authenticate=lambda *_: False, origin="https://console.example.test", base_path="/", operate=True)
    state = {"status": "ready", "prd_status": "approved", "dependencies": [], "claims": [], "renew_failure": False}
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        verb = argv[1]
        if verb == "show":
            result = {"task": {"id": "feature:T001", "status": state["status"]}, "active_claims": state["claims"]}
        elif verb == "prd":
            result = {"prds": [{"id": "feature", "status": state["prd_status"]}]}
        elif verb == "packet":
            return b"Wrote packet to private storage\n" + json.dumps({"task_id": "feature:T001", "dependencies_open": state["dependencies"]}).encode()
        elif verb == "claim":
            raise AssertionError("Claim must not execute in read/gate tests")
        elif verb == "renew":
            if state["renew_failure"]:
                raise ObservatoryError("project_source_unavailable", "renewal failed", 409)
            result = {"renewed": False}
        else:
            result = {}
        return json.dumps({"ok": True, "data": result}).encode()
    store = PrivateStore(tmp_path / "private.sqlite")
    adapter = Projects(config, store, access, run=run)
    yield adapter, state, calls
    store.close()


@pytest.mark.parametrize("change", [{"prd_status": "draft"}, {"dependencies": ["feature:T000"]}, {"claims": [{"id": "other"}]}, {"status": "needs_review"}])
def test_task_gates_precede_any_claim_or_runner(projects, change):
    adapter, state, calls = projects
    state.update(change)
    body = {"project_id": "product", "task_id": "feature:T001", "request_id": "one", "provider_id": "cloud", "model_id": "specific", "thinking_level": "high"}
    with pytest.raises(ObservatoryError) as error:
        adapter.prepare(identity(actions=("project.execute",)), body)
    assert error.value.code == "task_not_ready"
    assert all(call[1] != "claim" for call in calls)


def test_browser_cannot_select_another_checkout(projects):
    adapter, _, calls = projects
    with pytest.raises(ObservatoryError):
        adapter.task(identity(), "unconfigured", "feature:T001")
    assert not calls
    detail = adapter.task(identity(), "product", "feature:T001")
    assert detail["execution"]["ready"]
    assert all("--cwd" in call for call in calls)


def test_lease_owner_and_expiry_are_revalidated(projects):
    import hashlib
    adapter, state, _ = projects
    actor = "workbench-" + hashlib.sha256(b"alice").hexdigest()[:16]
    binding = PiTaskBinding("alice", "product", "feature:T001", "claim-one", "isolated", "cloud")
    state["claims"] = [{"id": "claim-one", "claimed_by": actor, "lease_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()}]
    adapter.store.put("task-binding", "alice", "one", {"id": "one", "owner": "alice", "project_id": "product", "task_id": "feature:T001", "lease_id": "claim-one", "last_renew_attempt": time.time(), "provider_id": "cloud"})
    adapter.validate_pi_binding(binding)
    state["claims"][0]["claimed_by"] = "someone-else"
    with pytest.raises(ObservatoryError):
        adapter.validate_pi_binding(binding)
    state["claims"][0].update(claimed_by=actor, lease_expires_at="2020-01-01T00:00:00Z")
    with pytest.raises(ObservatoryError):
        adapter.validate_pi_binding(binding)


def test_renewal_records_noop_without_claiming_progress(projects):
    import hashlib
    adapter, state, calls = projects
    actor = "workbench-" + hashlib.sha256(b"alice").hexdigest()[:16]
    expiry = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    binding = PiTaskBinding("alice", "product", "feature:T001", "claim-one", "isolated", "cloud")
    state["claims"] = [{"id": "claim-one", "claimed_by": actor, "lease_expires_at": expiry}]
    adapter.store.put("task-binding", "alice", "one", {"id": "one", "owner": "alice", "project_id": "product", "task_id": "feature:T001", "lease_id": "claim-one", "last_renew_attempt": 0, "provider_id": "cloud"})
    adapter.validate_pi_binding(binding)
    row = adapter.store.get("task-binding", "alice", "one")
    assert row["lease_renewed"] is False
    assert row["lease_expires_at"] == expiry
    renew = next(call for call in calls if call[1] == "renew")
    assert renew[renew.index("--lease") + 1] == "240"


def test_renewal_failure_does_not_create_a_second_claim(projects):
    import hashlib
    adapter, state, calls = projects
    actor = "workbench-" + hashlib.sha256(b"alice").hexdigest()[:16]
    binding = PiTaskBinding("alice", "product", "feature:T001", "claim-one", "isolated", "cloud")
    state.update(claims=[{"id": "claim-one", "claimed_by": actor, "lease_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()}], renew_failure=True)
    adapter.store.put("task-binding", "alice", "one", {"id": "one", "owner": "alice", "project_id": "product", "task_id": "feature:T001", "lease_id": "claim-one", "last_renew_attempt": 0, "provider_id": "cloud"})
    with pytest.raises(ObservatoryError):
        adapter.validate_pi_binding(binding)
    assert [call[1] for call in calls].count("claim") == 0


def test_lost_claim_response_reconciles_and_releases_without_force(tmp_path):
    import hashlib
    config = {"projects": [{"id": "product", "label": "Product", "resource_id": "serve-a", "checkout": str(tmp_path), "anvil_binary": "/usr/bin/anvil", "runner_root": str(tmp_path / "runners")}], "pi": {"id": "isolated"}}
    access = Access([], authenticate=lambda *_: False, origin="https://console.example.test", base_path="/", operate=True)
    actor = "workbench-" + hashlib.sha256(b"alice").hexdigest()[:16]
    claimed = tmp_path / "claimed"
    claimed.mkdir()
    calls, claims = [], []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "git":
            assert "--force" not in argv
            return b""
        verb = argv[1]
        if verb == "show": data = {"task": {"id": "feature:T001", "status": "ready"}, "active_claims": claims}
        elif verb == "prd": data = {"prds": [{"id": "feature", "status": "approved"}]}
        elif verb == "packet": return b"note\n" + json.dumps({"task_id": "feature:T001", "dependencies_open": [], "task": {"likely_files": ["src/feature.py"], "verification": {"commands": ["python -m pytest"]}}}).encode()
        elif verb == "claim":
            claims.append({"id": "claim-one", "claimed_by": actor, "worktree_path": str(claimed)})
            raise ObservatoryError("project_source_unavailable", "claim response lost", 409)
        elif verb == "release":
            claims.clear()
            data = {"released": True}
        else: raise AssertionError(argv)
        return json.dumps({"ok": True, "data": data}).encode()
    store = PrivateStore(tmp_path / "private.sqlite")
    adapter = Projects(config, store, access, run=run, artifacts=TaskArtifacts(_Sandbox(), is_active=lambda _: False))
    try:
        body = {"project_id": "product", "task_id": "feature:T001", "request_id": "lost", "provider_id": "cloud", "model_id": "specific", "thinking_level": "high"}
        with pytest.raises(ObservatoryError, match="could not be prepared"):
            adapter.prepare(identity(actions=("project.execute",)), body)
        assert any(call[1] == "release" and "--force" not in call for call in calls if call[0] != "git")
        assert adapter.store.get("task-binding", "alice", "lost")["status"] == "released"
    finally:
        store.close()


class _Sandbox(TaskArtifactSandbox):
    def provision(self, source, destination, verification_destination):
        destination.mkdir(parents=True)
        verification_destination.mkdir(parents=True)
        return {"runner_checkout": str(destination), "verification_checkout": str(verification_destination), "baseline_sha": "a" * 40}

    def capture(self, source, baseline_sha):
        return {"baseline_sha": baseline_sha, "patch": b"diff --git a/src/feature.py b/src/feature.py\n--- a/src/feature.py\n+++ b/src/feature.py\n@@ -1 +1 @@\n-old\n+new\n", "files": [{"path": "src/feature.py", "status": "M", "mode": "100644"}]}

    def verify_transfer(self, source, verification_base, target, baseline_sha, patch, commands, *, already_transferred=False):
        assert source.name == "request-one" and verification_base.name == "request-one-verify"
        assert target.name == "claimed"
        return {"baseline_sha": baseline_sha, "verification_clean": True, "applied": True, "commands": [{"command": command, "exit_code": 0, "duration_seconds": 0.1, "stdout": "ok", "stderr": ""} for command in commands]}

    def transfer_state(self, verification_base, target, baseline_sha, patch):
        return "pristine"


def test_task_evidence_uses_sandbox_and_submits_actual_cli_evidence(tmp_path):
    config = {"projects": [{"id": "product", "label": "Product", "resource_id": "serve-a", "checkout": str(tmp_path), "anvil_binary": "/usr/bin/anvil", "runner_root": str(tmp_path / "runners")}], "pi": {"id": "isolated"}}
    access = Access([], authenticate=lambda *_: False, origin="https://console.example.test", base_path="/", operate=True)
    claim = tmp_path / "claimed"
    claim.mkdir()
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        verb = argv[1]
        if verb == "show": data = {"task": {"id": "feature:T001", "status": "ready"}, "active_claims": []}
        elif verb == "prd": data = {"prds": [{"id": "feature", "status": "approved"}]}
        elif verb == "packet": return b"note\n" + json.dumps({"task_id": "feature:T001", "dependencies_open": [], "task": {"likely_files": ["src/feature.py"], "verification": {"commands": ["python -m pytest"]}}}).encode()
        elif verb == "claim": data = {"claim": {"id": "claim-one", "lease_expires_at": "2099-01-01T00:00:00Z"}, "worktree": str(claim), "branch": "workbench/test"}
        elif verb == "submit": data = {"evidence_id": "EV001", "task": {"status": "needs_review"}}
        else: raise AssertionError(argv)
        return json.dumps({"ok": True, "data": data}).encode()
    store = PrivateStore(tmp_path / "private.sqlite")
    adapter = Projects(config, store, access, run=run, artifacts=TaskArtifacts(_Sandbox(), is_active=lambda _: False))
    try:
        body = {"project_id": "product", "task_id": "feature:T001", "request_id": "request-one", "provider_id": "cloud", "model_id": "specific", "thinking_level": "high"}
        row = adapter.prepare(identity(actions=("project.execute",)), body)
        preview = adapter.review_evidence(identity(actions=("project.execute",)), row["id"])
        verified = adapter.verify_evidence(identity(actions=("project.execute",)), row["id"], preview["artifact_digest"])
        submitted = adapter.submit_evidence(identity(actions=("project.execute",)), row["id"], preview["artifact_digest"])
        assert verified["passed"] is True and submitted["state"]["evidence_id"] == "EV001"
        submit = next(call for call in calls if call[1] == "submit")
        assert "--output-file" in submit and submit.count("--commands") == 1 and "src/feature.py" in submit
        assert not any(call and call[0] == "git" for call in calls)
    finally:
        store.close()


def test_submit_timeout_is_persisted_and_never_replayed(tmp_path):
    config = {"projects": [{"id": "product", "label": "Product", "resource_id": "serve-a", "checkout": str(tmp_path), "anvil_binary": "/usr/bin/anvil", "runner_root": str(tmp_path / "runners")}], "pi": {"id": "isolated"}}
    access = Access([], authenticate=lambda *_: False, origin="https://console.example.test", base_path="/", operate=True)
    root, digest = tmp_path / "evidence", "c" * 64
    root.mkdir()
    preview = {"artifact_digest": digest, "baseline_sha": "a" * 40, "packet_digest": "b" * 64, "files": [{"path": "src/feature.py", "status": "M", "mode": "100644"}]}
    (root / f"{digest}.json").write_text(json.dumps(preview), encoding="utf-8")
    (root / f"{digest}.verification.json").write_text(json.dumps(preview | {"commands": [], "passed": True}), encoding="utf-8")
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        raise ObservatoryError("project_source_unavailable", "submit outcome lost", 409)
    store = PrivateStore(tmp_path / "private.sqlite")
    adapter = Projects(config, store, access, run=run, artifacts=TaskArtifacts(_Sandbox(), is_active=lambda _: False))
    try:
        row = {"id": "one", "owner": "alice", "project_id": "product", "task_id": "feature:T001", "actor": "workbench-test", "lease_id": "claim-one", "provider_id": "cloud", "artifact_root": str(root), "artifact_digest": digest, "baseline_sha": "a" * 40, "packet_digest": "b" * 64, "verification_commands": ["python -m pytest"], "verification": {"artifact_digest": digest, "passed": True}}
        store.put("task-binding", "alice", "one", row)
        session = identity(actions=("project.execute",))
        with pytest.raises(ObservatoryError):
            adapter.submit_evidence(session, "one", digest)
        with pytest.raises(ObservatoryError, match="will not submit it twice"):
            adapter.submit_evidence(session, "one", digest)
        assert len(calls) == 1
        assert store.get("task-binding", "alice", "one")["submission"]["status"] == "outcome_unknown"
    finally:
        store.close()


def test_process_timeout_and_output_bounds(tmp_path):
    import sys
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        run_bounded([sys.executable, "-c", "import time; time.sleep(10)"], cwd=tmp_path, timeout=0.05)
    assert time.monotonic() - start < 2
    with pytest.raises(ValueError):
        run_bounded([sys.executable, "-c", "print('x'*10000)"], cwd=tmp_path, limit=100)


def test_plan_content_uses_supported_scoped_bounded_cli(projects):
    adapter, _, calls = projects
    original = adapter.run
    def run(argv, **kwargs):
        calls.append(argv)
        assert argv[1:5] == ["prd", "show", "feature", "--json"]
        assert argv[5:7] == ["--limit", "2097152"]
        return json.dumps({"ok": True, "data": {"schema_id": "anvil.state.prd-content.v1", "content": "# Plan", "source_digest": "a" * 64, "prd_revision": 2, "private_extra": "not-projected"}}).encode()
    adapter.run = run
    with pytest.raises(ObservatoryError):
        adapter.prd(identity(), "unconfigured", "feature")
    assert not calls
    result = adapter.prd(identity(), "product", "feature")
    assert result == {"content": "# Plan", "source_digest": "a" * 64, "prd_revision": 2}
    assert calls[0][-2:] == ["--cwd", adapter.config["projects"][0]["checkout"]]
    adapter.run = original
