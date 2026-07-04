"""Sampling-field wire fidelity (fx-sampling gap 1).

Before this fix ``InternalRequest`` carried only ``max_tokens`` / ``temperature``
/ ``stream``: a harness sending ``top_p`` or ``stop`` (OpenAI) / ``stop_sequences``
(Anthropic) silently lost them on the way to a cloud/relay backend -- the served
model sampled with different parameters than the harness asked for. These tests
pin:

* both dialects parse ``top_p`` and stop-sequence fields into
  ``InternalRequest.top_p`` / ``InternalRequest.stop`` (normalized to a list);
* OpenAI's string-or-array ``stop`` form is normalized correctly;
* ``_build_body`` forwards them with dialect-correct wire names, only when
  present -- an absent field must build the exact same body as before (the
  #96 byte-identical regression pin, extended here);
* same-dialect-only forwarding for ``top_k`` (Anthropic) and
  ``presence_penalty`` / ``frequency_penalty`` (OpenAI) -- never invented for a
  translated cross-dialect request;
* a tier's ``extra_body`` (applied LAST) overrides a request's ``top_p``
  (documented precedence, #97).
"""
from __future__ import annotations

from anvil_serving.router.backends.cloud import CloudBackend
from anvil_serving.router.config import Tier
from anvil_serving.router.dialects.anthropic import AnthropicDialect
from anvil_serving.router.dialects.openai import OpenAIDialect
from anvil_serving.router.internal import normalize_stop


def _tier(dialect: str, **overrides) -> Tier:
    base = dict(
        id=f"{dialect}-tier",
        base_url="https://api.example.test",
        dialect=dialect,
        context_limit=200_000,
        privacy="cloud",
        tool_support=True,
        auth_env="EXAMPLE_KEY",
        model="concrete-model",
    )
    base.update(overrides)
    return Tier(**base)


def _backend(dialect: str, **tier_overrides) -> CloudBackend:
    return CloudBackend(_tier(dialect, **tier_overrides), env={"EXAMPLE_KEY": "k"})


# --------------------------------------------------------------------------- #
# normalize_stop
# --------------------------------------------------------------------------- #
def test_normalize_stop_none_is_none():
    assert normalize_stop(None) is None


def test_normalize_stop_string_becomes_single_item_list():
    assert normalize_stop("STOP") == ["STOP"]


def test_normalize_stop_empty_string_is_none():
    assert normalize_stop("") is None


def test_normalize_stop_list_passthrough():
    assert normalize_stop(["a", "b"]) == ["a", "b"]


def test_normalize_stop_empty_list_is_none():
    assert normalize_stop([]) is None


def test_normalize_stop_filters_non_string_entries():
    assert normalize_stop(["a", 1, None, "b"]) == ["a", "b"]


def test_normalize_stop_wrong_type_is_none():
    assert normalize_stop(42) is None


# --------------------------------------------------------------------------- #
# dialect parsing
# --------------------------------------------------------------------------- #
def test_openai_parse_top_p():
    req = OpenAIDialect().parse_request(
        {"model": "chat", "messages": [{"role": "user", "content": "hi"}], "top_p": 0.9}
    )
    assert req.top_p == 0.9


def test_openai_parse_stop_string_form():
    req = OpenAIDialect().parse_request(
        {"model": "chat", "messages": [{"role": "user", "content": "hi"}], "stop": "STOP"}
    )
    assert req.stop == ["STOP"]


def test_openai_parse_stop_array_form():
    req = OpenAIDialect().parse_request(
        {
            "model": "chat",
            "messages": [{"role": "user", "content": "hi"}],
            "stop": ["STOP", "END"],
        }
    )
    assert req.stop == ["STOP", "END"]


