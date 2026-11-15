from anvil_serving.router.backends.relay import RelayBackend
from anvil_serving.router.dialects.anthropic import AnthropicDialect
from anvil_serving.router.dialects.openai import OpenAIDialect
from tests.router.helpers import make_tier as _tier

PNG_DATA_URI = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
OPENAI_IMAGE_MESSAGES = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": PNG_DATA_URI}}, {"type": "text", "text": "OCR:"}]}]
ANTHROPIC_IMAGE_MESSAGES = [{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgoAAAANSUhEUg=="}}, {"type": "text", "text": "OCR:"}]}]


def _backend(dialect):
    return RelayBackend(_tier(dialect), env={"EXAMPLE_KEY": "k"})


def test_same_dialect_openai_image_passthrough():
    request = OpenAIDialect().parse_request({"model": "ocr", "messages": OPENAI_IMAGE_MESSAGES, "max_tokens": 1024})
    body = _backend("openai")._build_body(request)
    assert body["messages"] == OPENAI_IMAGE_MESSAGES
    assert body["model"] == "concrete-model"


def test_same_dialect_anthropic_image_passthrough():
    request = AnthropicDialect().parse_request({"model": "ocr", "max_tokens": 1024, "system": "be brief", "messages": ANTHROPIC_IMAGE_MESSAGES})
    body = _backend("anthropic")._build_body(request)
    assert body["messages"] == ANTHROPIC_IMAGE_MESSAGES
    assert body["system"] == "be brief"
