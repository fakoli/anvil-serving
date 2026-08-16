"""Opt-in multimodal admission remains bounded and content-free."""

from __future__ import annotations

from pathlib import Path

import pytest

from anvil_serving.router.availability import AlwaysAvailable
from anvil_serving.router.config import RouterConfig, Tier
from anvil_serving.router.dialects.openai import OpenAIDialect
from anvil_serving.router.internal import NoAvailableTierError
from anvil_serving.router.media_admission import count_media
from anvil_serving.router.serve import RoutingBackend
from tests.router.helpers import StaticBackend


ROOT = Path(__file__).resolve().parents[2]


class _CountingBackend(StaticBackend):
    def __init__(self):
        super().__init__(["ok"])
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        yield from super().generate(request)


def _routing(*, enabled: bool = True, context_limit: int = 131_072):
    tier = Tier(
        id="agents-a1",
        base_url="http://127.0.0.1:30000/v1",
        model="agents-a1-fp8",
        dialect="openai",
        context_limit=context_limit,
        privacy="local",
        tool_support=True,
        auth_env="TEST_KEY",
        params={
            "capacity": {
                "media_admission_enabled": enabled,
                "image_limit": 4,
                "video_limit": 1,
                "image_tokens_estimate": 2_048,
                "video_tokens_estimate": 16_384,
            }
        },
    )
    config = RouterConfig(tiers=(tier,), model_routes={"vision.test": tier.id})
    backend = _CountingBackend()
    return RoutingBackend(
        config,
        {tier.id: backend},
        availability=AlwaysAvailable(),
    ), backend


def _request(*, images: int = 0, videos: int = 0, max_tokens: int = 16_384):
    content = [{"type": "text", "text": "Describe the supplied media."}]
    content.extend(
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}
        for _ in range(images)
    )
    content.extend(
        {"type": "video_url", "video_url": {"url": "data:video/mp4;base64,AA=="}}
        for _ in range(videos)
    )
    return OpenAIDialect().parse_request(
        {
            "model": "vision.test",
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
        }
    )


def test_one_video_and_four_images_are_admitted():
    routing, backend = _routing()
    assert "".join(routing.generate(_request(images=4, videos=1))) == "ok"
    assert backend.calls == 1


@pytest.mark.parametrize(
    ("images", "videos", "reason"),
    (
        (5, 0, "media_admission_image_limit"),
        (0, 2, "media_admission_video_limit"),
    ),
)
def test_media_count_overflow_fails_before_upstream(images, videos, reason):
    routing, backend = _routing()
    with pytest.raises(NoAvailableTierError) as error:
        routing.generate(_request(images=images, videos=videos))
    assert error.value.kind == "media_limit"
    assert routing._decision_log.records[-1].attempts[0].reason == reason
    assert backend.calls == 0


def test_zero_video_limit_rejects_one_video_before_upstream():
    routing, backend = _routing()
    tier = routing._config.tiers[0]
    tier.params["capacity"]["video_limit"] = 0

    with pytest.raises(NoAvailableTierError) as error:
        routing.generate(_request(videos=1))

    assert error.value.kind == "media_limit"
    assert routing._decision_log.records[-1].attempts[0].reason == (
        "media_admission_video_limit"
    )
    assert backend.calls == 0


def test_visual_estimate_preserves_requested_output_headroom():
    routing, backend = _routing(context_limit=20_000)
    with pytest.raises(NoAvailableTierError) as error:
        routing.generate(_request(videos=1, max_tokens=4_096))
    assert error.value.kind == "over_context"
    assert backend.calls == 0


def test_disabled_policy_preserves_existing_media_path():
    routing, backend = _routing(enabled=False)
    assert "".join(routing.generate(_request(images=8, videos=3))) == "ok"
    assert backend.calls == 1


def test_media_counter_ignores_malformed_and_nested_values():
    raw = {
        "messages": [
            {
                "content": [
                    {"type": "image_url"},
                    {"type": "video_url"},
                    {"nested": {"type": "video_url"}},
                    "not-a-block",
                ]
            },
            "not-a-message",
        ]
    }
    assert count_media(raw) == (1, 1)


def test_agents_a1_isolated_router_enables_declared_media_policy():
    from anvil_serving.router.config import load

    config = load(ROOT / "configs" / "agents-a1-qualification-router.toml")
    tier = config.tier("agents-a1-fp8-qualification")
    capacity = tier.params["capacity"]
    assert config.model_routes == {
        "vision.agents_a1_qualification": "agents-a1-fp8-qualification"
    }
    assert capacity["media_admission_enabled"] is True
    assert capacity["image_limit"] == 4
    assert capacity["video_limit"] == 1
