"""Exercise real route/session/journal wiring with a separate deterministic owner."""

import http.client
import json
import socket
import sqlite3
import threading
import time

import pytest

from anvil_serving.observability.api import TelemetryRegistry, run_server_in_thread
from anvil_serving.observability.dashboard.app import create_dashboard_server
from anvil_serving.observability.dashboard.console import Console, attach_console
from anvil_serving.observability.dashboard.contracts import canonical, digest
from anvil_serving.observability.dashboard.intents import IntentStore
from anvil_serving.observability.dashboard import intents as intents_module
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
        self.benchmark_rows = None
        self.benchmark_states = {}

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

    def list_benchmark_jobs(self, *, limit=100, cursor=None, refresh_refs=None):
        if self.fail_verification:
            raise TimeoutError("fixture benchmark owner unavailable")
        rows = self.benchmark_rows or [{
            "native_id": "benchmark-fixture-1", "native_state": "completed", "suite": "context",
            "profile": "fixture-profile", "model": "fixture-model", "submitted_at": "2026-09-19T00:00:00Z",
            "updated_at": "2026-09-19T00:01:00Z", "artifact": {"sha256": "a" * 64},
        }]
        result = {"schema": "anvil-serving.benchmark-job-list/v1", "items": rows[:limit], "next_cursor": None, "source": {
            "id": "benchmark-owner", "status": "fresh", "observed_at": "2026-09-19T00:01:00Z",
            "deadline_seconds": 1.0,
        }}
        if refresh_refs:
            updates = []
            for ref in refresh_refs:
                state = self.benchmark_states.get((ref["suite"], ref["run_id"]))
                if state is not None:
                    updates.append({**ref, "native_state": state, "updated_at": "2026-09-19T00:02:00Z"})
            result["refresh"] = {"items": updates, "partial": len(updates) != len(refresh_refs)}
        return result


@pytest.fixture
def site(tmp_path):
    owner = FakeOwner()
    config = {"origin": "https://console.example.test", "base_path": "/observatory/", "operate": True,
              "users": [{"id": "operator-fixture", "username": "operator", "role": "operator", "resources": ["*"], "actions": ["configuration.apply", "tier.quiesce"]}],
              "authentication": {}, "state_path": str(tmp_path / "journal.sqlite"), "fixture": True,
              "runs": {"benchmark": {"resource_id": "benchmark.runs"}},
              "workbench": {"state_path": str(tmp_path / "workbench.sqlite"), "host_pi": {
                  "id": "host-pi", "resource_id": "host.pi", "origin": "https://pi.example.test",
                  "version": "0.9.0", "runtime_sha256": "a" * 64,
              }}}
    console = Console(config, metrics=FakeMetrics(), adapter=owner, authenticate=lambda u, p: u == "operator" and p == "fixture-password")
    server = create_dashboard_server(TelemetryRegistry(), port=0, auth_env="LEGACY_TOKEN", environment={"LEGACY_TOKEN": "fixture-legacy-read-token"})
    attach_console(server, console)
    thread = run_server_in_thread(server)
    session = {}

    def call(method, route, body=None, *, authenticated=True, extra=None, raw=None, include_headers=False):
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
        response_headers = dict(response.getheaders())
        connection.close()
        return (status, data, response_headers) if include_headers else (status, data)

    call.server = server
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


def test_host_pi_rejects_console_or_fallback_same_origin(tmp_path):
    base = {"origin": "https://console.example.test", "base_path": "/observatory/", "users": [],
            "authentication": {"grafana_url": "http://127.0.0.1:3000"}, "state_path": str(tmp_path / "journal.sqlite"),
            "prometheus_url": "http://127.0.0.1:9090", "inventory": {}, "workbench": {
                "state_path": str(tmp_path / "workbench.sqlite"), "host_pi": {
                    "id": "host-pi", "resource_id": "host.pi", "origin": "https://console.example.test:443",
                    "version": "0.9.0", "runtime_sha256": "a" * 64,
                }}}
    with pytest.raises(ValueError, match="separate application origin"):
        Console(base, metrics=FakeMetrics())

    fallback = {**base, "origin": "https://primary.example.test", "users": [{
        "id": "operator-fixture", "username": "operator", "resources": ["host.pi"], "actions": [],
    }], "authentication": {"mode": "connect", "connect": {
        "resource": "observatory", "keys": [{"id": "current", "secret_env": "CONNECT_ASSERTION_CURRENT"}],
        "principals": {"connect-owner": "operator-fixture"},
    }}, "fallback_authentication": {
        "origin": "https://console.example.test", "grafana_url": "http://127.0.0.1:3000", "users": ["operator-fixture"],
    }}
    with pytest.raises(ValueError, match="separate application origin"):
        Console(fallback, metrics=FakeMetrics(), environment={"CONNECT_ASSERTION_CURRENT": "fixture-secret"})


