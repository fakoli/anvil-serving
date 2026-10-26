"""Prepare numeric-budget wire controls without opening a model connection."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from anvil_serving.router.backends.relay import RelayBackend
from anvil_serving.router.config import load
from anvil_serving.router.dialects.openai import OpenAIDialect
from anvil_serving.router.serve import RoutingBackend


ROOT = Path(__file__).resolve().parents[2]
PACKET = json.loads((ROOT / "docs/benchmarks/reasoning-limit-probe.json").read_text())


@pytest.mark.parametrize("isolated", [False, True])
@pytest.mark.parametrize("case", PACKET["cases"], ids=lambda case: case["id"])
def test_probe_wire_controls_require_isolated_configuration(isolated, case):
    captured = []

    def transport(url, *, data, headers, timeout):
        captured.append((url, json.loads(data)))
        return json.dumps({"choices": [{"index": 0, "message": {
            "role": "assistant", "content": "offline fixture"}, "finish_reason": "stop"}]}).encode()

    configuration = load(ROOT / "configs/example.toml")
    common = dict(PACKET["isolated_tier_extra_body"])
    shared_controls = {**common, "thinking_token_budget": 49152}
    expected_controls = json.loads(json.dumps(common if isolated else shared_controls))
    original_packet = json.dumps(PACKET, sort_keys=True)
    shared_tier = replace(next(tier for tier in configuration.tiers if tier.id == "primary-local"),
                          base_url="http://127.0.0.1:30000/v1", model="diagnostic-model",
                          max_output_tokens=65536, extra_body_defaults=None,
                          extra_body=shared_controls)
    tier = replace(shared_tier, extra_body=common) if isolated else shared_tier
    configuration = replace(configuration, tiers=tuple(
        tier if entry.id == tier.id else entry for entry in configuration.tiers))
    relay = RelayBackend(tier, env={}, transport=transport)
    routing = RoutingBackend(configuration, {tier.id: relay})
    request_body = {**PACKET["request_common"], "thinking_token_budget": 64,
                    "messages": [{"role": "user", "content": case["prompt"]}]}
    assert list(routing.generate(OpenAIDialect().parse_request(request_body)))
    assert len(captured) == 1
    url, body = captured[0]
    assert url == "http://127.0.0.1:30000/v1/chat/completions"
    assert body["model"] == "diagnostic-model"
    assert body["messages"] == request_body["messages"]
    assert body["max_tokens"] == 512
    assert {key: body[key] for key in expected_controls} == expected_controls
    assert tier.extra_body == expected_controls
    assert shared_tier.extra_body == {**expected_controls, "thinking_token_budget": 49152}
    assert common == PACKET["isolated_tier_extra_body"]
    assert json.dumps(PACKET, sort_keys=True) == original_packet
    assert "chat_template_kwargs" not in body
    assert PACKET["execution_authorized"] is False
    assert PACKET["live_identity_verified"] is False
    assert PACKET["status"] == "prepared_not_executed"
    assert PACKET["ignis"] == "hold"
    assert PACKET["limits"] == {"concurrency": 1, "maximum_requests": 2,
                                "request_timeout_s": 120, "total_window_s": 300, "retries": 0}
    first, second, total = case["expected_integers"]
    assert first + second == total
