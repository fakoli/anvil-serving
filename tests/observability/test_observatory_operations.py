"""Exercise real route/session/journal wiring with a separate deterministic owner."""

import http.client
import json
import sqlite3
import threading
import time

import pytest

from anvil_serving.observability.api import TelemetryRegistry, run_server_in_thread
from anvil_serving.observability.dashboard.app import create_dashboard_server
from anvil_serving.observability.dashboard.console import Console, attach_console
from anvil_serving.observability.dashboard.contracts import digest
from anvil_serving.observability.dashboard.intents import IntentStore
from anvil_serving.observability.dashboard.access import Principal, Session
from anvil_serving.observability.dashboard.contracts import ObservatoryError


class FakeMetrics:
    def snapshot(self):
        return {"hosts": [{"id": "host-fixture-a", "controller": {"status": "unavailable"}}, {"id": "host-fixture-offline", "telemetry": {"status": "unavailable"}}],
                "serves": [{"id": "serve-fixture-a", "host_id": "host-fixture-a", "readiness": "unknown"}], "coverage": {"status": "partial"}}

    def integration_status(self):
        return {"prometheus": {"status": "unavailable"}}

    def chart(self, chart_id, **kwargs):
        if chart_id not in {"generation", "queue"}:
            raise ValueError("invalid chart")
        return {"id": chart_id, "status": "unavailable", "series": []}


class FakeOwner:
    def __init__(self):
        self.mutations = 0
        self.current = 64
        self.observed = 64
        self.fail_verification = False
        self.lose_transport = False
        self.reconciled = False
        self.completed = {}
        self.gate = threading.Event()
        self.gate.set()

    def snapshot(self):
        return {"hosts": [{"id": "host-fixture-a", "controller": {"status": "complete"}}], "serves": []}

    def controls(self, resource):
        return {"resource_id": resource, "baseline_digest": digest(self.current),
                "actions": [{"id": name, "label": name, "supported": True} for name in ("configuration.apply", "tier.quiesce")],
                "settings": [{"setting_id": "max_output", "label": "Maximum output", "value_type": "integer", "unit": "tokens", "support": "supported", "configured": self.current,
                              "observed": self.observed, "constraints": {"minimum": 1, "maximum": 128}}]}

    def preview(self, resource_id, action_id, values=None, parameters=None):
        values = values or {}
        if parameters:
            raise ValueError("no arbitrary parameters")
        return {"host_id": "host-fixture-a", "resource_id": resource_id, "action_id": action_id, "label": "Apply exact fixture change",
                "baseline_digest": digest(self.current), "candidate_digest": digest(values), "effect": "config_reload",
                "diff": [{"field": "max_output", "before": self.current, "after": values.get("max_output", self.current)}],
                "recovery": "Restore the prior fixture setting through the owner.", "binding": {"secret_marker": "never-browser-secret", "private_path": "/private/fixture/config"}}

    def execute(self, preview, intent_key):
        self.mutations += 1
        self.gate.wait(5)
        self.current = preview["private_values"].get("max_output", self.current)
        if not self.fail_verification:
            self.observed = self.current
        result = {"ok": True, "owner_operation_id": intent_key, "native_state": "succeeded", "execution_outcome": "succeeded"}
        self.completed[intent_key] = result
        if self.lose_transport:
            raise TimeoutError("secret upstream address must never leak")
        return result

    def reconcile(self, preview, intent_key):
        return self.completed.get(intent_key) if self.reconciled else None

    def verify(self, preview, result):
        correct = self.observed == preview["private_values"].get("max_output", self.current)
        return {"status": "passed" if correct else "failed", "message": "Fixture owner checked independently."}


