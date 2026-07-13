"""Wire fidelity for image-carrying requests through the relay backends
(gpu-reservations:T011).

The relay rebuilds the upstream body from the flattened InternalRequest, which
keeps only text — before this fix an OCR/vision request routed to a
vision-capable tier (e.g. the "ocr" preset -> a PaddleOCR-VL serve) silently
lost the image the caller sent: the tier answered a text-only prompt and the
failure was invisible. These tests pin:

* the ``has_image_artifacts`` detector (both dialects' image spellings);
* same-dialect passthrough (raw messages forwarded verbatim, image intact);
* cross-dialect requests keep the pre-T011 flattened behaviour (image
  translation is deliberately out of scope);
* regression safety: an image-free request builds the exact same body as before.
"""
from __future__ import annotations

from anvil_serving.router.backends.cloud import CloudBackend
from anvil_serving.router.config import Tier
from anvil_serving.router.dialects.anthropic import AnthropicDialect
from anvil_serving.router.dialects.openai import OpenAIDialect
from anvil_serving.router.dialects.translate import has_image_artifacts

PNG_DATA_URI = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="

OPENAI_IMAGE_MESSAGES = [
    {"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": PNG_DATA_URI}},
        {"type": "text", "text": "OCR:"},
    ]},
]

ANTHROPIC_IMAGE_MESSAGES = [
    {"role": "user", "content": [
        {"type": "image", "source": {
            "type": "base64", "media_type": "image/png",
            "data": "iVBORw0KGgoAAAANSUhEUg==",
        }},
        {"type": "text", "text": "OCR:"},
    ]},
]


def _tier(dialect: str, privacy: str = "cloud") -> Tier:
    return Tier(
        id=f"{dialect}-tier",
        base_url="https://api.example.test",
        dialect=dialect,
        context_limit=200_000,
        privacy=privacy,
        tool_support=True,
        auth_env="EXAMPLE_KEY",
        model="concrete-model",
    )


def _backend(dialect: str) -> CloudBackend:
    return CloudBackend(_tier(dialect), env={"EXAMPLE_KEY": "k"})


# --------------------------------------------------------------------------- #
# detector
# --------------------------------------------------------------------------- #
def test_has_image_artifacts_detects_each_shape():
    assert has_image_artifacts({"messages": OPENAI_IMAGE_MESSAGES})
    assert has_image_artifacts({"messages": ANTHROPIC_IMAGE_MESSAGES})
    assert has_image_artifacts({"messages": [
        {"role": "user", "content": [{"type": "input_image", "image_url": "u"}]}]})
    assert not has_image_artifacts({"messages": [{"role": "user", "content": "hi"}]})
    assert not has_image_artifacts({"messages": [
        {"role": "user", "content": [{"type": "text", "text": "no image"}]}]})
    assert not has_image_artifacts({})
    assert not has_image_artifacts({"messages": "garbage"})
    assert not has_image_artifacts({"messages": [
        {"role": "user", "content": ["bare string", 42, None]}]})


# --------------------------------------------------------------------------- #
# same-dialect passthrough: the image reaches the tier verbatim
# --------------------------------------------------------------------------- #
def test_same_dialect_openai_image_passthrough():
    """The T011 money path: an OCR caller (OpenAI dialect, image_url content)
    -> the local PaddleOCR-VL serve (OpenAI dialect)."""
    body_in = {
        "model": "ocr",
        "messages": OPENAI_IMAGE_MESSAGES,
        "max_tokens": 1024,
    }
    request = OpenAIDialect().parse_request(body_in)
    body = _backend("openai")._build_body(request)
    assert body["messages"] == OPENAI_IMAGE_MESSAGES  # verbatim, image intact
    assert body["model"] == "concrete-model"


def test_same_dialect_anthropic_image_passthrough():
    body_in = {
        "model": "ocr",
        "max_tokens": 1024,
        "system": "be brief",
        "messages": ANTHROPIC_IMAGE_MESSAGES,
    }
    request = AnthropicDialect().parse_request(body_in)
    body = _backend("anthropic")._build_body(request)
    assert body["messages"] == ANTHROPIC_IMAGE_MESSAGES
    assert body["system"] == "be brief"


def test_openai_image_with_system_message_stays_verbatim():
    body_in = {
        "model": "ocr",
        "messages": [{"role": "system", "content": "extract text"}]
        + OPENAI_IMAGE_MESSAGES,
    }
    request = OpenAIDialect().parse_request(body_in)
    body = _backend("openai")._build_body(request)
    # Verbatim: the system message is already in messages, never duplicated.
    assert body["messages"] == body_in["messages"]
    assert sum(1 for m in body["messages"] if m["role"] == "system") == 1


# --------------------------------------------------------------------------- #
# cross-dialect: image translation is out of scope — flattened as before
# --------------------------------------------------------------------------- #
def test_cross_dialect_image_request_keeps_flattened_behaviour():
    body_in = {
        "model": "ocr",
        "max_tokens": 1024,
        "messages": ANTHROPIC_IMAGE_MESSAGES,
    }
    request = AnthropicDialect().parse_request(body_in)
    body = _backend("openai")._build_body(request)
    # Pre-T011 shape: flattened text only (the image block is dropped).
    assert body["messages"] == [{"role": "user", "content": "OCR:"}]


# --------------------------------------------------------------------------- #
# regression pin: image-free bodies are byte-identical to before
# --------------------------------------------------------------------------- #
def test_image_free_request_body_is_unchanged():
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
