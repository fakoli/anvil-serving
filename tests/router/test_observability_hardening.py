"""ADR-0033 observability hardening: mid-stream honesty, inline-probe
single-flight, restart-detectable metrics, and the honest token estimator."""
from __future__ import annotations

import http.client
import json
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from anvil_serving.router.availability import AvailabilityResult, HttpHealthAvailability
from anvil_serving.router.config import ReplicaIdentity, ReplicaMember, load
from anvil_serving.router.decision_log import DecisionLog, DecisionLogWriter, decision_line
from anvil_serving.router.internal import InternalRequest, Message, estimate_tokens
from anvil_serving.router.model_capacity import MetricsSnapshot
from anvil_serving.router.replica_scheduler import ReplicaDecisionReason
from anvil_serving.router.serve import NoAvailableTierError, ReplicaRuntime, RoutingBackend, build_server


_CONFIG = """\
[router]
{router_keys}

[[router.tiers]]
id = "primary"
base_url = "http://127.0.0.1:31002/v1"
dialect = "openai"
context_limit = 4096
privacy = "local"
tool_support = true
auth_env = "ANVIL_PRIMARY_KEY"
model = "primary-model"
health_path = "/health"

[router.model_routes]
llm.primary = "primary"
"""


def _config(tmp_path: Path, **router_keys) -> str:
    lines = []
    for key, value in router_keys.items():
        rendered = f'"{value}"' if isinstance(value, str) else value
        lines.append(f"{key} = {rendered}")
    path = tmp_path / "router.toml"
    path.write_text(_CONFIG.format(router_keys="\n".join(lines)), encoding="utf-8")
    return str(path)


# --- 4B: mid-stream failure honesty ----------------------------------------


class ExplodingBackend:
    def generate(self, request):
        yield "first "
        yield "second "
        raise RuntimeError("upstream exploded with secret detail")


@pytest.mark.parametrize("path,accept", [("/v1/chat/completions", "openai"), ("/v1/messages", "anthropic")])
def test_mid_stream_failure_emits_terminal_error_and_valid_chunked_close(tmp_path, path, accept):
    config_path = _config(tmp_path)
    server = build_server(
        config_path, host="127.0.0.1", port=0, backends={"primary": ExplodingBackend()}
    )
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(host, port, timeout=10)
        body = {
            "model": "llm.primary",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }
        if path == "/v1/messages":
            body["max_tokens"] = 128
        connection.request(
            "POST", path, json.dumps(body), {"Content-Type": "application/json"}
        )
        response = connection.getresponse()
        raw = response.read()  # raises IncompleteRead if chunked framing is broken
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    text = raw.decode("utf-8", errors="replace")
    assert response.status == 200
    assert "first" in text
    if accept == "openai":
        assert '"error"' in text
        assert "[DONE]" not in text  # failure is distinguishable from completion
    else:
        assert "event: error" in text
    # Server-side detail never reaches the wire.
    assert "secret detail" not in text
    assert "RuntimeError" not in text


# --- 4A: inline availability probe single-flight ----------------------------


def _availability(tmp_path, opener, clock):
    config = load(_config(tmp_path))
    return HttpHealthAvailability(config, opener=opener, clock=clock, wall_clock=clock)


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def getcode(self):
        return self.status


def test_inline_single_flight_losers_get_last_known_or_fail_closed(tmp_path):
    clock = [0.0]
    release = threading.Event()
    started = threading.Event()

    def opener(request, timeout):
        started.set()
        release.wait(timeout=10)
        return _Response()

    inner = _availability(tmp_path, opener, lambda: clock[0])
    tier = load(_config(tmp_path)).tiers[0]

    winner_result = {}

    def winner():
        winner_result["result"] = inner.check(tier)

    thread = threading.Thread(target=winner, daemon=True)
    thread.start()
    assert started.wait(timeout=5)
    # No prior result: the concurrent caller fails closed instead of stacking
    # a duplicate probe behind the same struggling serve.
    loser = inner.check(tier)
    assert loser.available is False
    assert loser.reason == "probe_pending"
    release.set()
    thread.join(timeout=5)
    assert winner_result["result"].available is True


# --- 4C: restart-detectable metrics ------------------------------------------


def test_metrics_expose_process_start_time_and_buffer_capacity(tmp_path):
    config_path = _config(tmp_path)

    class OkBackend:
        def generate(self, request):
            yield "ok"

    server = build_server(
        config_path, host="127.0.0.1", port=0, backends={"primary": OkBackend()}
    )
    try:
        rendered = server.anvil_routing.prometheus_metrics({})
    finally:
        server.server_close()
    assert "anvil_router_process_start_time_seconds " in rendered
    assert "anvil_router_decision_buffer_capacity 10000" in rendered
    start_line = next(
        line
        for line in rendered.splitlines()
        if line.startswith("anvil_router_process_start_time_seconds ")
    )
    assert float(start_line.split()[-1]) <= time.time() + 1


