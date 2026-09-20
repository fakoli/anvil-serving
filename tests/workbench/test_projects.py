import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from anvil_serving.observability.dashboard.access import Access
from anvil_serving.observability.dashboard.contracts import ObservatoryError, digest
from anvil_serving.workbench_app.projects import BoundedCommandFailure, Projects, run_bounded
from anvil_serving.workbench_app import projects as projects_module
from anvil_serving.workbench_app.store import PrivateStore
from anvil_serving.workbench_app.pi_sessions import PiTaskBinding
from anvil_serving.workbench_app.task_artifacts import TaskArtifacts, TaskArtifactSandbox
from test_playground import identity


_SAFE_TASK_ROOTS = os.name == "posix" and all(
    hasattr(os, name) for name in ("O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK")
)
requires_safe_task_roots = pytest.mark.skipif(
    not _SAFE_TASK_ROOTS,
    reason="requires safe POSIX task-root descriptors",
)


@pytest.fixture
def projects(tmp_path):
    config = {"projects": [{"id": "product", "label": "Product", "resource_id": "serve-a", "checkout": str(tmp_path), "anvil_binary": str(tmp_path / "anvil"), "runner_root": str(tmp_path / "runners")}], "pi": {"id": "isolated"}}
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
@requires_safe_task_roots
def test_task_gates_precede_any_claim_or_runner(projects, change):
    adapter, state, calls = projects
    state.update(change)
    body = {"project_id": "product", "task_id": "feature:T001", "request_id": "one", "provider_id": "cloud", "model_id": "specific", "thinking_level": "high"}
    with pytest.raises(ObservatoryError) as error:
        adapter.prepare(identity(actions=("project.execute",)), body)
    assert error.value.code == "task_not_ready"
    assert all(call[1] != "claim" for call in calls)


def test_prepare_refuses_missing_descriptors_before_claim_or_runner(projects, monkeypatch):
    adapter, _, calls = projects
    monkeypatch.delattr(projects_module.os, "O_DIRECTORY", raising=False)
    with pytest.raises(ObservatoryError) as error:
        adapter.prepare(identity(actions=("project.execute",)), {
            "project_id": "product", "task_id": "feature:T001", "request_id": "unsupported",
            "provider_id": "cloud", "model_id": "specific", "thinking_level": "high",
        })
    assert error.value.code == "project_root_unsupported"
    assert not calls
    assert not (Path(adapter.config["projects"][0]["runner_root"]) / ".workbench-context").exists()
    with pytest.raises(ObservatoryError) as missing:
        adapter.store.get("task-binding", "alice", "unsupported")
    assert missing.value.status == 404


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