def test_run_sources_do_not_expose_the_other_side_without_its_grant(site):
    console, _owner, _call = site
    operation_only = Session("operation-only", "csrf", Principal(
        "operation-only", "operation-only", "viewer", frozenset({"serve-fixture-a"}), frozenset(),
    ), time.time() + 60)
    catalog = console.read("run-sources", {}, operation_only)
    assert {item["id"] for item in catalog["items"]} == {"operations"}
    assert console.read("runs/operations", {"limit": "1"}, operation_only)["items"] == []
    with pytest.raises(ObservatoryError) as denied:
        console.read("runs/benchmark", {"limit": "1"}, operation_only)
    assert denied.value.status == 403


def test_benchmark_cache_is_authorized_before_stale_fallback(site):
    console, owner, _call = site
    session = Session("fixture-session", "csrf", console.access.users["operator"], time.time() + 60)
    fresh = console.read("runs/benchmark", {"limit": "1"}, session)
    owner.fail_verification = True
    stale = console.read("runs/benchmark", {"limit": "1"}, session)
    denied = Session("denied-session", "csrf", Principal("denied", "denied", "viewer", frozenset(), frozenset()), time.time() + 60)

    assert fresh["sources"][0]["status"] == "fresh"
    assert stale["sources"][0]["status"] == "stale"
    assert stale["items"][0]["freshness"] == "stale"
    with pytest.raises(ObservatoryError, match="does not grant"):
        console.read("runs/benchmark", {"limit": "1"}, denied)


def test_benchmark_head_poll_carries_bounded_off_head_status_updates(site):
    console, owner, _call = site
    owner.benchmark_rows = [{
        "native_id": "older-active", "native_state": "running", "suite": "context",
        "profile": "fixture-profile", "model": "fixture-model",
        "submitted_at": "2026-09-19T00:00:00Z", "updated_at": "2026-09-19T00:01:00Z",
    }]
    owner.benchmark_states[("context", "older-active")] = "completed"
    session = Session("fixture-session", "csrf", console.access.users["operator"], time.time() + 60)
    result = console.read("runs/benchmark", {
        "limit": "1", "refresh_refs": json.dumps([{"suite": "context", "run_id": "older-active"}]),
    }, session)
    assert result["items"][0]["native_state"] == "running"
    assert result["updates"][0]["native_state"] == "completed"
    assert result["sources"][0]["partial"] is False

    denied = Session("denied", "csrf", Principal("denied", "denied", "viewer", frozenset(), frozenset()), time.time() + 60)
    with pytest.raises(ObservatoryError) as exc:
        console.read("runs/benchmark", {"refresh_refs": "not-json"}, denied)
    assert exc.value.status == 403

    with pytest.raises(ObservatoryError, match="distinct valid"):
        console.read("runs/benchmark", {"refresh_refs": json.dumps([{"suite": "bad/path", "run_id": "older-active"}])}, session)


def test_console_operation_runs_page_past_the_legacy_hundred_row_cap(site):
    console, _owner, _call = site
    for number in range(101):
        native_id = f"operation-{number:03d}"
        item = {
            "id": native_id, "resource_id": "serve-fixture-a", "host_id": "host-fixture-a",
            "action_id": "configuration.apply", "label": native_id, "actor": "operator-fixture",
            "service_identity": "fixture", "submitted_at": "2026-09-19T00:00:00Z",
            "updated_at": "2026-09-19T00:00:00Z", "status": "succeeded", "native_state": "completed",
            "owner_operation_id": native_id, "execution_outcome": "succeeded", "verification": {},
            "recovery": {}, "events": [], "evidence_id": None, "candidate_digest": "a" * 64,
            "baseline_digest": "b" * 64,
        }
        console.store.db.execute(
            "INSERT INTO intents VALUES(?,?,?,?,?,?,?,?)",
            (native_id, "key-" + native_id, digest(native_id), "operator-fixture", "serve-fixture-a",
             canonical(item).decode(), float(number), float(number)),
        )
    session = Session("fixture-session", "csrf", console.access.users["operator"], time.time() + 60)
    first = page = console.read("runs/operations", {"limit": "100"}, session)
    native_ids = []
    while True:
        native_ids.extend(item["native_id"] for item in page["items"])
        if page["next_cursor"] is None:
            break
        page = console.read("runs/operations", {"limit": "100", "cursor": page["next_cursor"]}, session)

    assert first["sources"][0]["truncated"] is True
    assert len(native_ids) == 101 and len(set(native_ids)) == 101


