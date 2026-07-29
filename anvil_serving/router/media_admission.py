"""Content-free multimodal request admission for explicitly enabled tiers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


_IMAGE_TYPES = frozenset(("image_url", "input_image", "image"))
_VIDEO_TYPES = frozenset(("video_url", "input_video", "video"))


@dataclass(frozen=True)
class MediaAdmission:
    """One bounded admission decision without retaining media or prompt data."""

    enabled: bool
    images: int
    videos: int
    estimated_visual_tokens: int
    allowed: bool
    reason: str


def count_media(raw: Mapping[str, Any]) -> tuple[int, int]:
    """Count recognized top-level message media blocks without reading media bytes."""

    images = 0
    videos = 0
    messages = raw.get("messages")
    if not isinstance(messages, (list, tuple)):
        return images, videos
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        content = message.get("content")
        if not isinstance(content, (list, tuple)):
            continue
        for block in content:
            if not isinstance(block, Mapping):
                continue
            block_type = block.get("type")
            if block_type in _IMAGE_TYPES:
                images += 1
            elif block_type in _VIDEO_TYPES:
                videos += 1
    return images, videos


def evaluate_media_admission(
    params: Mapping[str, Any] | None,
    raw: Mapping[str, Any],
    *,
    prompt_tokens: int,
    context_limit: int,
) -> MediaAdmission:
    """Evaluate an opt-in media count and estimated-context policy."""

    capacity = params.get("capacity") if isinstance(params, Mapping) else None
    if not isinstance(capacity, Mapping) or capacity.get("media_admission_enabled") is not True:
        return MediaAdmission(False, 0, 0, 0, True, "disabled")

    images, videos = count_media(raw)
    image_limit = capacity.get("image_limit")
    video_limit = capacity.get("video_limit")
    image_tokens = capacity.get("image_tokens_estimate")
    video_tokens = capacity.get("video_tokens_estimate")
    controls = (image_limit, video_limit, image_tokens, video_tokens)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in controls):
        return MediaAdmission(True, images, videos, 0, False, "invalid_policy")
    if images > image_limit:
        return MediaAdmission(True, images, videos, 0, False, "image_limit")
    if videos > video_limit:
        return MediaAdmission(True, images, videos, 0, False, "video_limit")

    visual_tokens = images * image_tokens + videos * video_tokens
    output_tokens = raw.get("max_completion_tokens", raw.get("max_tokens", 0))
    if isinstance(output_tokens, bool) or not isinstance(output_tokens, int):
        output_tokens = 0
    output_tokens = max(output_tokens, 0)
    if prompt_tokens + visual_tokens + output_tokens > context_limit:
        return MediaAdmission(
            True,
            images,
            videos,
            visual_tokens,
            False,
            "context_limit",
        )
    return MediaAdmission(True, images, videos, visual_tokens, True, "allowed")


__all__ = ["MediaAdmission", "count_media", "evaluate_media_admission"]
