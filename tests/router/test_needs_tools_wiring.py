"""``policy.Needs.needs_tools`` wired on the serve path (fx-sampling gap 2).

``policy.route()`` has always honored ``needs.needs_tools`` (drops a
``tool_support=false`` tier), but before this fix ``serve.RoutingBackend``
never constructed a ``Needs`` at all -- ``route()`` was always called with
``needs=None``, so the hard constraint was dead code on the actual serve path:
a tools-bearing request could still land on a tier with no tool support.

This wires ``RoutingBackend.generate`` / ``.decide`` to build a ``Needs`` from
the request via ``dialects.translate.has_tool_artifacts`` (#96) -- a request
carrying ``tools``, ``tool_choice``, or tool_use/tool_result history sets
``needs_tools=True`` and so is excluded from any ``tool_support=false`` tier,
regardless of cost order.

Hermetic; ``StaticBackend`` stubs stand in for real tiers (no network). Uses a
custom (non-taxonomy) preset name so ``work_class`` resolves to ``None`` and
the quality-gate deny filter is skipped -- isolating the ``needs_tools``
hard-constraint behaviour from profile-store state.
"""
from __future__ import annotations

from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.config import RouterConfig, Tier
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.profile_store import default_profile
from anvil_serving.router.serve import RoutingBackend

CHEAP_NO_TOOLS = "cheap-no-tools"
EXPENSIVE_TOOLS = "expensive-tools"


def _tier(tier_id: str, *, tool_support: bool) -> Tier:
    return Tier(
        id=tier_id,
        base_url=f"http://127.0.0.1:9/{tier_id}",
        dialect="openai",
        context_limit=32768,
        privacy="local",
        tool_support=tool_support,
        auth_env=f"{tier_id.upper().replace('-', '_')}_KEY",
    )


def _config() -> RouterConfig:
    tiers = (
        _tier(CHEAP_NO_TOOLS, tool_support=False),
        _tier(EXPENSIVE_TOOLS, tool_support=True),
    )
    return RouterConfig(
        tiers=tiers,
        # Cost order deliberately puts the cheap/no-tools tier FIRST so a
        # tools-bearing request routing to the expensive tier proves the
        # needs_tools constraint actually excluded the cheap one (rather than
        # cost order coincidentally picking the right tier).
        presets={"chat-custom": (CHEAP_NO_TOOLS, EXPENSIVE_TOOLS)},
        mapping_version="test.0",
    )


def _routing_backend() -> RoutingBackend:
    backends = {
        CHEAP_NO_TOOLS: StaticBackend(["cheap-served"]),
        EXPENSIVE_TOOLS: StaticBackend(["expensive-served"]),
    }
    return RoutingBackend(_config(), backends, default_profile())


def _tool_free_request() -> InternalRequest:
    return InternalRequest(
        model="chat-custom",
        messages=[Message("user", "hi")],
        raw={"model": "chat-custom", "messages": [{"role": "user", "content": "hi"}]},
        dialect="openai",
    )


def _tools_bearing_request() -> InternalRequest:
    raw = {
        "model": "chat-custom",
        "messages": [{"role": "user", "content": "what's the weather?"}],
        "tools": [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }],
    }
    return InternalRequest(
        model="chat-custom",
        messages=[Message("user", "what's the weather?")],
        raw=raw,
        dialect="openai",
    )


def test_tool_free_request_routes_to_cheap_tier():
    rb = _routing_backend()
    text = "".join(rb.generate(_tool_free_request()))
    assert text == "cheap-served"


def test_tools_bearing_request_excludes_no_tool_support_tier():
    rb = _routing_backend()
    text = "".join(rb.generate(_tools_bearing_request()))
    assert text == "expensive-served"


def test_decide_endpoint_also_excludes_no_tool_support_tier_for_tools_request():
    """``decide()`` (the /v1/route decision endpoint) mirrors generate()'s
    needs_tools constraint -- it must not merely be a generate()-only special case."""
    rb = _routing_backend()
    result = rb.decide(_tools_bearing_request())
    assert result["provider"] == EXPENSIVE_TOOLS


def test_decide_endpoint_tool_free_still_picks_cheap_tier():
    rb = _routing_backend()
    result = rb.decide(_tool_free_request())
    assert result["provider"] == CHEAP_NO_TOOLS


def test_tool_history_without_tools_array_also_excludes_no_tool_support_tier():
    """A follow-up turn carrying tool_use/tool_result history (but no fresh
    ``tools`` array on this particular request) must still be treated as
    tools-bearing -- has_tool_artifacts() detects the history shape too."""
    raw = {
        "model": "chat-custom",
        "messages": [
            {"role": "user", "content": "weather?"},
            {
                "role": "assistant",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
        ],
    }
    request = InternalRequest(
        model="chat-custom",
        messages=[Message("user", "weather?")],
        raw=raw,
        dialect="openai",
    )
    rb = _routing_backend()
    text = "".join(rb.generate(request))
    assert text == "expensive-served"