def test_operation_run_query_has_an_enforced_sqlite_deadline(tmp_path, monkeypatch):
    store = IntentStore(tmp_path / "journal.sqlite")
    try:
        for number in range(256):
            store.db.execute(
                "INSERT INTO intents VALUES(?,?,?,?,?,?,?,?)",
                (f"operation-{number}", f"key-{number}", "f", "operator", "resource", "{}", float(number), float(number)),
            )
        calls = iter((0.0, 0.0, 2.0, 2.0))
        monkeypatch.setattr(intents_module.time, "monotonic", lambda: next(calls, 2.0))
        with pytest.raises(ObservatoryError, match="read deadline"):
            store.list_run_page(frozenset({"resource"}), limit=100)
    finally:
        store.close()


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


@pytest.mark.parametrize("route", ["drafts", "previews", "operations"])
def test_read_credentials_and_forged_identity_cannot_mutate(site, route):
    console, owner, call = site
    for token in ("fixture-legacy-read-token", "fixture-workload-read-token"):
        status, _ = call("POST", route, {}, authenticated=False, extra={"Authorization": "Bearer " + token, "Tailscale-User-Login": "operator"})
        assert status == 401
    assert owner.mutations == 0


def _rejected_mutation(port, headers, body=b""):
    request = (
        b"POST /observatory/api/observatory/v1/previews HTTP/1.0\r\n"
        + b"".join(name.encode("ascii") + b": " + value.encode("ascii") + b"\r\n" for name, value in headers)
        + b"Connection: close\r\n\r\n"
        + body
    )
    with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
        connection.settimeout(1)
        started = time.monotonic()
        connection.sendall(request)
        raw = b""
        while True:
            chunk = connection.recv(4096)
            if not chunk:
                break
            raw += chunk
    return time.monotonic() - started, raw


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


class _SecondIntentUpdateFault:
    def __init__(self, connection):
        self.connection = connection
        self.intent_updates = 0

    def execute(self, statement, parameters=()):
        if statement.startswith("UPDATE intents"):
            self.intent_updates += 1
            if self.intent_updates == 2:
                raise sqlite3.OperationalError("fixture second intent update fault")
        return self.connection.execute(statement, parameters)

    def __getattr__(self, name):
        return getattr(self.connection, name)


def _recovery_pair(store):
    original_preview = {"id": "original-preview", "resource_id": "runtime-a", "host_id": "host-fixture-a",
                        "action_id": "experiment.start", "label": "Start fixture", "candidate_digest": digest({}),
                        "baseline_digest": digest({}), "private_parameters": {}, "private_values": {}}
    original, _ = store.accept(original_preview, "operator", "original-intent")
    evidence_id = store.save_evidence("runtime-a", {"kind": "failed_candidate"})
    store.update(original["id"], status="manual_recovery_required", execution_outcome="failed", evidence_id=evidence_id)
    recovery_preview = {**original_preview, "id": "recovery-preview", "action_id": "operation.recover",
                        "private_recovery_of": original["id"], "private_parameters": {"run_id": "original-intent"}}
    recovery, _ = store.accept(recovery_preview, "operator", "recovery-intent")
    store.update(recovery["id"], status="verifying", execution_outcome="succeeded")
    return original, recovery, evidence_id


def _intent_body(store, operation_id):
    return store.db.execute("SELECT body FROM intents WHERE id=?", (operation_id,)).fetchone()[0]


