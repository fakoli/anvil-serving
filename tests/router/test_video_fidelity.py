"""Wire-fidelity and fail-closed behavior for video requests."""

from __future__ import annotations

import pytest

from anvil_serving.router.backends.relay import RelayBackend
from anvil_serving.router.config import Tier
from anvil_serving.router.dialects.openai import OpenAIDialect
from anvil_serving.router.dialects.translate import has_video_artifacts
from anvil_serving.router.internal import BackendClientError

VIDEO_DATA_URI = "data:video/mp4;base64,AAAAHGZ0eXBtcDQy"
VIDEO_MESSAGES = [
    {
        "role": "user",
        "content": [
            {"type": "video_url", "video_url": {"url": VIDEO_DATA_URI}},
            {"type": "text", "text": "Describe the ordered events."},
        ],
    },
]


def _backend(dialect: str) -> RelayBackend:
    return RelayBackend(
        Tier(
            id=f"{dialect}-video",
            base_url="https://api.example.test",
            dialect=dialect,
            context_limit=131_072,
            privacy="local",
            tool_support=True,
            auth_env="EXAMPLE_KEY",
            model="concrete-model",
        ),
        env={"EXAMPLE_KEY": "secret"},
    )


def test_has_video_artifacts_detects_supported_wire_spellings():
    assert has_video_artifacts({"messages": VIDEO_MESSAGES})
    assert has_video_artifacts({"messages": [{
        "role": "user", "content": [{"type": "input_video", "video_url": "u"}],
    }]})
    assert has_video_artifacts({"messages": [{
        "role": "user", "content": [{"type": "video", "source": {}}],
    }]})
    assert not has_video_artifacts({"messages": [{
        "role": "user", "content": [{"type": "text", "text": "no video"}],
    }]})


def test_same_dialect_openai_video_is_forwarded_verbatim():
    request = OpenAIDialect().parse_request({
        "model": "vision",
        "messages": VIDEO_MESSAGES,
        "max_tokens": 512,
    })

    body = _backend("openai")._build_body(request)

    assert body["messages"] == VIDEO_MESSAGES
    assert body["model"] == "concrete-model"


def test_cross_dialect_video_fails_closed_instead_of_flattening():
    request = OpenAIDialect().parse_request({
        "model": "vision",
        "messages": VIDEO_MESSAGES,
        "max_tokens": 512,
    })

    with pytest.raises(
        BackendClientError, match="cross-dialect video translation is unsupported"
    ) as exc_info:
        _backend("anthropic")._build_body(request)
    assert exc_info.value.status == 400
    assert exc_info.value.etype == "invalid_request_error"