# --- 4D: honest token estimator ----------------------------------------------


def test_estimate_tokens_floors_at_bytes_over_four():
    # Prose: the word count still dominates for ordinary English.
    assert estimate_tokens(["one two three"]) == 3
    # CJK: one "word" by whitespace, but bytes/4 reflects real token cost.
    cjk = "模型服务" * 8  # 32 chars, 96 utf-8 bytes
    assert estimate_tokens([cjk]) == 24
    # Base64-ish blobs: single word, large byte count.
    blob = "A" * 4000
    assert estimate_tokens([blob]) == 1000
    assert estimate_tokens([]) == 0


class _PrivateProviderError(RuntimeError):
    pass


class _DecisionMember:
    def __init__(self, mode):
        self.mode = mode
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        if self.mode == "eager":
            raise _PrivateProviderError("provider-secret http://100.64.0.10/private")

        def stream():
            if self.mode == "first_stream":
                raise _PrivateProviderError("provider-secret")
            yield "response-secret"
            if self.mode == "mid_stream":
                raise _PrivateProviderError("provider-secret")
        return stream()

    def get_last_structured(self):
        if self.mode == "completion":
            raise _PrivateProviderError("provider-secret")
        return None


class _DecisionReadiness:
    def __init__(self):
        self.calls = []
        self.available = True

    def check(self, tier):
        raise AssertionError("no aggregate replica probe")

    def check_member(self, tier, member_id):
        self.calls.append(member_id)
        return AvailabilityResult(
            self.available, "ready" if self.available else "unavailable",
            "ready" if self.available else "identity_mismatch",
            expected_model="model-private", observed_model="unexpected-private",
        )


def _replica_decisions(tmp_path, mode="success", *, capacity=False, capacity_cap=2):
    # Mirror test_backends' actual ReplicaRuntime + compound TierAdmission path.
    config = load(_config(tmp_path))
    tier = replace(
        config.tiers[0], base_url="", model_identity=True,
        replicas=tuple(ReplicaMember(
            member, f"http://127.0.0.1:{31001 + index}/v1",
            "node-a", f"resource-{index}", "qualification:a", capacity_cap if capacity else None,
        ) for index, member in enumerate(("member-a", "member-b"))),
        replica_identity=ReplicaIdentity(
            "revision-private", "engine-private", "sha256:" + "1" * 64,
            "sha256:" + "2" * 64,
        ),
        max_output_tokens=8 if mode == "clamped" else None,
        replica_strategy="capacity" if capacity else "round_robin",
    )
    config = replace(config, tiers=(tier,))
    members = {"member-a": _DecisionMember(mode), "member-b": _DecisionMember("success")}
    readiness = _DecisionReadiness()
    path = tmp_path / "replica-decisions.jsonl"
    log = DecisionLog(sink=DecisionLogWriter(str(path)))
    routing = RoutingBackend(
        config, {tier.id: ReplicaRuntime(members)}, availability=readiness, decision_log=log,
        capacity_metrics=(
            (lambda _tier: MetricsSnapshot("unavailable", {}, "metrics_missing"))
            if capacity else None
        ),
    )
    request = InternalRequest(
        model="llm.primary", messages=[Message("user", "prompt-secret")],
        max_tokens=16, raw={"max_tokens": 16},
    )
    return routing, log, path, request, members, readiness


@pytest.mark.parametrize("mode,reason,served", [
    ("success", "served", True), ("clamped", "served_output_clamped", True),
    ("eager", "backend_error", False), ("first_stream", "backend_error", False),
    ("mid_stream", "backend_error", False), ("completion", "completion_error", False),
    ("close_before", "client_disconnected", False), ("close_after", "client_disconnected", False),
])
def test_replica_terminal_paths_record_exactly_one_member_attempt(tmp_path, mode, reason, served):
    routing, log, path, request, members, readiness = _replica_decisions(tmp_path, mode)
    if mode in {"eager", "first_stream", "mid_stream", "completion"}:
        with pytest.raises(_PrivateProviderError):
            list(routing.generate(request))
    else:
        stream = routing.generate(request)
        if mode == "close_after":
            assert next(stream) == "response-secret"
        if mode.startswith("close_"):
            stream.close()
            stream.close()  # idempotent terminal recording and release
        else:
            assert list(stream) == ["response-secret"]
    assert len(log) == 1
    record = log.last
    assert record.requested_tier == "primary"
    assert record.replica_member_id == "member-a"
    assert record.replica_selection == "identity_passed"
    assert len(record.attempts) == 1
    assert record.attempts[0].tier_id == "primary"
    assert record.attempts[0].reason == reason
    assert record.attempts[0].succeeded is served
    assert record.served_tier == ("primary" if served else None)
    assert members["member-a"].calls == 1
    assert members["member-b"].calls == 0
    assert readiness.calls == ["member-a", "member-b"]
    assert routing._admission.snapshot("primary").active_requests == 0
    wire = path.read_text()
    assert len(wire.splitlines()) == 1
    combined = repr(record) + repr(log.summary()) + decision_line(record) + wire
    for private in (
        "prompt-secret", "response-secret", "provider-secret", "_PrivateProviderError",
        "http://", "127.0.0.1", "100.64.0.10", "ANVIL_PRIMARY_KEY",
        "model-private", "unexpected-private", "revision-private", "engine-private",
    ):
        assert private not in combined