def test_root_binding_digest_mismatch_refuses_live_pi_authority(projects):
    import hashlib
    adapter, state, _calls = projects
    actor = "workbench-" + hashlib.sha256(b"alice").hexdigest()[:16]
    binding = PiTaskBinding("alice", "product", "feature:T001", "claim-one", "isolated", "cloud", "a" * 64)
    state["claims"] = [{"id": "claim-one", "claimed_by": actor,
                        "lease_expires_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()}]
    adapter.store.put("task-binding", "alice", "roots", {"id": "roots", "owner": "alice", "project_id": "product", "task_id": "feature:T001", "lease_id": "claim-one", "provider_id": "cloud", "root_binding_digest": "b" * 64})
    with pytest.raises(ObservatoryError) as error:
        adapter.validate_pi_binding(binding)
    assert error.value.code == "task_root_binding_lost"


@requires_safe_task_roots
def test_lost_claim_response_reconciles_and_releases_without_force(tmp_path):
    import hashlib
    config = {"projects": [{"id": "product", "label": "Product", "resource_id": "serve-a", "checkout": str(tmp_path), "anvil_binary": str(tmp_path / "anvil"), "runner_root": str(tmp_path / "runners")}], "pi": {"id": "isolated"}}
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


class _GitCloneSandbox(TaskArtifactSandbox):
    """Disposable test-only sandbox that proves each owner worktree is cloned."""

    def provision(self, source, destination, verification_destination):
        for target in (destination, verification_destination):
            subprocess.run(
                ["git", "clone", "--no-local", str(source), str(target)],
                check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, text=True, timeout=15,
            )
        baseline = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"], check=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=15,
        ).stdout.strip()
        return {
            "runner_checkout": str(destination),
            "verification_checkout": str(verification_destination),
            "baseline_sha": baseline,
        }


def _git_repo(path):
    path.mkdir()
    for args in (
        ("init", "-q"), ("config", "user.email", "fixture@example.test"),
        ("config", "user.name", "Fixture"),
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=15)
    (path / "README.md").write_text(path.name + "\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=15)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "initial"], check=True,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=15)


def _candidate_anvil(binary, cwd, *args):
    result = subprocess.run([str(binary), *args], cwd=cwd, check=False,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["data"]


class _PerRootArtifactSandbox(TaskArtifactSandbox):
    def __init__(self):
        self.transferred, self.calls = set(), []
        self.fail_second = True
        self.changed = set()

    def provision(self, source, destination, verification_destination):
        raise AssertionError("not used")

    def capture(self, source, baseline_sha):
        root_id = source.name
        patch = f"diff --git a/shared.py b/shared.py\n--- a/shared.py\n+++ b/shared.py\n+{root_id}\n".encode()
        return {"baseline_sha": baseline_sha, "patch": patch,
                "files": [{"path": "shared.py", "status": "M", "mode": "100644"}]}

    def verify_transfer(self, source, verification_base, target, baseline_sha, patch, commands, *, already_transferred=False):
        root_id = source.name
        self.calls.append((root_id, already_transferred, tuple(commands)))
        if root_id in self.changed:
            raise ObservatoryError("transfer_refused", "secondary changed after review", 409)
        if root_id == "library" and self.fail_second:
            self.fail_second = False
            raise ObservatoryError("transfer_refused", "second-root transfer failed", 409)
        self.transferred.add(root_id)
        return {"baseline_sha": baseline_sha, "verification_clean": True, "applied": True,
                "commands": [{"command": command, "exit_code": 0, "duration_seconds": 0.01,
                              "stdout": root_id, "stderr": ""} for command in commands]}

    def transfer_state(self, verification_base, target, baseline_sha, patch):
        return "exact" if verification_base.name.replace("-verify", "") in self.transferred else "pristine"


def _per_root_artifact_adapter(tmp_path, sandbox):
    access = Access([], authenticate=lambda *_: False, origin="https://console.example.test", base_path="/", operate=True)
    config = {"projects": [{"id": "product", "label": "Product", "resource_id": "serve-a", "checkout": str(tmp_path),
                              "anvil_binary": str(tmp_path / "anvil"), "runner_root": str(tmp_path / "runners")}], "pi": {"id": "isolated"}}
    store = PrivateStore(tmp_path / "private.sqlite")
    root_binding = {"version": 1, "primary_root_id": "app", "claim_root_id": "app", "cwd_root_id": "app",
                    "multi_root": True, "roots": [{"id": "app", "mount": "workspace"}, {"id": "library", "mount": "workspace"}]}
    roots = []
    for root_id, baseline, commands in (("app", "a" * 40, ["pytest app"]), ("library", "b" * 40, ["pytest library"])):
        runner, verify, claim, evidence = (tmp_path / root_id, tmp_path / f"{root_id}-verify", tmp_path / f"{root_id}-claim", tmp_path / "evidence" / root_id)
        for path in (runner, verify, claim, evidence):
            path.mkdir(parents=True, exist_ok=True)
        roots.append({"root_id": root_id, "repository_id": root_id, "baseline_sha": baseline,
                      "canonical_root": str(tmp_path / f"{root_id}-source"), "claim_worktree": str(claim), "branch": "agent/test",
                      "runner_checkout": str(runner), "verification_checkout": str(verify), "artifact_root": str(evidence),
                      "verification_commands": commands, "declared_paths": ["shared.py"], "packet_digest": "c" * 64})
    row = {"id": "root-evidence", "owner": "alice", "project_id": "product", "task_id": "feature:T001", "actor": "workbench-owner",
           "lease_id": "claim-one", "provider_id": "cloud", "status": "ready", "packet_digest": "c" * 64,
           "root_binding": root_binding, "root_bindings": roots}
    row["root_binding_digest"] = digest({"binding": root_binding, "roots": roots})
    store.put("task-binding", "alice", row["id"], row)
    def runner_active(binding):
        assert binding["owner"] == "alice" and binding["lease_id"] == "claim-one"
        assert binding["project_id"] == "product" and binding["task_id"] == "feature:T001"
        return False
    return Projects(config, store, access, artifacts=TaskArtifacts(sandbox, is_active=runner_active)), store, row


def _multi_root_adapter(tmp_path, *, roots=None, primary_root_id="state"):
    checkout, secondary, claim = tmp_path / "checkout", tmp_path / "secondary", tmp_path / "claim"
    for path in (checkout, secondary, claim):
        path.mkdir()
    config = {"projects": [{"id": "product", "label": "Product", "resource_id": "serve-a",
                             "checkout": str(checkout), "anvil_binary": str(tmp_path / "anvil"),
                             "runner_root": str(tmp_path / "runners"), "primary_root_id": primary_root_id,
                             "roots": roots or [
                                 {"id": "state", "label": "State", "owner_id": "local-owner", "runtime_id": "local-runtime", "task_access": "read-write", "path": str(checkout)},
                                 {"id": "docs", "label": "Docs", "owner_id": "local-owner", "runtime_id": "local-runtime", "task_access": "read-only", "path": str(secondary)},
                             ]}], "pi": {"id": "isolated"}}
    access = Access([], authenticate=lambda *_: False, origin="https://console.example.test", base_path="/", operate=True)
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        verb = argv[1]
        if verb == "show": data = {"task": {"id": "feature:T001", "status": "ready"}, "active_claims": []}
        elif verb == "prd": data = {"prds": [{"id": "feature", "status": "approved"}]}
        elif verb == "packet": return b"packet\n" + json.dumps({"task_id": "feature:T001", "dependencies_open": [], "task": {"likely_files": ["src/feature.py"], "verification": {"commands": ["python -m pytest"]}}}).encode()
        elif verb == "claim": data = {"claim": {"id": "claim-one", "lease_expires_at": "2099-01-01T00:00:00Z"}, "worktree": str(claim), "branch": "workbench/test"}
        else: raise AssertionError(argv)
        return json.dumps({"ok": True, "data": data}).encode()

    store = PrivateStore(tmp_path / "private.sqlite")
    return Projects(config, store, access, run=run, artifacts=TaskArtifacts(_Sandbox(), is_active=lambda _: False)), store, checkout, secondary, calls


@requires_safe_task_roots
def test_prepare_acquires_two_owner_roots_and_clones_them_with_read_only_context(tmp_path, monkeypatch):
    """Exercise the candidate owner CLI, not a mocked root-set response."""
    candidate = Path(os.environ.get("ANVIL_TEST_BINARY", ""))
    if not candidate.is_file():
        pytest.skip("ANVIL_TEST_BINARY is unavailable")
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    app, library, docs = (tmp_path / "app", tmp_path / "library", tmp_path / "docs")
    for root in (app, library, docs):
        _git_repo(root)
    (docs / "guide.md").write_text("read-only context\n", encoding="utf-8")
    (docs / ".env").write_text("never copied\n", encoding="utf-8")

    initialized = subprocess.run([str(candidate), "init", "--with-sample"], cwd=app,
                                 check=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, timeout=20)
    assert initialized.returncode == 0, initialized.stderr
    task = _candidate_anvil(candidate, app, "next", "--cwd", str(app), "--json")["task"]
    primary_commands = task["verification"]["commands"]
    library_commands = ["python -m pytest -q"]
    _candidate_anvil(candidate, app, "roots", "enroll", "--repository-id", "app",
                     "--path", str(app), "--origin", "local:app", "--verification-command",
                     primary_commands[0], "--json")
    _candidate_anvil(candidate, app, "roots", "enroll", "--repository-id", "library",
                     "--path", str(library), "--origin", "local:library", "--verification-command",
                     library_commands[0], "--json")

    config = {"projects": [{
        "id": "product", "label": "Product", "resource_id": "serve-a",
        "checkout": str(app), "anvil_binary": str(candidate), "runner_root": str(tmp_path / "runners"),
        "primary_root_id": "app", "roots": [
            {"id": "app", "label": "App", "owner_id": "local-owner", "runtime_id": "local-runtime",
             "task_access": "read-write", "path": str(app), "repository_id": "app",
             "verification_commands": primary_commands},
            {"id": "library", "label": "Library", "owner_id": "local-owner", "runtime_id": "local-runtime",
             "task_access": "read-write", "path": str(library), "repository_id": "library",
             "verification_commands": library_commands, "expected_files": ["README.md", "shared.py"]},
            {"id": "docs", "label": "Docs", "owner_id": "local-owner", "runtime_id": "local-runtime",
             "task_access": "read-only", "path": str(docs)},
        ],
    }], "pi": {"id": "isolated"}}
    access = Access([], authenticate=lambda *_: False, origin="https://console.example.test", base_path="/", operate=True)
    store = PrivateStore(tmp_path / "private.sqlite")
    adapter = Projects(config, store, access, artifacts=TaskArtifacts(_GitCloneSandbox(), is_active=lambda _: False))
    body = {"project_id": "product", "task_id": task["id"], "request_id": "two-root-acquire",
            "provider_id": "cloud", "model_id": "specific", "thinking_level": "high",
            "writable_root_ids": ["library"]}
    try:
        row = adapter.prepare(identity(actions=("project.execute",)), body)
        roots = {item["root_id"]: item for item in row["root_bindings"]}
        assert set(roots) == {"app", "library"}
        assert roots["app"]["verification_commands"] == primary_commands
        assert roots["library"]["verification_commands"] == library_commands
        for root_id, item in roots.items():
            checkout = Path(item["runner_checkout"])
            assert checkout.is_dir() and (checkout / ".git").is_dir()
            assert checkout.parent == Path(config["projects"][0]["runner_root"]) / root_id
            assert subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"], check=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15).stdout.strip() == item["baseline_sha"]
        frozen_docs = next(item for item in row["root_binding"]["roots"] if item["id"] == "docs")
        assert frozen_docs["mount"] == "context-read-only"
        snapshot = Path(frozen_docs["context_snapshot"]["path"])
        assert snapshot != docs and (snapshot / "guide.md").read_text(encoding="utf-8") == "read-only context\n"
        assert not (snapshot / ".env").exists() and not (snapshot / ".git").exists()
        assert row["root_claim"]["request_digest"] == row["root_request"]["request_digest"]
        assert row["baseline_sha"] == roots["app"]["baseline_sha"]
        request = json.loads(Path(row["root_request"]["path"]).read_text(encoding="utf-8"))
        primary = next(item for item in request["roots"] if item["root_id"] == "app")
        assert primary["expected_files"] == task["likely_files"]
    finally:
        store.close()


@requires_safe_task_roots
def test_prepare_reconciles_lost_owner_claim_response_without_a_second_claim(tmp_path, monkeypatch):
    candidate = Path(os.environ.get("ANVIL_TEST_BINARY", ""))
    if not candidate.is_file():
        pytest.skip("ANVIL_TEST_BINARY is unavailable")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    app, library, docs = (tmp_path / "app", tmp_path / "library", tmp_path / "docs")
    for root in (app, library, docs):
        _git_repo(root)
    assert subprocess.run([str(candidate), "init", "--with-sample"], cwd=app, check=False,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20).returncode == 0
    task = _candidate_anvil(candidate, app, "next", "--cwd", str(app), "--json")["task"]
    primary_commands, library_commands = task["verification"]["commands"], ["python -m pytest -q"]
    for repository_id, root, commands in (("app", app, primary_commands), ("library", library, library_commands)):
        _candidate_anvil(candidate, app, "roots", "enroll", "--repository-id", repository_id, "--path", str(root),
                         "--origin", "local:" + repository_id, "--verification-command", commands[0], "--json")
    config = {"projects": [{"id": "product", "label": "Product", "resource_id": "serve-a", "checkout": str(app),
                              "anvil_binary": str(candidate), "runner_root": str(tmp_path / "runners"), "primary_root_id": "app", "roots": [
        {"id": "app", "label": "App", "owner_id": "local-owner", "runtime_id": "local-runtime", "task_access": "read-write", "path": str(app), "repository_id": "app", "verification_commands": primary_commands},
        {"id": "library", "label": "Library", "owner_id": "local-owner", "runtime_id": "local-runtime", "task_access": "read-write", "path": str(library), "repository_id": "library", "verification_commands": library_commands, "expected_files": ["README.md", "shared.py"]},
        {"id": "docs", "label": "Docs", "owner_id": "local-owner", "runtime_id": "local-runtime", "task_access": "read-only", "path": str(docs)},
    ]}], "pi": {"id": "isolated"}}
    store = PrivateStore(tmp_path / "private.sqlite")
    access = Access([], authenticate=lambda *_: False, origin="https://console.example.test", base_path="/", operate=True)
    calls = []

    def lose_claim_response(argv, **kwargs):
        result = run_bounded(argv, **kwargs)
        if argv[1:3] == ["roots", "claim"]:
            calls.append(tuple(argv))
            raise BoundedCommandFailure(result)
        return result

    adapter = Projects(config, store, access, run=lose_claim_response,
                       artifacts=TaskArtifacts(_GitCloneSandbox(), is_active=lambda _: False))
    body = {"project_id": "product", "task_id": task["id"], "request_id": "lost-owner-response",
            "provider_id": "cloud", "model_id": "specific", "thinking_level": "high", "writable_root_ids": ["library"]}
    try:
        with pytest.raises(ObservatoryError, match="could not be prepared"):
            adapter.prepare(identity(actions=("project.execute",)), body)
        retained = store.get("task-binding", "alice", body["request_id"])
        assert retained["status"] == "provisioning" and retained["root_request"]["request_digest"]
        assert len(calls) == 1
        adapter.run = run_bounded
        recovered = adapter.prepare(identity(actions=("project.execute",)), body)
        assert recovered["status"] == "ready"
        assert recovered["root_claim"]["claim_id"] == recovered["lease_id"]
        assert {item["root_id"] for item in recovered["root_bindings"]} == {"app", "library"}
        assert len(calls) == 1
    finally:
        store.close()


def _candidate_two_root_adapter(tmp_path, monkeypatch, sandbox):
    candidate = Path(os.environ.get("ANVIL_TEST_BINARY", ""))
    if not candidate.is_file():
        pytest.skip("ANVIL_TEST_BINARY is unavailable")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    app, library, docs = (tmp_path / "app", tmp_path / "library", tmp_path / "docs")
    for root in (app, library, docs):
        _git_repo(root)
    initialized = subprocess.run([str(candidate), "init", "--with-sample"], cwd=app, check=False,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, timeout=20)
    assert initialized.returncode == 0, initialized.stderr
    task = _candidate_anvil(candidate, app, "next", "--cwd", str(app), "--json")["task"]
    primary_commands, library_commands = task["verification"]["commands"], ["python -m pytest -q"]
    for repository_id, root, commands in (("app", app, primary_commands), ("library", library, library_commands)):
        _candidate_anvil(candidate, app, "roots", "enroll", "--repository-id", repository_id, "--path", str(root),
                         "--origin", "local:" + repository_id, "--verification-command", commands[0], "--json")
    config = {"projects": [{"id": "product", "label": "Product", "resource_id": "serve-a", "checkout": str(app),
                              "anvil_binary": str(candidate), "runner_root": str(tmp_path / "runners"), "primary_root_id": "app", "roots": [
        {"id": "app", "label": "App", "owner_id": "local-owner", "runtime_id": "local-runtime", "task_access": "read-write", "path": str(app), "repository_id": "app", "verification_commands": primary_commands},
        {"id": "library", "label": "Library", "owner_id": "local-owner", "runtime_id": "local-runtime", "task_access": "read-write", "path": str(library), "repository_id": "library", "verification_commands": library_commands, "expected_files": ["README.md", "shared.py"]},
        {"id": "docs", "label": "Docs", "owner_id": "local-owner", "runtime_id": "local-runtime", "task_access": "read-only", "path": str(docs)},
    ]}], "pi": {"id": "isolated"}}
    store = PrivateStore(tmp_path / "private.sqlite")
    access = Access([], authenticate=lambda *_: False, origin="https://console.example.test", base_path="/", operate=True)
    adapter = Projects(config, store, access, artifacts=TaskArtifacts(sandbox, is_active=lambda _: False))
    body = {"project_id": "product", "task_id": task["id"], "request_id": "recover-two-roots",
            "provider_id": "cloud", "model_id": "specific", "thinking_level": "high", "writable_root_ids": ["library"]}
    return adapter, store, body


class _FailSecondCloneSandbox(_GitCloneSandbox):
    def __init__(self):
        self.calls = 0

    def provision(self, source, destination, verification_destination):
        self.calls += 1
        if self.calls == 2:
            raise ObservatoryError("fixture_clone_failure", "fixture second clone failure", 409)
        return super().provision(source, destination, verification_destination)


@requires_safe_task_roots
def test_prepare_recovers_partial_second_root_clone_from_retained_owner_facts(tmp_path, monkeypatch):
    sandbox = _FailSecondCloneSandbox()
    adapter, store, body = _candidate_two_root_adapter(tmp_path, monkeypatch, sandbox)
    try:
        with pytest.raises(ObservatoryError, match="could not be prepared"):
            adapter.prepare(identity(actions=("project.execute",)), body)
        retained = store.get("task-binding", "alice", body["request_id"])
        assert retained["status"] == "provisioning"
        first, second = retained["root_bindings"]
        assert Path(first["runner_checkout"]).is_dir() and "runner_checkout" not in second
        recovered = adapter.prepare(identity(actions=("project.execute",)), body)
        assert recovered["status"] == "ready" and sandbox.calls == 3
        assert all(Path(item["runner_checkout"]).is_dir() for item in recovered["root_bindings"])
    finally:
        store.close()


@requires_safe_task_roots
def test_release_confirms_root_set_runner_stop_before_freeing_reservation(tmp_path, monkeypatch):
    adapter, store, body = _candidate_two_root_adapter(tmp_path, monkeypatch, _GitCloneSandbox())
    try:
        row = adapter.prepare(identity(actions=("project.execute",)), body)
        released = adapter.release(identity(actions=("project.execute",)), row["id"])
        assert released["status"] == "released"
        owner_state = adapter.cli(adapter.configured("product"), "roots", "status", "--request-id", row["root_request"]["request_id"],
                                  "--request-digest", row["root_request"]["request_digest"], "--actor", row["actor"], "--json")
        assert owner_state["state"] == "released" and owner_state["claim_id"] == row["lease_id"]
    finally:
        store.close()


@requires_safe_task_roots
def test_pi_authority_refuses_changed_owner_root_facts_and_expired_lease(tmp_path, monkeypatch):
    adapter, store, body = _candidate_two_root_adapter(tmp_path, monkeypatch, _GitCloneSandbox())
    try:
        row = adapter.prepare(identity(actions=("project.execute",)), body)
        original_run = adapter.run

        def changed_root_fact(argv, **kwargs):
            raw = original_run(argv, **kwargs)
            if argv[1:3] == ["roots", "reconcile"]:
                payload = json.loads(raw)
                payload["data"]["roots"][0]["baseline_sha"] = "f" * 40
                return json.dumps(payload).encode("utf-8")
            return raw

        adapter.run = changed_root_fact
        with pytest.raises(ObservatoryError) as changed:
            adapter.validate_pi_binding(adapter.pi_binding(row))
        assert changed.value.code == "task_root_binding_lost"

        adapter.run = original_run
        row = adapter.prepare(identity(actions=("project.execute",)), body)

        def expired_claim(argv, **kwargs):
            raw = original_run(argv, **kwargs)
            if argv[1] == "show":
                payload = json.loads(raw)
                for claim in payload["data"].get("active_claims", []):
                    if claim.get("id") == row["lease_id"]:
                        claim["lease_expires_at"] = "2020-01-01T00:00:00Z"
                return json.dumps(payload).encode("utf-8")
            return raw

        adapter.run = expired_claim
        with pytest.raises(ObservatoryError) as expired:
            adapter.validate_pi_binding(adapter.pi_binding(row))
        assert expired.value.code == "lease_expired"
    finally:
        store.close()


@requires_safe_task_roots
def test_root_request_files_are_owner_scoped_immutable_and_not_browser_named(tmp_path):
    adapter, store, checkout, _secondary, _calls = _multi_root_adapter(tmp_path)
    try:
        adapter.config["projects"][0]["roots"][0]["repository_id"] = "state"
        binding = adapter._freeze_task_roots(adapter.config["projects"][0], {})
        first = {
            "id": "same-browser-start", "owner": "alice", "project_id": "product", "task_id": "feature:T001",
            "root_binding": binding, "task_verification_commands": ["pytest"], "task_declared_paths": ["src/a.py"],
        }
        second = first | {"owner": "bob", "task_declared_paths": ["src/b.py"]}
        first_path = adapter._root_request(first, adapter.config["projects"][0]["runner_root"])
        second_path = adapter._root_request(second, adapter.config["projects"][0]["runner_root"])
        assert first_path != second_path and first_path.name != "same-browser-start.json"
        assert json.loads(first_path.read_text(encoding="utf-8"))["roots"][0]["expected_files"] == ["src/a.py"]
        conflicting = first | {"task_declared_paths": ["src/changed.py"]}
        with pytest.raises(ObservatoryError) as error:
            adapter._root_request(conflicting, adapter.config["projects"][0]["runner_root"])
        assert error.value.code == "task_root_binding_lost"
    finally:
        store.close()


def test_release_cancels_only_owner_proven_prelog_root_request(tmp_path):
    adapter, store, checkout, _secondary, _calls = _multi_root_adapter(tmp_path)
    request_path = tmp_path / "retained-request.json"
    request_path.write_text("{}", encoding="utf-8")
    calls = []

    def owner_cli(_project, *args):
        calls.append(args)
        if args[0] == "show":
            return {"active_claims": []}
        if args[:2] == ("roots", "request-digest"):
            return {"request_id": "prelog", "request_digest": "a" * 64}
        if args[:2] == ("roots", "reconcile"):
            assert "--cancel-if-no-claim" in args and "--confirm-runner-stopped" not in args
            return {"state": "released"}
        raise AssertionError(args)

    adapter.cli = owner_cli
    row = {"id": "prelog", "owner": "alice", "project_id": "product", "task_id": "feature:T001",
           "actor": "workbench-owner", "root_request": {"path": str(request_path), "request_id": "prelog", "request_digest": "a" * 64}}
    try:
        adapter._release_preparation(adapter.configured("product"), row)
        assert row["status"] == "released"
        assert any(args[:2] == ("roots", "reconcile") for args in calls)
    finally:
        store.close()


def test_per_root_review_and_transfer_retains_partial_progress_without_replay(tmp_path):
    sandbox = _PerRootArtifactSandbox()
    adapter, store, row = _per_root_artifact_adapter(tmp_path, sandbox)
    try:
        review = adapter.review_evidence(identity(actions=("project.execute",)), row["id"])
        assert review["rootset_digest"] == row["root_binding_digest"]
        assert [item["root_id"] for item in review["roots"]] == ["app", "library"]
        assert all(item["files"] == [{"path": "shared.py", "status": "M", "mode": "100644"}] for item in review["roots"])
        with pytest.raises(ObservatoryError, match="second-root transfer failed"):
            adapter.verify_evidence(identity(actions=("project.execute",)), row["id"], review["manifest_digest"])
        retained = store.get("task-binding", "alice", row["id"])
        assert retained["root_transfer"]["app"]["state"] == "transferred"
        assert retained["root_transfer"]["library"]["state"] == "intent"
        recovered = adapter.verify_evidence(identity(actions=("project.execute",)), row["id"], review["manifest_digest"])
        assert recovered["passed"] and recovered["transferred"] and not recovered["partial_transfer"]
        assert [item for item in sandbox.calls if item[0] == "app"] == [("app", False, ("pytest app",))]
        assert [item for item in sandbox.calls if item[0] == "library"] == [("library", False, ("pytest library",)), ("library", False, ("pytest library",))]
        evidence = adapter.evidence(identity(), row["id"])
        assert evidence["verification"]["manifest_digest"] == review["manifest_digest"]
        with pytest.raises(ObservatoryError, match="original root-set request"):
            adapter.submit_evidence(identity(actions=("project.execute",)), row["id"], review["manifest_digest"])
    finally:
        store.close()


def test_per_root_transfer_refuses_changed_secondary_after_review(tmp_path):
    sandbox = _PerRootArtifactSandbox()
    sandbox.fail_second = False
    adapter, store, row = _per_root_artifact_adapter(tmp_path, sandbox)
    try:
        review = adapter.review_evidence(identity(actions=("project.execute",)), row["id"])
        sandbox.changed.add("library")
        with pytest.raises(ObservatoryError, match="secondary changed after review"):
            adapter.verify_evidence(identity(actions=("project.execute",)), row["id"], review["manifest_digest"])
        retained = store.get("task-binding", "alice", row["id"])
        assert retained["root_transfer"]["app"]["state"] == "transferred"
        assert retained["root_transfer"]["library"]["state"] == "intent"
        assert retained["status"] == "transfer_recovery_required"
    finally:
        store.close()


def test_root_submission_rereads_reviewed_artifacts_before_owner_manifest(tmp_path):
    """Private-store edits or missing retained patches cannot mint owner evidence."""
    sandbox = _PerRootArtifactSandbox()
    sandbox.fail_second = False
    adapter, store, row = _per_root_artifact_adapter(tmp_path, sandbox)
    try:
        review = adapter.review_evidence(identity(actions=("project.execute",)), row["id"])
        adapter.verify_evidence(identity(actions=("project.execute",)), row["id"], review["manifest_digest"])
        retained = store.get("task-binding", "alice", row["id"])
        retained["root_review"]["roots"][0]["files"] = [{"path": "forged.py"}]
        with pytest.raises(ObservatoryError) as forged:
            adapter._root_submission_material(retained, review["manifest_digest"])
        assert forged.value.code == "evidence_unverified"
        retained = store.get("task-binding", "alice", row["id"])
        app_root = next(item for item in retained["root_bindings"] if item["root_id"] == "app")
        artifact_digest = next(
            item["evidence"]["artifact_digest"]
            for item in retained["root_verification"]["roots"]
            if item["root_id"] == "app"
        )
        (Path(app_root["artifact_root"]) / (artifact_digest + ".patch")).unlink()
        with pytest.raises(ObservatoryError) as deleted:
            adapter._root_submission_material(retained, review["manifest_digest"])
        assert deleted.value.code == "evidence_tampered"
    finally:
        store.close()


class _CandidateEvidenceSandbox(_GitCloneSandbox):
    """Disposable bounded sandbox fixture for owner-backed per-root submission."""

    def capture(self, source, baseline_sha):
        patch = (
            b"diff --git a/src/mdlinks/extract.py b/src/mdlinks/extract.py\n"
            b"--- a/src/mdlinks/extract.py\n+++ b/src/mdlinks/extract.py\n@@ -1 +1 @@\n-old\n+new\n"
        )
        return {
            "baseline_sha": baseline_sha,
            "patch": patch,
            "files": [{"path": "src/mdlinks/extract.py", "status": "M", "mode": "100644"}],
        }

    def verify_transfer(self, source, verification_base, target, baseline_sha, patch, commands, *, already_transferred=False):
        assert source.is_dir() and verification_base.is_dir() and target.is_dir()
        return {
            "baseline_sha": baseline_sha,
            "verification_clean": True,
            "applied": True,
            "commands": [
                {"command": command, "exit_code": 0, "duration_seconds": 0.01,
                 "stdout": "ok", "stderr": ""}
                for command in commands
            ],
        }

    def transfer_state(self, verification_base, target, baseline_sha, patch):
        return "pristine"


class _CandidateUnchangedSecondarySandbox(_CandidateEvidenceSandbox):
    """The secondary verifier accepts its exact baseline without a transfer."""

    def capture(self, source, baseline_sha):
        if "library" in source.parts:
            return {"baseline_sha": baseline_sha, "patch": b"", "files": []}
        return super().capture(source, baseline_sha)

    def verify_transfer(self, source, verification_base, target, baseline_sha, patch, commands, *, already_transferred=False):
        result = super().verify_transfer(
            source, verification_base, target, baseline_sha, patch, commands,
            already_transferred=already_transferred,
        )
        if not patch:
            # ``applied`` means the exact frozen target was accepted; no patch
            # bytes are written for the unchanged root.
            assert not already_transferred
        return result


@requires_safe_task_roots
def test_prepare_verify_and_submit_two_root_evidence_through_owner_cli(tmp_path, monkeypatch):
    """A real owner claim records two same-named files as distinct root facts."""
    adapter, store, body = _candidate_two_root_adapter(
        tmp_path, monkeypatch, _CandidateEvidenceSandbox()
    )
    try:
        for root in adapter.config["projects"][0]["roots"]:
            if root["id"] == "library":
                root["expected_files"] = ["src/mdlinks/extract.py"]
        row = adapter.prepare(identity(actions=("project.execute",)), body)
        review = adapter.review_evidence(identity(actions=("project.execute",)), row["id"])
        verified = adapter.verify_evidence(
            identity(actions=("project.execute",)), row["id"], review["manifest_digest"]
        )
        assert verified["passed"] and verified["transferred"]
        submitted = adapter.submit_evidence(
            identity(actions=("project.execute",)), row["id"], review["manifest_digest"]
        )
        assert submitted["status"] == "submitted"
        retained = store.get("task-binding", "alice", row["id"])
        assert retained["status"] == "submitted"
        owner = adapter.cli(
            adapter.configured("product"), "roots", "evidence-status", row["task_id"],
            "--request-file", row["root_request"]["path"],
            "--manifest-file", str(
                Path(adapter.configured("product")["runner_root"])
                / ".workbench-root-evidence"
                / (digest({"owner": row["owner"], "project_id": row["project_id"], "submission_id": submitted["submission_id"]}) + ".json")
            ),
            "--actor", row["actor"], "--json",
        )
        assert owner["status"] == "submitted"
        owner_manifest = json.loads(
            next(
                (Path(adapter.configured("product")["runner_root"]) / ".workbench-root-evidence").glob("*.json")
            ).read_text(encoding="utf-8")
        )
        assert [item["root_id"] for item in owner_manifest["roots"]] == ["app", "library"]
        assert [item["files"] for item in owner_manifest["roots"]] == [
            ["src/mdlinks/extract.py"], ["src/mdlinks/extract.py"],
        ]
    finally:
        store.close()


@requires_safe_task_roots
def test_owner_submission_retains_an_unchanged_secondary_root(tmp_path, monkeypatch):
    adapter, store, body = _candidate_two_root_adapter(
        tmp_path, monkeypatch, _CandidateUnchangedSecondarySandbox()
    )
    try:
        for root in adapter.config["projects"][0]["roots"]:
            if root["id"] == "library":
                root["expected_files"] = ["src/mdlinks/extract.py"]
        row = adapter.prepare(identity(actions=("project.execute",)), body)
        review = adapter.review_evidence(identity(actions=("project.execute",)), row["id"])
        unchanged = next(item for item in review["roots"] if item["root_id"] == "library")
        assert unchanged["files"] == []
        assert unchanged["artifact_digest"] == hashlib.sha256(b"").hexdigest()
        adapter.verify_evidence(identity(actions=("project.execute",)), row["id"], review["manifest_digest"])
        submitted = adapter.submit_evidence(identity(actions=("project.execute",)), row["id"], review["manifest_digest"])
        assert submitted["status"] == "submitted"
        owner_manifest = json.loads(next(
            (Path(adapter.configured("product")["runner_root"]) / ".workbench-root-evidence").glob("*.json")
        ).read_text(encoding="utf-8"))
        secondary = next(item for item in owner_manifest["roots"] if item["root_id"] == "library")
        assert secondary["files"] == []
        assert secondary["artifact_digest"] == hashlib.sha256(b"").hexdigest()
    finally:
        store.close()


@requires_safe_task_roots
def test_root_submission_retries_only_release_reconciliation_after_owner_failure(tmp_path, monkeypatch):
    adapter, store, body = _candidate_two_root_adapter(
        tmp_path, monkeypatch, _CandidateEvidenceSandbox()
    )
    try:
        for root in adapter.config["projects"][0]["roots"]:
            if root["id"] == "library":
                root["expected_files"] = ["src/mdlinks/extract.py"]
        row = adapter.prepare(identity(actions=("project.execute",)), body)
        review = adapter.review_evidence(identity(actions=("project.execute",)), row["id"])
        adapter.verify_evidence(identity(actions=("project.execute",)), row["id"], review["manifest_digest"])
        original = adapter.cli
        calls, failed = [], [False]

        def fail_first_reconcile(project, *args):
            calls.append(args)
            if args[:2] == ("roots", "reconcile") and not failed[0]:
                failed[0] = True
                raise ObservatoryError("owner_unavailable", "owner reconciliation interrupted", 409)
            return original(project, *args)

        adapter.cli = fail_first_reconcile
        with pytest.raises(ObservatoryError, match="owner reconciliation interrupted"):
            adapter.submit_evidence(identity(actions=("project.execute",)), row["id"], review["manifest_digest"])
        retained = store.get("task-binding", "alice", row["id"])
        assert retained["status"] == "submitted_release_pending"
        assert retained["submission"]["status"] == "submitted"
        recovered = adapter.submit_evidence(
            identity(actions=("project.execute",)), row["id"], review["manifest_digest"]
        )
        assert recovered["status"] == "submitted"
        assert store.get("task-binding", "alice", row["id"])["status"] == "submitted"
        assert sum(args[:2] == ("roots", "submit-evidence") for args in calls) == 1
        assert sum(args[:2] == ("roots", "reconcile") for args in calls) == 2
    finally:
        store.close()


@requires_safe_task_roots
def test_prepare_freezes_primary_and_private_read_only_context_snapshot(tmp_path):
    adapter, store, _checkout, secondary, _calls = _multi_root_adapter(tmp_path)
    (secondary / "safe.py").write_text("value = 1\n", encoding="utf-8")
    (secondary / ".env").write_text("not mounted", encoding="utf-8")
    (secondary / ".git").mkdir()
    (secondary / ".git" / "config").write_text("not mounted", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.py").write_text("not mounted", encoding="utf-8")
    (secondary / "escape").symlink_to(outside, target_is_directory=True)
    try:
        body = {"project_id": "product", "task_id": "feature:T001", "request_id": "bound", "provider_id": "cloud", "model_id": "specific", "thinking_level": "high"}
        row = adapter.prepare(identity(actions=("project.execute",)), body)
        frozen = row["root_binding"]
        assert frozen["primary_root_id"] == frozen["cwd_root_id"] == frozen["claim_root_id"] == "state"
        context = next(root for root in frozen["roots"] if root["id"] == "docs")["context_snapshot"]
        snapshot = Path(context["path"])
        assert (snapshot / "safe.py").read_text(encoding="utf-8") == "value = 1\n"
        assert not (snapshot / ".env").exists() and not (snapshot / ".git").exists()
        assert not (snapshot / "escape").exists() and not (snapshot / "leak.py").exists()
        assert adapter.pi_binding(row).rootset_digest == row["root_binding_digest"]
        # The idempotent request returns its frozen pre-change binding.
        adapter.config["projects"][0]["roots"][1]["path"] = str(tmp_path / "other")
        assert adapter.prepare(identity(actions=("project.execute",)), body)["root_binding_digest"] == row["root_binding_digest"]
    finally:
        store.close()


@requires_safe_task_roots
def test_not_ready_task_does_not_create_a_context_destination(tmp_path):
    adapter, store, _checkout, secondary, _calls = _multi_root_adapter(tmp_path)
    (secondary / "safe.py").write_text("value = 1\n", encoding="utf-8")

    def not_ready(argv, **_kwargs):
        verb = argv[1]
        if verb == "show": data = {"task": {"id": "feature:T001", "status": "needs_review"}, "active_claims": []}
        elif verb == "prd": data = {"prds": [{"id": "feature", "status": "approved"}]}
        elif verb == "packet": return b"packet\n" + json.dumps({"task_id": "feature:T001", "dependencies_open": []}).encode()
        else: raise AssertionError(argv)
        return json.dumps({"ok": True, "data": data}).encode()

    try:
        adapter.run = not_ready
        body = {"project_id": "product", "task_id": "feature:T001", "request_id": "not-ready", "provider_id": "cloud", "model_id": "specific", "thinking_level": "high"}
        _error = pytest.raises(ObservatoryError)
        with _error as caught:
            adapter.prepare(identity(actions=("project.execute",)), body)
        assert caught.value.code == "task_not_ready"
        assert not (Path(adapter.config["projects"][0]["runner_root"]) / ".workbench-context").exists()
    finally:
        store.close()


@requires_safe_task_roots
def test_snapshot_failure_is_retained_without_a_final_destination(tmp_path, monkeypatch):
    adapter, store, _checkout, secondary, _calls = _multi_root_adapter(tmp_path)
    (secondary / "safe.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(adapter, "_copy_context_tree", lambda *_args: (_ for _ in ()).throw(OSError("fixture")))
    try:
        body = {"project_id": "product", "task_id": "feature:T001", "request_id": "snapshot-failure", "provider_id": "cloud", "model_id": "specific", "thinking_level": "high"}
        with pytest.raises(ObservatoryError) as caught:
            adapter.prepare(identity(actions=("project.execute",)), body)
        assert caught.value.code == "preparation_failed"
        assert store.get("task-binding", "alice", "snapshot-failure")["status"] == "released"
        assert not (Path(adapter.config["projects"][0]["runner_root"]) / ".workbench-context" / "snapshot-failure").exists()
    finally:
        store.close()


@requires_safe_task_roots
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires FIFO support")
def test_context_snapshot_refuses_raced_special_objects_and_growth(tmp_path, monkeypatch):
    adapter, store, _checkout, secondary, _calls = _multi_root_adapter(tmp_path)
    (secondary / "safe.py").write_text("safe\n", encoding="utf-8")
    for name in ("fifo.txt", "directory.txt", "grown.txt"):
        (secondary / name).write_text("small\n", encoding="utf-8")
    original = projects_module.os.open
    replaced = set()

    def raced(path, flags, *args, **kwargs):
        if path in {"fifo.txt", "directory.txt", "grown.txt"} and "dir_fd" in kwargs and flags & os.O_NONBLOCK and path not in replaced:
            replaced.add(path)
            target = secondary / path
            target.unlink()
            if path == "fifo.txt":
                os.mkfifo(target)
            elif path == "directory.txt":
                target.mkdir()
            else:
                target.write_bytes(b"x" * (64 * 1024 + 1))
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(projects_module.os, "open", raced)
    try:
        body = {"project_id": "product", "task_id": "feature:T001", "request_id": "raced", "provider_id": "cloud", "model_id": "specific", "thinking_level": "high"}
        row = adapter.prepare(identity(actions=("project.execute",)), body)
        snapshot = Path(next(root for root in row["root_binding"]["roots"] if root["id"] == "docs")["context_snapshot"]["path"])
        assert (snapshot / "safe.py").is_file()
        assert not any((snapshot / name).exists() for name in ("fifo.txt", "directory.txt", "grown.txt"))
        assert replaced == {"fifo.txt", "directory.txt", "grown.txt"}
    finally:
        store.close()


@pytest.mark.parametrize("change, code", [
    (lambda config, _checkout, secondary: config["projects"][0]["roots"][1].update(owner_id="remote-owner"), "project_root_unsupported"),
    (lambda config, _checkout, secondary: config["projects"][0].update(primary_root_id="docs"), "project_root_invalid"),
    (lambda config, _checkout, secondary: config["projects"][0]["roots"][1].update(path=str(secondary / "missing")), "project_root_unavailable"),
])
@requires_safe_task_roots
def test_prepare_rejects_unsupported_root_sets_before_claim(tmp_path, change, code):
    adapter, store, checkout, secondary, calls = _multi_root_adapter(tmp_path)
    try:
        change(adapter.config, checkout, secondary)
        body = {"project_id": "product", "task_id": "feature:T001", "request_id": "reject", "provider_id": "cloud", "model_id": "specific", "thinking_level": "high"}
        with pytest.raises(ObservatoryError) as error:
            adapter.prepare(identity(actions=("project.execute",)), body)
        assert error.value.code == code
        assert not any(call[1] == "claim" for call in calls)
    finally:
        store.close()


@requires_safe_task_roots
def test_prepare_rejects_symlinked_or_aliased_secondary_before_claim(tmp_path):
    adapter, store, _checkout, secondary, calls = _multi_root_adapter(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(secondary, target_is_directory=True)
    try:
        adapter.config["projects"][0]["roots"][1]["path"] = str(alias)
        body = {"project_id": "product", "task_id": "feature:T001", "request_id": "symlink", "provider_id": "cloud", "model_id": "specific", "thinking_level": "high"}
        with pytest.raises(ObservatoryError) as error:
            adapter.prepare(identity(actions=("project.execute",)), body)
        assert error.value.code == "project_root_unavailable"
        assert not any(call[1] == "claim" for call in calls)
    finally:
        store.close()


def test_task_evidence_uses_sandbox_and_submits_actual_cli_evidence(tmp_path):
    config = {"projects": [{"id": "product", "label": "Product", "resource_id": "serve-a", "checkout": str(tmp_path), "anvil_binary": str(tmp_path / "anvil"), "runner_root": str(tmp_path / "runners")}], "pi": {"id": "isolated"}}
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
        if _SAFE_TASK_ROOTS:
            row = adapter.prepare(identity(actions=("project.execute",)), body)
        else:
            runner_root = tmp_path / "runners"
            row = {
                "id": "request-one", "owner": "alice", "project_id": "product", "task_id": "feature:T001",
                "actor": "workbench-test", "lease_id": "claim-one", "provider_id": "cloud", "status": "ready",
                "runner_checkout": str(runner_root / "request-one"),
                "verification_checkout": str(runner_root / "request-one-verify"),
                "claim_worktree": str(claim), "artifact_root": str(runner_root / ".workbench-artifacts" / "request-one"),
                "baseline_sha": "a" * 40, "packet_digest": "b" * 64,
                "declared_paths": ["src/feature.py"], "verification_commands": ["python -m pytest"],
            }
            adapter.store.put("task-binding", "alice", row["id"], row)
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
    config = {"projects": [{"id": "product", "label": "Product", "resource_id": "serve-a", "checkout": str(tmp_path), "anvil_binary": str(tmp_path / "anvil"), "runner_root": str(tmp_path / "runners")}], "pi": {"id": "isolated"}}
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


def test_nonzero_structured_plan_failure_is_typed_and_redacted(tmp_path, projects):
    import sys
    raw = b'{"ok":false,"error":{"schema_id":"anvil.state.read-error.v1","code":"projection_not_converged","message":"DO_NOT_EXPOSE_PRIVATE_DETAILS"}}'
    with pytest.raises(BoundedCommandFailure) as failure:
        run_bounded([sys.executable, "-c", "import sys; sys.stdout.buffer.write(" + repr(raw) + "); sys.exit(1)"], cwd=tmp_path)
    assert raw not in failure.value.args
    assert "DO_NOT_EXPOSE" not in str(failure.value)
    assert "DO_NOT_EXPOSE" not in repr(failure.value)
    adapter, _, _ = projects
    adapter.run = lambda *_args, **_kwargs: (_ for _ in ()).throw(failure.value)
    with pytest.raises(ObservatoryError) as error:
        adapter.prd(identity(), "product", "feature")
    assert error.value.code == "project_projection_not_converged"
    assert "DO_NOT_EXPOSE" not in error.value.message


@pytest.mark.parametrize(
    "raw",
    [b"not-json", b"[]", b'{"ok":false,"error":[]}', b'{"ok":false,"error":{"schema_id":"anvil.state.read-error.v1","code":"private_error"}}', b"x" * (4 * 1024 * 1024 + 1)],
    ids=["not-json", "array", "error-array", "private-error", "oversized"],
)
def test_unrecognized_plan_failures_stay_generic(projects, raw):
    adapter, _, _ = projects
    adapter.run = lambda *_args, **_kwargs: (_ for _ in ()).throw(BoundedCommandFailure(raw))
    with pytest.raises(ObservatoryError) as error:
        adapter.prd(identity(), "product", "feature")
    assert error.value.code == "project_source_unavailable"


def test_malformed_success_envelopes_are_rejected(projects):
    adapter, _, _ = projects
    adapter.run = lambda *_args, **_kwargs: b"[]"
    with pytest.raises(ObservatoryError) as error:
        adapter.prd(identity(), "product", "feature")
    assert error.value.code == "invalid_project_source"


@pytest.mark.parametrize("verb", ["claim", "release", "renew", "submit"])
@pytest.mark.parametrize("nonzero", [True, False])
def test_mutation_failures_do_not_use_plan_read_diagnoses(projects, verb, nonzero):
    adapter, _, _ = projects
    raw = b'{"ok":false,"error":{"schema_id":"anvil.state.read-error.v1","code":"prd_not_found","message":"PRIVATE_ERROR"}}'

    def fail(*_args, **_kwargs):
        if nonzero:
            raise BoundedCommandFailure(raw)
        return raw

    adapter.run = fail
    with pytest.raises(ObservatoryError) as error:
        adapter.cli(adapter.configured("product"), verb, "feature:T001")
    assert error.value.code == "project_source_unavailable"
    assert "current task and claim state" in error.value.message
    assert "PRIVATE_ERROR" not in error.value.message


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

@pytest.mark.parametrize("limit, value", [
    ("_MAX_CONTEXT_FILES", 1),
    ("_MAX_CONTEXT_FILE_BYTES", 3),
    ("_MAX_CONTEXT_DEPTH", 1),
])
@requires_safe_task_roots
def test_snapshot_bound_skips_are_recorded_as_truncated(tmp_path, monkeypatch, limit, value):
    adapter, store, _checkout, secondary, _calls = _multi_root_adapter(tmp_path)
    if limit == "_MAX_CONTEXT_FILES":
        (secondary / "a.txt").write_text("a", encoding="utf-8")
        (secondary / "b.txt").write_text("b", encoding="utf-8")
    elif limit == "_MAX_CONTEXT_FILE_BYTES":
        (secondary / "large.txt").write_text("four", encoding="utf-8")
    else:
        (secondary / "nested").mkdir()
        (secondary / "nested" / "leaf.txt").write_text("leaf", encoding="utf-8")
    monkeypatch.setattr(projects_module, limit, value)
    try:
        row = adapter.prepare(identity(actions=("project.execute",)), {
            "project_id": "product", "task_id": "feature:T001", "request_id": f"truncated-{limit}",
            "provider_id": "cloud", "model_id": "specific", "thinking_level": "high",
        })
        snapshot = next(root for root in row["root_binding"]["roots"] if root["id"] == "docs")["context_snapshot"]
        assert snapshot["truncated"] is True
    finally:
        store.close()


@requires_safe_task_roots
def test_snapshot_cleanup_removes_completed_private_children_on_error(tmp_path, monkeypatch):
    adapter, store, _checkout, secondary, _calls = _multi_root_adapter(tmp_path)
    completed = secondary / "a-completed"
    completed.mkdir()
    (completed / "safe.txt").write_text("safe", encoding="utf-8")
    (secondary / "z-fail.txt").write_text("fail", encoding="utf-8")
    original_owner = adapter._context_owner

    def fail_after_completed_child(path):
        original_owner(path)
        if path.name == "z-fail.txt":
            raise OSError("injected owner failure")

    monkeypatch.setattr(adapter, "_context_owner", fail_after_completed_child)
    try:
        with pytest.raises(ObservatoryError) as caught:
            adapter.prepare(identity(actions=("project.execute",)), {
                "project_id": "product", "task_id": "feature:T001", "request_id": "cleanup-error",
                "provider_id": "cloud", "model_id": "specific", "thinking_level": "high",
            })
        assert caught.value.code == "preparation_failed"
        context_parent = Path(adapter.config["projects"][0]["runner_root"]) / ".workbench-context"
        assert not (context_parent / "cleanup-error").exists()
        assert not any(path.name.endswith(".staging") for path in context_parent.iterdir())
    finally:
        store.close()


def test_owner_root_facts_reject_source_and_claim_aliases(tmp_path):
    from copy import deepcopy
    adapter, store, row = _per_root_artifact_adapter(tmp_path, _PerRootArtifactSandbox())
    try:
        row["root_request"] = {"request_digest": "d" * 64}
        row["task_verification_commands"] = ["pytest app"]
        for declared, root in zip(row["root_binding"]["roots"], row["root_bindings"]):
            declared.update(repository_id=root["repository_id"], source_path=root["canonical_root"], verification_commands=root["verification_commands"])
        response = {"status": "ready", "request_id": row["id"], "request_digest": "d" * 64,
                    "roots": [root | {"state": "prepared"} for root in row["root_bindings"]]}
        assert len(adapter._root_facts(row, response)) == 2
        for field, value in (("canonical_root", str(tmp_path / "wrong-source")),
                             ("claim_worktree", response["roots"][0]["claim_worktree"]),
                             ("claim_worktree", response["roots"][0]["canonical_root"])):
            changed = deepcopy(response)
            changed["roots"][1][field] = value
            with pytest.raises(ObservatoryError, match="workspaces"):
                adapter._root_facts(row, changed)
        with pytest.raises(ObservatoryError, match="private root path"):
            adapter._provision_root_claim(adapter.configured("product"), row)
    finally:
        store.close()