def test_openai_parse_absent_sampling_fields_are_none():
    req = OpenAIDialect().parse_request(
        {"model": "chat", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert req.top_p is None
    assert req.stop is None


def test_anthropic_parse_top_p():
    req = AnthropicDialect().parse_request(
        {
            "model": "claude",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
            "top_p": 0.5,
        }
    )
    assert req.top_p == 0.5


def test_anthropic_parse_stop_sequences():
    req = AnthropicDialect().parse_request(
        {
            "model": "claude",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
            "stop_sequences": ["STOP", "END"],
        }
    )
    assert req.stop == ["STOP", "END"]


def test_anthropic_parse_absent_sampling_fields_are_none():
    req = AnthropicDialect().parse_request(
        {
            "model": "claude",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
        }
    )
    assert req.top_p is None
    assert req.stop is None


# --------------------------------------------------------------------------- #
# _build_body forwarding -- dialect-correct names
# --------------------------------------------------------------------------- #
def test_build_body_forwards_top_p_and_stop_openai_to_openai():
    req = OpenAIDialect().parse_request(
        {
            "model": "chat",
            "messages": [{"role": "user", "content": "hi"}],
            "top_p": 0.7,
            "stop": ["STOP"],
        }
    )
    body = _backend("openai")._build_body(req)
    assert body["top_p"] == 0.7
    assert body["stop"] == ["STOP"]


def test_build_body_forwards_top_p_and_stop_anthropic_to_anthropic():
    req = AnthropicDialect().parse_request(
        {
            "model": "claude",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
            "top_p": 0.7,
            "stop_sequences": ["STOP"],
        }
    )
    body = _backend("anthropic")._build_body(req)
    assert body["top_p"] == 0.7
    assert body["stop_sequences"] == ["STOP"]
    assert "stop" not in body


def test_build_body_forwards_stop_openai_to_anthropic_dialect_name():
    """Cross-dialect: an OpenAI-origin request routed to an Anthropic tier still
    gets the Anthropic wire name (stop_sequences), never the OpenAI one."""
    req = OpenAIDialect().parse_request(
        {
            "model": "chat",
            "messages": [{"role": "user", "content": "hi"}],
            "stop": ["STOP"],
        }
    )
    body = _backend("anthropic")._build_body(req)
    assert body["stop_sequences"] == ["STOP"]
    assert "stop" not in body


def test_build_body_forwards_stop_anthropic_to_openai_dialect_name():
    """Cross-dialect: an Anthropic-origin request routed to an OpenAI tier gets
    the OpenAI wire name (stop), never stop_sequences."""
    req = AnthropicDialect().parse_request(
        {
            "model": "claude",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
            "stop_sequences": ["STOP"],
        }
    )
    body = _backend("openai")._build_body(req)
    assert body["stop"] == ["STOP"]
    assert "stop_sequences" not in body


def test_build_body_absent_sampling_fields_are_omitted():
    req = OpenAIDialect().parse_request(
        {"model": "chat", "messages": [{"role": "user", "content": "hi"}]}
    )
    body = _backend("openai")._build_body(req)
    assert "top_p" not in body
    assert "stop" not in body


# --------------------------------------------------------------------------- #
# same-dialect-only forwarding: top_k (anthropic), penalties (openai)
# --------------------------------------------------------------------------- #
def test_top_k_forwarded_anthropic_to_anthropic():
    req = AnthropicDialect().parse_request(
        {
            "model": "claude",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
            "top_k": 40,
        }
    )
    body = _backend("anthropic")._build_body(req)
    assert body["top_k"] == 40


def test_top_k_not_invented_for_openai_origin_request():
    """An OpenAI-origin request has no top_k concept; routing it to an Anthropic
    tier must NOT invent a top_k value out of thin air."""
    req = OpenAIDialect().parse_request(
        {"model": "chat", "messages": [{"role": "user", "content": "hi"}]}
    )
    body = _backend("anthropic")._build_body(req)
    assert "top_k" not in body


def test_penalties_forwarded_openai_to_openai():
    req = OpenAIDialect().parse_request(
        {
            "model": "chat",
            "messages": [{"role": "user", "content": "hi"}],
            "presence_penalty": 0.2,
            "frequency_penalty": 0.3,
        }
    )
    body = _backend("openai")._build_body(req)
    assert body["presence_penalty"] == 0.2
    assert body["frequency_penalty"] == 0.3


def test_penalties_not_invented_for_anthropic_origin_request():
    """An Anthropic-origin request has no penalty concept; routing it to an
    OpenAI tier must NOT invent presence/frequency_penalty values."""
    req = AnthropicDialect().parse_request(
        {
            "model": "claude",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
        }
    )
    body = _backend("openai")._build_body(req)
    assert "presence_penalty" not in body
    assert "frequency_penalty" not in body


# --------------------------------------------------------------------------- #
# reasoning_effort passthrough + extra_body_defaults (per-request reasoning)
# --------------------------------------------------------------------------- #
def _openai_req(**extra):
    return OpenAIDialect().parse_request(
        {"model": "chat", "messages": [{"role": "user", "content": "hi"}], **extra})


def test_reasoning_effort_forwarded_openai_to_openai():
    # OpenClaw's per-message reasoning selector arrives as reasoning_effort -> forwarded verbatim.
    body = _backend("openai")._build_body(_openai_req(reasoning_effort="medium"))
    assert body["reasoning_effort"] == "medium"


def test_reasoning_effort_not_invented_for_anthropic_origin():
    req = AnthropicDialect().parse_request(
        {"model": "claude", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]})
    assert "reasoning_effort" not in _backend("openai")._build_body(req)


def test_extra_body_defaults_fills_when_request_absent():
    # tier soft-default applies only when the caller didn't set it.
    body = _backend("openai", extra_body_defaults={"reasoning_effort": "high"})._build_body(_openai_req())
    assert body["reasoning_effort"] == "high"


def test_request_reasoning_effort_overrides_soft_default():
    body = _backend("openai", extra_body_defaults={"reasoning_effort": "high"})._build_body(
        _openai_req(reasoning_effort="low"))
    assert body["reasoning_effort"] == "low"          # request wins over the SOFT default


def test_hard_extra_body_still_wins_over_request():
    # the hard-override contract is preserved: extra_body clobbers even a request value.
    body = _backend("openai", extra_body={"reasoning_effort": "high"})._build_body(
        _openai_req(reasoning_effort="low"))
    assert body["reasoning_effort"] == "high"


def test_extra_body_both_hard_wins_over_soft_default():
    body = _backend("openai", extra_body={"reasoning_effort": "high"},
                    extra_body_defaults={"reasoning_effort": "low"})._build_body(_openai_req())
    assert body["reasoning_effort"] == "high"          # hard extra_body wins over the soft default


def test_no_reasoning_config_no_key():
    assert "reasoning_effort" not in _backend("openai")._build_body(_openai_req())


def test_logit_bias_seed_user_metadata_never_forwarded():
    """Deliberately-excluded provider-account/session-scoped fields never leak
    into the upstream body even when the caller sends them."""
    req = OpenAIDialect().parse_request(
        {
            "model": "chat",
            "messages": [{"role": "user", "content": "hi"}],
            "logit_bias": {"123": -100},
            "seed": 42,
            "user": "user-abc",
            "metadata": {"foo": "bar"},
        }
    )
    body = _backend("openai")._build_body(req)
    for key in ("logit_bias", "seed", "user", "metadata"):
        assert key not in body


# --------------------------------------------------------------------------- #
# regression pin: absent sampling fields build the exact old tool-free body
# --------------------------------------------------------------------------- #
def test_tool_and_sampling_free_request_body_is_still_unchanged():
    body_in = {
        "model": "chat",
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hello there"},
        ],
        "temperature": 0.5,
        "max_tokens": 64,
    }
    request = OpenAIDialect().parse_request(body_in)
    body = _backend("openai")._build_body(request)
    assert body == {
        "model": "concrete-model",
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hello there"},
        ],
        "stream": False,
        "max_tokens": 64,
        "temperature": 0.5,
    }


# --------------------------------------------------------------------------- #
# extra_body precedence: tier extra_body beats request top_p (#97 contract)
# --------------------------------------------------------------------------- #
def test_tier_extra_body_top_p_overrides_request_top_p():
    req = OpenAIDialect().parse_request(
        {
            "model": "chat",
            "messages": [{"role": "user", "content": "hi"}],
            "top_p": 0.9,
        }
    )
    backend = _backend("openai", extra_body={"top_p": 0.1})
    body = backend._build_body(req)
    assert body["top_p"] == 0.1


def test_tier_extra_body_stop_sequences_overrides_request_stop_anthropic():
    req = AnthropicDialect().parse_request(
        {
            "model": "claude",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
            "stop_sequences": ["REQUEST_STOP"],
        }
    )
    backend = _backend("anthropic", extra_body={"stop_sequences": ["TIER_STOP"]})
    body = backend._build_body(req)
    assert body["stop_sequences"] == ["TIER_STOP"]
