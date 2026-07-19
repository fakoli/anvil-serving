"""Tool-name and tool-choice enforcement on the routed serve path.

Regression coverage for an OpenClaw/Anvil incident where the local chat tier
emitted syntactically valid calls named ``open_file`` and bare ``functions``
even though neither name appeared in OpenClaw's advertised tool catalog.
"""

from __future__ import annotations

import pytest

from anvil_serving.router.config import RouterConfig, Tier
from anvil_serving.router.internal import (
    InternalRequest,
    Message,
    NoAvailableTierError,
    StructuredResult,
)
from anvil_serving.router.profile_store import default_profile
from anvil_serving.router.serve import RoutingBackend
from anvil_serving.router.verify import (
    ResponseView,
    ToolCallContractValid,
    ToolCallJSONValid,
)


def _openai_raw(*, tool_choice="auto"):
    return {
        "model": "chat-custom",
        "messages": [{"role": "user", "content": "fetch it"}],
        "tools": [{
            "type": "function",
            "function": {
                "name": "web_fetch",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
        "tool_choice": tool_choice,
    }


def test_contract_accepts_exact_advertised_openai_tool_name():
    verifier = ToolCallContractValid.from_request_raw(_openai_raw())
    result = verifier.verify(ResponseView(
        tool_calls=[{"name": "web_fetch", "arguments": "{}"}],
    ))
    assert result.passed


@pytest.mark.parametrize("bad_name", ["open_file", "functions", "functions.web_fetch"])
def test_contract_rejects_unadvertised_tool_names(bad_name):
    verifier = ToolCallContractValid.from_request_raw(_openai_raw())
    result = verifier.verify(ResponseView(
        tool_calls=[{"name": bad_name, "arguments": "{}"}],
    ))
    assert not result.passed
    assert "not advertised" in result.reason


def test_contract_enforces_required_none_and_specific_choices():
    required = ToolCallContractValid.from_request_raw(
        _openai_raw(tool_choice="required")
    )
    assert not required.verify(ResponseView(text="I fetched it")).passed

    forbidden = ToolCallContractValid.from_request_raw(
        _openai_raw(tool_choice="none")
    )
    assert not forbidden.verify(ResponseView(
        tool_calls=[{"name": "web_fetch", "arguments": "{}"}],
    )).passed

    specific_choice = {
        "type": "function",
        "function": {"name": "web_fetch"},
    }
    specific = ToolCallContractValid.from_request_raw(
        _openai_raw(tool_choice=specific_choice)
    )
    assert not specific.verify(ResponseView(
        tool_calls=[{"name": "other", "arguments": "{}"}],
    )).passed


def test_contract_understands_anthropic_catalog_and_choice():
    verifier = ToolCallContractValid.from_request_raw({
        "tools": [{"name": "web_fetch", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "tool", "name": "web_fetch"},
    })
    assert verifier.verify(ResponseView(
        tool_calls=[{"name": "web_fetch", "arguments": {}}],
    )).passed
    assert not verifier.verify(ResponseView(text="pretended result")).passed


@pytest.mark.parametrize(
    "raw",
    [
        {
            "tools": [{
                "type": "function",
                "function": {
                    "name": "web_fetch",
                    "parameters": {
                        "type": "object",
                        "required": ["url"],
                    },
                },
            }],
        },
        {
            "tools": [{
                "name": "web_fetch",
                "input_schema": {
                    "type": "object",
                    "required": ["url"],
                },
            }],
        },
    ],
)
def test_json_verifier_derives_required_keys_from_both_dialects(raw):
    verifier = ToolCallJSONValid.from_request_raw(raw)
    assert verifier.verify(ResponseView(
        tool_calls=[{"name": "web_fetch", "arguments": '{"url":"https://x"}'}],
    )).passed
    missing = verifier.verify(ResponseView(
        tool_calls=[{"name": "web_fetch", "arguments": "{}"}],
    ))
    assert not missing.passed
    assert "url" in missing.reason


class _ToolBackend:
    def __init__(self, name: str):
        self.name = name

    def generate(self, request):
        yield ""

    def get_last_structured(self):
        return StructuredResult(
            finish_reason="tool_calls",
            tool_calls=[{"name": self.name, "arguments": "{}"}],
        )


class _TextBackend:
    def generate(self, request):
        yield "pretended tool success"

    def get_last_structured(self):
        return StructuredResult(finish_reason="stop")


def _tier(name: str) -> Tier:
    return Tier(
        id=name,
        base_url=f"http://127.0.0.1:9/{name}",
        dialect="openai",
        context_limit=32768,
        privacy="local",
        tool_support=True,
        auth_env=f"{name.upper().replace('-', '_')}_KEY",
    )


def _request(*, tool_choice="auto") -> InternalRequest:
    raw = _openai_raw(tool_choice=tool_choice)
    return InternalRequest(
        model="chat-custom",
        messages=[Message("user", "fetch it")],
        raw=raw,
        dialect="openai",
    )


def test_routing_discards_unknown_tool_and_falls_back_to_valid_tier():
    config = RouterConfig(
        tiers=(_tier("fast"), _tier("heavy")),
        presets={"chat-custom": ("fast", "heavy")},
        mapping_version="test.0",
    )
    routing = RoutingBackend(
        config,
        {"fast": _ToolBackend("open_file"), "heavy": _ToolBackend("web_fetch")},
        default_profile(),
    )

    assert "".join(routing.generate(_request())) == ""
    structured = routing.get_last_structured()
    assert structured is not None
    assert structured.tool_calls[0]["name"] == "web_fetch"
    record = routing._decision_log.last
    assert record is not None and record.fell_back
    assert record.served_tier == "heavy"
    assert "tool_call_contract_valid" in record.attempts[0].verify_reason


def test_required_tool_choice_rejects_fabricated_text_success():
    config = RouterConfig(
        tiers=(_tier("fast"),),
        presets={"chat-custom": ("fast",)},
        mapping_version="test.0",
    )
    routing = RoutingBackend(config, {"fast": _TextBackend()}, default_profile())

    with pytest.raises(NoAvailableTierError) as exc:
        list(routing.generate(_request(tool_choice="required")))
    assert exc.value.kind == "exhausted"
    record = routing._decision_log.last
    assert record is not None
    assert record.attempts[0].verify_reason == "tool_call_contract_valid"