@pytest.mark.parametrize("mode,selection,probes", [
    ("over_context", "request_rejected", []),
    ("backend_unbound", "request_rejected", []),
    ("unavailable", "not_admitted", ["member-a", "member-b"]),
    ("quiesced", "not_admitted", ["member-a", "member-b"]),
])
def test_replica_preselection_refusal_has_no_attempt_or_member(tmp_path, mode, selection, probes):
    routing, log, path, request, members, readiness = _replica_decisions(tmp_path)
    if mode == "over_context":
        request.messages = [Message("user", "x" * 20000)]
    elif mode == "backend_unbound":
        routing._backends.clear()
    elif mode == "unavailable":
        readiness.available = False
    else:
        routing._admission.quiesce("primary", "maintenance")
    with pytest.raises(NoAvailableTierError):
        list(routing.generate(request))
    assert len(log) == 1
    assert log.last.replica_member_id is None
    assert log.last.replica_selection == selection
    assert log.last.attempts == ()
    assert readiness.calls == probes
    assert all(member.calls == 0 for member in members.values())
    assert "replica_member_id" not in path.read_text()


def test_router_does_not_stamp_an_undeclared_member_or_caller_metadata(tmp_path):
    routing, log, _, request, _, _ = _replica_decisions(tmp_path)
    request.raw.update(replica_member_id="member-b", replica_selection="identity_passed")
    assert list(routing.generate(request)) == ["response-secret"]
    assert log.last.replica_member_id == "member-a"
    routing._record(request, routing._config.tiers[0], served=False, reason="backend_error",
                    replica_member_id="not-declared")
    assert log.last.replica_member_id is None
    assert log.last.replica_selection == "request_rejected"
    assert log.last.attempts == ()


@pytest.mark.parametrize("mode", ["success", "eager", "first_stream", "mid_stream", "close_before", "close_after"])
def test_capacity_replica_terminal_records_retain_one_pre_reservation_decision(tmp_path, mode):
    routing, log, _, request, members, _ = _replica_decisions(
        tmp_path, mode, capacity=True,
    )
    if mode in {"eager", "first_stream", "mid_stream"}:
        with pytest.raises(_PrivateProviderError):
            list(routing.generate(request))
    elif mode == "close_before":
        stream = routing.generate(request)
        stream.close()
    elif mode == "close_after":
        stream = routing.generate(request)
        assert next(stream) == "response-secret"
        stream.close()
    else:
        assert list(routing.generate(request)) == ["response-secret"]
    record = log.last
    decision = record.replica_scheduler
    assert decision is not None
    assert decision.reason is ReplicaDecisionReason.SELECTED
    assert decision.selected_member_id == record.replica_member_id == "member-a"
    assert decision.scores[0].local_numerator == 0
    assert decision.scores[0].local_denominator == 2
    assert members["member-a"].calls == 1
    assert members["member-b"].calls == 0


def test_capacity_exhaustion_and_semantic_refusal_do_not_fabricate_scheduler_evidence(tmp_path):
    routing, log, _, request, members, _ = _replica_decisions(
        tmp_path, capacity=True, capacity_cap=1,
    )
    first = routing.generate(request)
    second = routing.generate(request)
    assert next(first) == "response-secret"
    assert next(second) == "response-secret"
    try:
        with pytest.raises(NoAvailableTierError):
            list(routing.generate(request))
        exhausted = log.last
        assert exhausted.replica_selection == "not_admitted"
        assert exhausted.replica_scheduler is None
        assert exhausted.attempts == ()
        assert sum(member.calls for member in members.values()) == 2
    finally:
        first.close()
        second.close()

    semantic_request = InternalRequest(
        model="llm.primary", messages=[Message("user", "x" * 20_000)], raw={},
    )
    with pytest.raises(NoAvailableTierError):
        list(routing.generate(semantic_request))
    assert log.last.replica_selection == "request_rejected"
    assert log.last.replica_scheduler is None


def test_capacity_scheduler_ignores_caller_supplied_raw_spoofing(tmp_path):
    routing, log, _, request, _, _ = _replica_decisions(tmp_path, capacity=True)
    request.raw["replica_scheduler"] = {"private-token": "http://100.64.0.10"}
    assert list(routing.generate(request)) == ["response-secret"]
    assert log.last.replica_scheduler is not None
    rendered = repr(log.last) + repr(log.summary()) + decision_line(log.last)
    assert "private-token" not in rendered
    assert "100.64.0.10" not in rendered