@pytest.fixture
def site(tmp_path):
    owner = FakeOwner()
    config = {"origin": "https://console.example.test", "base_path": "/observatory/", "operate": True,
              "users": [{"id": "operator-fixture", "username": "operator", "role": "operator", "resources": ["*"], "actions": ["configuration.apply", "tier.quiesce"]}],
              "authentication": {}, "state_path": str(tmp_path / "journal.sqlite"), "fixture": True}
    console = Console(config, metrics=FakeMetrics(), adapter=owner, authenticate=lambda u, p: u == "operator" and p == "fixture-password")
    server = create_dashboard_server(TelemetryRegistry(), port=0, auth_env="LEGACY_TOKEN", environment={"LEGACY_TOKEN": "fixture-legacy-read-token"})
    attach_console(server, console)
    thread = run_server_in_thread(server)
    session = {}

    def call(method, route, body=None, *, authenticated=True, extra=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        headers = {"Host": "console.example.test", "Origin": config["origin"]}
        if authenticated and session:
            headers.update({"Cookie": session["cookie"], "X-CSRF-Token": session["csrf"]})
        if body is not None:
            raw = json.dumps(body)
        if raw is not None:
            headers["Content-Type"] = "application/json"
        headers.update(extra or {})
        connection.request(method, "/observatory/api/observatory/v1/" + route, body=raw, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        if route == "session" and method == "POST" and response.status == 200:
            session.update(cookie=response.getheader("Set-Cookie").split(";", 1)[0], csrf=data["data"]["csrf_token"])
        status = response.status
        connection.close()
        return status, data

    call("POST", "session", {"username": "operator", "password": "fixture-password"})
    yield console, owner, call
    owner.gate.set()
    server.shutdown()
    server.server_close()
    thread.join()
    console.close()


def preview(call, value=32):
    status, draft = call("POST", "drafts", {"resource_id": "serve-fixture-a", "values": {"max_output": value}})
    assert status == 200, draft
    status, result = call("POST", "previews", {"resource_id": "serve-fixture-a", "action_id": "configuration.apply", "draft_id": draft["data"]["id"]})
    assert status == 200, result
    return result["data"]


def terminal(call, key):
    for _ in range(100):
        status, result = call("GET", "operations/" + key)
        assert status == 200, result
        if result["data"]["status"] in {"succeeded", "failed", "outcome_unknown", "manual_recovery_required"}:
            return result["data"]
        time.sleep(0.01)
    raise AssertionError("operation did not reach expected state")


def test_preview_is_read_only_and_duplicate_confirmation_is_one_dispatch(site):
    console, owner, call = site
    review = preview(call)
    assert owner.mutations == 0 and owner.current == 64 and owner.observed == 64
    assert "binding" not in review and "never-browser-secret" not in json.dumps(review)
    request = {"preview_id": review["id"], "intent_key": "fixture-intent-1"}
    first, second = call("POST", "operations", request), call("POST", "operations", request)
    assert first[0] == second[0] == 202
    assert first[1]["data"]["id"] == second[1]["data"]["id"]
    operation = terminal(call, first[1]["data"]["id"])
    assert operation["status"] == "succeeded" and owner.mutations == 1 and owner.observed == 32
    changed = preview(call, 16)
    status, _ = call("POST", "operations", {"preview_id": changed["id"], "intent_key": "fixture-intent-1"})
    assert status == 409 and owner.mutations == 1


def test_failed_run_retains_evidence_and_recovery_independently(site):
    console, owner, call = site
    review = preview(call)
    owner.execute = lambda *_: {"ok": False, "execution_outcome": "failed", "native_state": "failed",
        "recovery": {"status": "succeeded", "message": "Previous configuration restored."},
        "evidence": {"correctness": "failed", "request": {"max_output": 32}, "restore": "succeeded"}}
    _, response = call("POST", "operations", {"preview_id": review["id"], "intent_key": "fixture-incorrect"})
    operation = terminal(call, response["data"]["id"])
    assert operation["status"] == "failed"
    assert operation["recovery"]["status"] == "succeeded"
    status, evidence = call("GET", "evidence/" + operation["evidence_id"])
    assert status == 200 and evidence["data"]["correctness"] == "failed"
    assert evidence["data"]["request"] == {"max_output": 32}


def test_failed_recovery_retains_attention_and_blocks_conflicting_intent(site):
    console, owner, call = site
    review = preview(call)
    owner.execute = lambda *_: {"ok": False, "execution_outcome": "failed",
        "recovery": {"status": "failed", "message": "Independent recovery required."},
        "evidence": {"failure": "fixture", "restore": "failed"}}
    _, response = call("POST", "operations", {"preview_id": review["id"], "intent_key": "fixture-recovery-failed"})
    operation = terminal(call, response["data"]["id"])
    assert operation["status"] == "manual_recovery_required" and operation["evidence_id"]
    fresh = preview(call)
    status, result = call("POST", "operations", {"preview_id": fresh["id"], "intent_key": "fixture-new-conflict"})
    assert status == 409 and result["error"]["code"] == "operation_conflict"
    console.store.prune()
    assert console.store.get(operation["id"])["status"] == "manual_recovery_required"


def test_expired_session_cannot_apply_open_preview(site):
    console, owner, call = site
    review = preview(call)
    console.access.clock = lambda: time.time() + 7200
    status, result = call("POST", "operations", {"preview_id": review["id"], "intent_key": "fixture-expired-session"})
    assert status == 401 and result["error"]["code"] == "unauthenticated"
    assert owner.mutations == 0 and not console.store.list_private()


def test_config_install_does_not_mark_stopped_model_ready(site):
    console, owner, call = site
    owner.snapshot = lambda: {"hosts": [], "serves": [{"id": "serve-fixture-a", "runtime_state": "stopped", "readiness": "not_ready"}]}
    review = preview(call)
    _, response = call("POST", "operations", {"preview_id": review["id"], "intent_key": "fixture-install-stopped"})
    assert terminal(call, response["data"]["id"])["status"] == "succeeded"
    _, fleet = call("GET", "fleet")
    assert fleet["data"]["serves"][0]["runtime_state"] == "stopped"
    assert fleet["data"]["serves"][0]["readiness"] == "not_ready"


def test_unknown_metric_filter_is_a_client_error_without_source_call(site):
    console, owner, call = site
    console.metrics.chart = lambda *_args, **_kwargs: pytest.fail("invalid query reached source")
    status, result = call("GET", "metrics?chart=arbitrary-query")
    assert status == 400 and result["error"]["code"] == "invalid_metric_filter"
    status, _ = call("GET", "metrics?chart=generation&range=unbounded")
    assert status == 400


def test_config_drift_and_expired_preview_never_dispatch(site):
    console, owner, call = site
    review = preview(call)
    owner.current = 65
    status, result = call("POST", "operations", {"preview_id": review["id"], "intent_key": "fixture-stale"})
    assert status == 409 and result["error"]["code"] == "stale_preview" and owner.mutations == 0


def test_lost_transport_reconciles_without_replay(site):
    console, owner, call = site
    owner.lose_transport = True
    review = preview(call)
    _, response = call("POST", "operations", {"preview_id": review["id"], "intent_key": "fixture-lost"})
    key = response["data"]["id"]
    assert terminal(call, key)["status"] == "outcome_unknown"
    assert owner.mutations == 1
    _, history = call("GET", "operations")
    assert history["data"]["items"][0]["id"] == key
    owner.reconciled = True
    for _ in range(100):
        _, status = call("GET", "operations/" + key)
        if status["data"]["status"] == "succeeded":
            break
        time.sleep(0.01)
    assert status["data"]["status"] == "succeeded" and owner.mutations == 1


def test_transient_verification_failure_during_reconcile_remains_resumable(site):
    console, owner, call = site
    owner.lose_transport = True
    review = preview(call)
    _, response = call("POST", "operations", {"preview_id": review["id"], "intent_key": "fixture-verification-lost"})
    key = response["data"]["id"]
    assert terminal(call, key)["status"] == "outcome_unknown"
    original_verify = owner.verify
    def unavailable(*args):
        raise TimeoutError("temporary verification loss")
    owner.verify = unavailable
    owner.reconciled = True
    console._reconcile(console.store.get(key))
    assert console.store.get(key)["status"] == "outcome_unknown"
    owner.verify = original_verify
    console._reconcile(console.store.get(key))
    assert console.store.get(key)["status"] == "succeeded" and owner.mutations == 1


def test_command_success_is_not_observed_success(site):
    console, owner, call = site
    owner.fail_verification = True
    review = preview(call)
    _, result = call("POST", "operations", {"preview_id": review["id"], "intent_key": "fixture-verification"})
    status = terminal(call, result["data"]["id"])
    assert status["status"] == "failed" and status["execution_outcome"] == "succeeded"
    assert status["verification"]["status"] == "failed"
    assert owner.observed == 64 and owner.current == 32


@pytest.mark.parametrize("route", ["drafts", "previews", "operations"])
def test_read_credentials_and_forged_identity_cannot_mutate(site, route):
    console, owner, call = site
    for token in ("fixture-legacy-read-token", "fixture-workload-read-token"):
        status, _ = call("POST", route, {}, authenticated=False, extra={"Authorization": "Bearer " + token, "Tailscale-User-Login": "operator"})
        assert status == 401
    assert owner.mutations == 0


def test_csrf_origin_body_and_query_hardening(site):
    console, owner, call = site
    for extra in ({"Origin": "https://evil.example.test"}, {"X-CSRF-Token": "forged"}, {"Host": "forged.example.test"}):
        assert call("POST", "previews", {}, extra=extra)[0] == 403
    assert call("POST", "previews", raw='{"resource_id":"one","resource_id":"two"}')[0] == 400
    assert call("GET", "controls?resource=one&resource=two")[0] == 400
    assert call("GET", "metrics?chart=generation&url=http://127.0.0.1/private")[0] == 400
    assert call("POST", "previews", {"resource_id": "serve-fixture-a", "action_id": "shell.execute"})[0] == 403
    assert owner.mutations == 0


def test_partial_sources_and_offline_hosts_survive(site):
    console, owner, call = site
    status, body = call("GET", "fleet")
    assert status == 200
    assert len(body["data"]["hosts"]) == 2
    assert body["data"]["hosts"][0]["controller"]["status"] == "complete"
    assert body["data"]["hosts"][1]["telemetry"]["status"] == "unavailable"


def test_owner_placeholders_cannot_erase_independent_telemetry(site):
    console, owner, call = site
    console.metrics.snapshot = lambda: {"hosts": [{"id": "host-fixture-a", "platform": "linux", "telemetry": {"status": "complete"}, "gpus": [{"id": "gpu-fixture-a", "memory_used": {"value": 42, "status": "fresh"}, "owners": []}]}],
                                        "serves": [{"id": "serve-fixture-a", "host_id": "host-fixture-a", "metrics": {"queue": {"value": 0, "status": "fresh"}}}]}
    owner.snapshot = lambda: {"hosts": [{"id": "host-fixture-a", "platform": "unknown", "telemetry": {"status": "unavailable"}, "controller": {"status": "complete"}, "gpus": [{"id": "gpu-fixture-a", "owners": ["serve-fixture-a"]}]}],
                               "serves": [{"id": "serve-fixture-a", "metrics": {}, "runtime_state": "running"}]}
    _, response = call("GET", "fleet")
    host, serve = response["data"]["hosts"][0], response["data"]["serves"][0]
    assert host["platform"] == "linux" and host["telemetry"]["status"] == "complete"
    assert host["gpus"][0]["memory_used"]["value"] == 42
    assert host["gpus"][0]["owners"] == ["serve-fixture-a"]
    assert serve["metrics"]["queue"]["value"] == 0 and serve["runtime_state"] == "running"


def test_host_grant_does_not_grant_its_serves_or_integration_ids(site):
    console, owner, call = site
    console.config["inventory"] = {"serves": [{"id": "serve-fixture-a", "host_id": "host-fixture-a"}]}
    reader = Session("key", "csrf", Principal("reader", "reader", "viewer", frozenset({"host-fixture-a"}), frozenset()), time.time() + 100)
    with pytest.raises(ObservatoryError):
        console.read("metrics", {"host": "host-fixture-a", "chart": "generation"}, reader)
    console.metrics.integration_status = lambda: {"hosts": ["host-fixture-a", "host-fixture-b"], "serves": ["serve-fixture-a"]}
    settings = console.read("settings", {}, reader)
    assert settings["integrations"]["hosts"] == ["host-fixture-a"]
    assert settings["integrations"]["serves"] == []


def test_observatory_does_not_publish_anonymous_legacy_telemetry(tmp_path):
    config = {"origin": "https://console.example.test", "base_path": "/observatory/", "users": [], "authentication": {}, "state_path": str(tmp_path / "db")}
    console = Console(config, metrics=FakeMetrics(), adapter=FakeOwner(), authenticate=lambda u, p: False)
    server = create_dashboard_server(TelemetryRegistry(), port=0)
    attach_console(server, console)
    thread = run_server_in_thread(server)
    try:
        for route, expected in (("/v1/metrics", 401), ("/v1/workloads", 403)):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
            connection.request("GET", route, headers={"Host": "console.example.test"})
            response = connection.getresponse()
            response.read()
            assert response.status == expected
            connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        console.close()


def test_journal_restart_preserves_ambiguous_intent_and_never_prunes_it(tmp_path):
    path = tmp_path / "journal.sqlite"
    store = IntentStore(path)
    preview = {"id": "fixture-preview", "resource_id": "fixture-serve", "host_id": "fixture-host", "action_id": "serve.stop", "label": "Stop fixture", "candidate_digest": "a" * 64, "baseline_digest": "b" * 64}
    item, created = store.accept(preview, "fixture-actor", "fixture-key")
    assert created
    store.close()
    restored = IntentStore(path, clock=lambda: time.time() + 86400 * 90)
    restored.prune()
    assert restored.get(item["id"])["status"] == "outcome_unknown"
    same, created = restored.accept(preview, "fixture-actor", "fixture-key")
    assert not created and same["id"] == item["id"]
    restored.close()


def test_additive_connect_profiles_survive_a_previous_v1_binary_open(tmp_path):
    """A rollback opens the same journal; it never restores an older database."""
    path = tmp_path / "journal.sqlite"
    # Start from the exact previous v1 table set with already-owned state.
    prior_schema = """
        CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, actor TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS previews(id TEXT PRIMARY KEY, actor TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS intents(id TEXT PRIMARY KEY, intent_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
            actor TEXT NOT NULL, resource TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, resource TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL);
        PRAGMA user_version=1;
    """
    prior = sqlite3.connect(path)
    prior.executescript(prior_schema)
    prior.execute("INSERT INTO drafts VALUES(?,?,?,?)", ("legacy-draft", "fixture-actor", '{"id":"legacy-draft"}', 0))
    prior.commit()
    prior.close()

    store = IntentStore(path)
    assert store.draft("legacy-draft", "fixture-actor") == {"id": "legacy-draft"}
    preview = {"id": "fixture-preview", "resource_id": "fixture-serve", "host_id": "fixture-host", "action_id": "serve.stop", "label": "Stop fixture", "candidate_digest": "a" * 64, "baseline_digest": "b" * 64}
    intent, created = store.accept(preview, "fixture-actor", "fixture-key")
    assert created
    profile = store.connect_profile("connect-subject")
    store.close()

    # This is the previous binary's open-time schema setup. Its v1 marker and
    # table declarations leave unknown additive tables and journal records in
    # place rather than replacing the state database.
    prior = sqlite3.connect(path)
    assert prior.execute("PRAGMA user_version").fetchone()[0] == 1
    prior.executescript(prior_schema)
    assert prior.execute("SELECT body FROM drafts WHERE id='legacy-draft'").fetchone()[0] == '{"id":"legacy-draft"}'
    prior.close()

    restored = IntentStore(path)
    assert restored.db.execute("PRAGMA user_version").fetchone()[0] == 1
    assert restored.draft("legacy-draft", "fixture-actor") == {"id": "legacy-draft"}
    assert restored.get(intent["id"])["id"] == intent["id"]
    assert restored.connect_profile("connect-subject") == profile
    assert restored.db.execute("SELECT count(*) FROM connect_profiles").fetchone()[0] == 1
    restored.close()

    foreign = tmp_path / "future.sqlite"
    future = sqlite3.connect(foreign)
    future.execute("PRAGMA user_version=2")
    future.close()
    with pytest.raises(ValueError, match="unsupported journal schema"):
        IntentStore(foreign)


@pytest.mark.parametrize("restoration_verified", [True, False])
def test_runtime_recovery_is_bound_to_original_intent_and_keeps_failure(tmp_path, restoration_verified):
    class RecoveryOwner(FakeOwner):
        def controls(self, resource):
            data = super().controls(resource)
            data["actions"] += [{"id": action, "supported": True} for action in ("experiment.start", "operation.recover")]
            return data

        def preview(self, resource_id, action_id, values=None, parameters=None):
            result = super().preview(resource_id, action_id, values)
            result["candidate_digest"] = digest(parameters or {})
            return result

        def execute(self, preview, intent_key):
            self.mutations += 1
            if preview["action_id"] == "experiment.start":
                return {"ok": False, "execution_outcome": "failed", "recovery": {"status": "failed"}, "evidence": {"kind": "failed_candidate"}}
            assert preview["private_parameters"] == {"run_id": "runtime-original-intent"}
            return {"ok": True, "execution_outcome": "succeeded", "recovery": {"status": "succeeded"}, "evidence": {"kind": "restoration"}}

        def verify(self, preview, result):
            return {"status": "passed" if restoration_verified else "failed"}

    owner = RecoveryOwner()
    actions = frozenset({"experiment.start", "operation.recover", "tier.quiesce"})
    config = {"origin": "https://console.example.test", "base_path": "/observatory/", "operate": True,
              "users": [{"id": "operator", "username": "operator", "role": "operator", "resources": ["*"], "actions": sorted(actions)}],
              "authentication": {}, "state_path": str(tmp_path / "journal.sqlite")}
    console = Console(config, adapter=owner, metrics=FakeMetrics(), authenticate=lambda *_: True)
    session = Session("key", "csrf", Principal("operator", "operator", "operator", frozenset({"*"}), actions), time.time() + 100)

    def settled(key):
        for _ in range(100):
            result = console.store.get(key)
            if result["status"] in {"succeeded", "failed", "manual_recovery_required"}:
                return result
            time.sleep(.01)
        pytest.fail("bounded owner fixture did not settle")

    try:
        original_preview = console.create_preview(session, {"resource_id": "runtime-a", "action_id": "experiment.start"})
        original = console.apply(session, {"preview_id": original_preview["id"], "intent_key": "runtime-original-intent"})
        assert settled(original["id"])["status"] == "manual_recovery_required"
        with pytest.raises(ObservatoryError, match="retained operation"):
            console.create_preview(session, {"resource_id": "runtime-a", "action_id": "operation.recover", "parameters": {"run_id": "forged"}})
        with pytest.raises(ObservatoryError, match="selected recovery"):
            console.create_preview(session, {"resource_id": "runtime-b", "action_id": "operation.recover", "operation_id": original["id"]})
        blocked = console.create_preview(session, {"resource_id": "runtime-a", "action_id": "tier.quiesce"})
        with pytest.raises(ObservatoryError, match="requires attention"):
            console.apply(session, {"preview_id": blocked["id"], "intent_key": "conflicting-intent"})
        recovery = console.create_preview(session, {"resource_id": "runtime-a", "action_id": "operation.recover", "operation_id": original["id"]})
        assert "private_recovery_of" not in recovery and "private_parameters" not in recovery
        accepted = console.apply(session, {"preview_id": recovery["id"], "intent_key": "recovery-intent"})
        assert console.apply(session, {"preview_id": recovery["id"], "intent_key": "recovery-intent"})["id"] == accepted["id"]
        assert settled(accepted["id"])["status"] == ("succeeded" if restoration_verified else "failed")
        prior = console.store.get(original["id"])
        assert prior["execution_outcome"] == "failed" and prior["evidence_id"]
        assert prior["status"] == ("failed" if restoration_verified else "manual_recovery_required")
        assert prior["recovery"]["status"] == ("succeeded" if restoration_verified else "failed")
        assert owner.mutations == 2
    finally:
        console.close()
