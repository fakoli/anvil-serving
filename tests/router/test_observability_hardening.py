"""ADR-0033 observability hardening: mid-stream honesty, inline-probe
single-flight, restart-detectable metrics, and the honest token estimator."""
from __future__ import annotations

import http.client
import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from anvil_serving.router.availability import AvailabilityResult, HttpHealthAvailability
from anvil_serving.router.config import ReplicaIdentity, ReplicaMember, load
from anvil_serving.router.decision_log import DecisionLog, DecisionLogWriter
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.model_capacity import MetricsSnapshot
from anvil_serving.router.serve import ReplicaRuntime, RoutingBackend, build_server


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
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
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


# --- 4C: restart-detectable metrics ------------------------------------------


# --- 4D: honest token estimator ----------------------------------------------


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
        if self.available:
            return AvailabilityResult(
                True, "ready", "identity_passed", tier.model, tier.model
            )
        return AvailabilityResult(
            False, "unavailable", "identity_mismatch", tier.model, None
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
