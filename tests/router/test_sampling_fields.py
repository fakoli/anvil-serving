"""Sampling normalization, dialect forwarding, and tier override precedence."""
from dataclasses import replace

import pytest

from anvil_serving.router.backends.relay import RelayBackend
from anvil_serving.router.dialects.anthropic import AnthropicDialect
from anvil_serving.router.dialects.openai import OpenAIDialect
from tests.router.helpers import make_tier


def _request(dialect="openai", **fields):
    parser = OpenAIDialect() if dialect == "openai" else AnthropicDialect()
    return parser.parse_request({
        "model": "chat", "max_tokens": 64,
        "messages": [{"role": "user", "content": "hi"}], **fields,
    })


def _body(request, dialect="openai", **overrides):
    tier = replace(make_tier(dialect), **overrides)
    return RelayBackend(tier, env={"EXAMPLE_KEY": "k"})._build_body(request)


@pytest.mark.parametrize("source, stop_field, stop, expected", [
    ("openai", "stop", "STOP", ["STOP"]),
    ("openai", "stop", ["STOP", "END"], ["STOP", "END"]),
    ("anthropic", "stop_sequences", ["STOP", "END"], ["STOP", "END"]),
])
@pytest.mark.parametrize("target, wire_field, other_field", [
    ("openai", "stop", "stop_sequences"),
    ("anthropic", "stop_sequences", "stop"),
])
def test_sampling_fields_parse_and_relay(source, stop_field, stop, expected,
                                       target, wire_field, other_field):
    request = _request(source, top_p=0.7, **{stop_field: stop})
    assert request.top_p == 0.7
    assert request.stop == expected
    body = _body(request, target)
    assert body["top_p"] == 0.7
    assert body[wire_field] == expected
    assert other_field not in body


@pytest.mark.parametrize("defaults, request_fields, overrides, expected", [
    ({"reasoning_effort": "high"}, {}, {}, {"reasoning_effort": "high"}),
    ({"reasoning_effort": "high"}, {"reasoning_effort": "low"}, {},
     {"reasoning_effort": "low"}),
    ({}, {"reasoning_effort": "low"}, {"reasoning_effort": "high"},
     {"reasoning_effort": "high"}),
    ({"reasoning_effort": "low"}, {}, {"reasoning_effort": "high"},
     {"reasoning_effort": "high"}),
    ({}, {"chat_template_kwargs": {"enable_thinking": False}},
     {"chat_template_kwargs": {"enable_thinking": True}},
     {"chat_template_kwargs": {"enable_thinking": True}}),
    ({}, {"top_p": 0.9}, {"top_p": 0.1}, {"top_p": 0.1}),
])
def test_hard_override_beats_request_which_beats_soft_default(
    defaults, request_fields, overrides, expected,
):
    body = _body(_request(**request_fields), extra_body_defaults=defaults, extra_body=overrides)
    assert {key: body[key] for key in expected} == expected


def test_account_and_session_fields_never_forwarded():
    fields = {"logit_bias": {"123": -100}, "seed": 42, "user": "user-abc", "metadata": {"foo": "bar"}}
    assert fields.keys().isdisjoint(_body(_request(**fields)))
