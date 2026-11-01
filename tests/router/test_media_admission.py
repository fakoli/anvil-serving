"""Opt-in multimodal admission remains bounded and content-free."""

from __future__ import annotations

from pathlib import Path


from anvil_serving.router.availability import AlwaysAvailable
from anvil_serving.router.config import RouterConfig, Tier
from anvil_serving.router.dialects.openai import OpenAIDialect
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
