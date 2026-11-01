"""OpenAI-compatible reasoning aliases share one response and history contract."""
import json

import pytest

from anvil_serving.router.backends.relay import RelayBackend
from anvil_serving.router.dialects.openai import OpenAIDialect
from anvil_serving.router.internal import ModelDelta
from tests.router.helpers import make_tier
from tests.router.test_reasoning_content import _parse_openai_sse, _request
from tests.router.test_streaming_relay import FakeStreamTransport, _openai_sse


@pytest.mark.parametrize("mode", ["buffered", "sse", "json-stream-fallback"])
@pytest.mark.parametrize(("fields", "expected"), [
    ({"reasoning_content": "Canonical"}, "Canonical"),
    ({"reasoning": "Alternate"}, "Alternate"),
    ({"reasoning_text": "Text alias"}, "Text alias"),
    ({"reasoning_text": "Third", "reasoning": "Second", "reasoning_content": "First"},
     "First"),
    ({"reasoning_content": "", "reasoning": "Second", "reasoning_text": "Third"},
     "Second"),
    ({"reasoning_content": {"opaque": True}, "reasoning": [], "reasoning_text": "Third"},
     "Third"),
    ({"reasoning_content": None, "reasoning": False, "reasoning_text": 42}, None),
    ({"reasoning_content": " ", "reasoning": "Do not trim stream whitespace"}, " "),
])
def test_response_aliases_emit_one_canonical_field(mode, fields, expected):
    message = {"content": "Visible answer", **fields}
    upstream = json.dumps({"choices": [{"message": message, "finish_reason": "stop"}]}).encode()
    backend_kwargs = {}
    if mode == "buffered":
        backend_kwargs["transport"] = lambda url, **kwargs: upstream
    else:
        payload = (_openai_sse({"choices": [{"delta": message, "finish_reason": "stop"}]})
                   if mode == "sse" else upstream)
        backend_kwargs["stream_transport"] = FakeStreamTransport(
            payload, "text/event-stream" if mode == "sse" else "application/json")
    backend = RelayBackend(make_tier("openai"), env={"EXAMPLE_KEY": "test"}, **backend_kwargs)
    request = _request(stream=mode != "buffered")
    deltas = list(backend.generate(request))
    structured = backend.get_last_structured()
    assert structured.reasoning == expected
    if mode == "buffered":
        assert "".join(deltas) == "Visible answer"
        outputs = [OpenAIDialect().render(request, "Visible answer", structured=structured)
                   ["choices"][0]["message"]]
    else:
        assert "".join(d.text or "" if isinstance(d, ModelDelta) else d for d in deltas) == (
            "Visible answer")
        chunks = _parse_openai_sse(b"".join(OpenAIDialect().stream(
            request, deltas, get_structured=backend.get_last_structured)))
        outputs = [c["choices"][0]["delta"] for c in chunks if c.get("choices")]
    assert [o["reasoning_content"] for o in outputs if "reasoning_content" in o] == (
        [] if expected is None else [expected])
    assert all("reasoning" not in o and "reasoning_text" not in o for o in outputs)