def test_verified_recovery_publication_is_atomic(tmp_path):
    console = Console({"origin": "https://console.example.test", "base_path": "/observatory/", "users": [],
                       "authentication": {}, "state_path": str(tmp_path / "journal.sqlite")},
                      adapter=FakeOwner(), metrics=FakeMetrics(), authenticate=lambda *_: False)
    try:
        original, recovery, evidence_id = _recovery_pair(console.store)
        publication = console.store.publish_verified_recovery(
            original["id"], recovery["id"], native_state="succeeded",
            verification={"status": "passed"}, evidence_id=None,
        )
        assert set(publication) == {"original", "recovery"}
        prior, published = console.store.get(original["id"]), console.store.get(recovery["id"])
        assert publication == {"original": prior, "recovery": published}
        assert prior["status"] == "failed"
        assert prior["execution_outcome"] == "failed" and prior["evidence_id"] == evidence_id
        assert prior["recovery"]["operation_id"] == recovery["id"]
        assert published["status"] == "succeeded"
    finally:
        console.close()


def test_verified_recovery_second_sql_write_rolls_back_before_restart(tmp_path):
    path = tmp_path / "journal.sqlite"
    store = IntentStore(path)
    original, recovery, _evidence_id = _recovery_pair(store)
    original_before, recovery_before = _intent_body(store, original["id"]), _intent_body(store, recovery["id"])
    store.db = _SecondIntentUpdateFault(store.db)
    with pytest.raises(sqlite3.OperationalError, match="second intent update"):
        store.publish_verified_recovery(original["id"], recovery["id"], native_state="succeeded",
                                        verification={"status": "passed"}, evidence_id=None)
    assert _intent_body(store, original["id"]) == original_before
    assert _intent_body(store, recovery["id"]) == recovery_before
    store.close()

    restored = IntentStore(path)
    try:
        assert _intent_body(restored, original["id"]) == original_before
        assert restored.get(original["id"])["recovery"]["status"] == "not_attempted"
        assert restored.get(recovery["id"])["status"] == "outcome_unknown"
    finally:
        restored.close()


@pytest.mark.parametrize("caller", ("_execute", "_reconcile"))
def test_recovery_publication_survives_post_commit_prune_failure(tmp_path, caller):
    console = Console({"origin": "https://console.example.test", "base_path": "/observatory/", "users": [],
                       "authentication": {}, "state_path": str(tmp_path / "journal.sqlite")},
                      adapter=FakeOwner(), metrics=FakeMetrics(), authenticate=lambda *_: False)
    try:
        original, recovery, evidence_id = _recovery_pair(console.store)
        if caller == "_reconcile":
            console.adapter.reconciled = True
            console.adapter.completed[recovery["intent_key"]] = {"ok": True, "native_state": "succeeded", "execution_outcome": "succeeded"}

        def fail_prune():
            raise sqlite3.OperationalError("fixture prune failure")

        console.store.prune = fail_prune
        with pytest.warns(RuntimeWarning) as caught:
            getattr(console, caller)(recovery)
        assert str(caught[0].message) == "Observatory retention cleanup failed after terminal publication."
        prior, published = console.store.get(original["id"]), console.store.get(recovery["id"])
        assert prior["status"] == "failed"
        assert prior["execution_outcome"] == "failed" and prior["evidence_id"] == evidence_id
        assert prior["recovery"]["status"] == "succeeded" and prior["recovery"]["operation_id"] == recovery["id"]
        assert published["status"] == "succeeded" and published["execution_outcome"] == "succeeded"
        assert published["verification"]["status"] == "passed"
    finally:
        console.close()


@pytest.mark.parametrize("case", ("missing_original", "missing_recovery", "mismatched_link"))
def test_verified_recovery_rejects_invalid_pair_without_writing_other_row(tmp_path, case):
    store = IntentStore(tmp_path / "journal.sqlite")
    try:
        original, recovery, _evidence_id = _recovery_pair(store)
        if case == "missing_original":
            preserved_id = recovery["id"]
            store.db.execute("DELETE FROM intents WHERE id=?", (original["id"],))
        elif case == "missing_recovery":
            preserved_id = original["id"]
            store.db.execute("DELETE FROM intents WHERE id=?", (recovery["id"],))
        else:
            preserved_id = original["id"]
            broken = store.get(recovery["id"])
            broken["private_preview"] = {**broken["private_preview"], "private_recovery_of": "other-operation"}
            store.update(recovery["id"], private_preview=broken["private_preview"])
        preserved_before = _intent_body(store, preserved_id)
        with pytest.raises(ObservatoryError, match="linked recovery operation"):
            store.publish_verified_recovery(original["id"], recovery["id"], native_state="succeeded",
                                            verification={"status": "passed"}, evidence_id=None)
        assert _intent_body(store, preserved_id) == preserved_before
    finally:
        store.close()
