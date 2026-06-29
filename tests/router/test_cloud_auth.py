"""Cloud-tier credential resolution for the router Backend seam (T006).

Proves PRD acceptance criterion 1:
  AC1 - a cloud-tier request authenticates with the key resolved from the tier's
        configured env var; a MISSING key yields a clear, typed startup error
        naming the env var — never a silent no-auth request.

Hermetic: a fake transport captures the outgoing headers/body in-process. NO
network is touched; the env var is provided via monkeypatch with a fake key.
"""

from __future__ import annotations

import json

import pytest

from anvil_serving.router.backends import CloudBackend, MissingCredentialError
from anvil_serving.router.config import ConfigError, Tier

FAKE_KEY = "sk-test-DEADBEEF-not-a-real-key"
ANTHROPIC_ENV = "ANVIL_TEST_ANTHROPIC_KEY"
OPENAI_ENV = "ANVIL_TEST_OPENAI_KEY"


def _anthropic_tier() -> Tier:
    return Tier(
        id="cloud",
        base_url="https://api.anthropic.com",
        dialect="anthropic",
        context_limit=200000,
        privacy="cloud",
        tool_support=True,
        auth_env=ANTHROPIC_ENV,
    )


def _openai_cloud_tier() -> Tier:
    return Tier(
        id="cloud-oai",
        base_url="https://api.openai.com/v1",
        dialect="openai",
        context_limit=128000,
        privacy="cloud",
        tool_support=True,
        auth_env=OPENAI_ENV,
    )


def _request(dialect: str):
    from anvil_serving.router.internal import InternalRequest, Message

    return InternalRequest(
        model="some-model",
        messages=[Message("user", "ping")],
        system="be terse",
        max_tokens=16,
        dialect=dialect,
    )


class _CaptureTransport:
    """A fake Transport that records the call and returns a canned reply."""

    def __init__(self, reply_body: bytes):
        self.reply_body = reply_body
        self.calls = []

    def __call__(self, url, *, data, headers, timeout):
        self.calls.append({"url": url, "data": data, "headers": dict(headers),
                           "timeout": timeout})
        return self.reply_body


# ── AC1a: the key from the env var ends up on the outbound auth header ─────────
def test_anthropic_request_sets_x_api_key_from_env(monkeypatch):
    monkeypatch.setenv(ANTHROPIC_ENV, FAKE_KEY)
    reply = json.dumps({"content": [{"type": "text", "text": "pong"}]}).encode()
    transport = _CaptureTransport(reply)

    backend = CloudBackend(_anthropic_tier(), transport=transport)
    out = "".join(backend.generate(_request("anthropic")))

    assert out == "pong"  # response text flows back through as deltas
    assert len(transport.calls) == 1
    headers = transport.calls[0]["headers"]
    # Anthropic auth = x-api-key, built from the env var, plus the version pin.
    assert headers["x-api-key"] == FAKE_KEY
    assert "anthropic-version" in headers
    assert "Authorization" not in headers  # not the OpenAI scheme
    assert transport.calls[0]["url"] == "https://api.anthropic.com/v1/messages"


def test_openai_cloud_request_sets_bearer_from_env(monkeypatch):
    monkeypatch.setenv(OPENAI_ENV, FAKE_KEY)
    reply = json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": "hi there"}}]}
    ).encode()
    transport = _CaptureTransport(reply)

    backend = CloudBackend(_openai_cloud_tier(), transport=transport)
    out = "".join(backend.generate(_request("openai")))

    assert out == "hi there"
    headers = transport.calls[0]["headers"]
    # OpenAI-compatible auth = Authorization: Bearer <key from env>.
    assert headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert "x-api-key" not in headers
    assert transport.calls[0]["url"] == "https://api.openai.com/v1/chat/completions"


def test_request_body_carries_no_credential(monkeypatch):
    # Defense: the secret must ride a header, never the JSON body.
    monkeypatch.setenv(ANTHROPIC_ENV, FAKE_KEY)
    transport = _CaptureTransport(
        json.dumps({"content": [{"type": "text", "text": "ok"}]}).encode()
    )
    backend = CloudBackend(_anthropic_tier(), transport=transport)
    list(backend.generate(_request("anthropic")))

    body_bytes = transport.calls[0]["data"]
    assert FAKE_KEY.encode() not in body_bytes
    body = json.loads(body_bytes)
    assert body["max_tokens"] == 16
    assert body["system"] == "be terse"


# ── AC1b: a missing/empty key is a clear, typed startup error, NOT silent ──────
def test_missing_env_var_raises_typed_error_naming_var(monkeypatch):
    monkeypatch.delenv(ANTHROPIC_ENV, raising=False)
    transport = _CaptureTransport(b"{}")

    with pytest.raises(MissingCredentialError) as ei:
        CloudBackend(_anthropic_tier(), transport=transport)

    msg = str(ei.value)
    assert ANTHROPIC_ENV in msg       # names the offending env var
    assert "cloud" in msg             # names the tier
    assert ei.value.env_var == ANTHROPIC_ENV
    assert ei.value.tier_id == "cloud"
    # It is a config error, and it fired at construction => no request was sent.
    assert isinstance(ei.value, ConfigError)
    assert transport.calls == []


def test_empty_env_var_is_treated_as_missing(monkeypatch):
    # An empty string is NOT a valid key; must fail the same way, not auth with "".
    monkeypatch.setenv(ANTHROPIC_ENV, "")
    with pytest.raises(MissingCredentialError):
        CloudBackend(_anthropic_tier())


def test_missing_key_is_not_a_silent_no_auth_request(monkeypatch):
    # The anti-pattern guard: construction must raise rather than yield a backend
    # that would later send an unauthenticated request.
    monkeypatch.delenv(ANTHROPIC_ENV, raising=False)
    sent = []

    def spy_transport(url, *, data, headers, timeout):
        sent.append(headers)
        return b"{}"

    with pytest.raises(MissingCredentialError):
        CloudBackend(_anthropic_tier(), transport=spy_transport)
    assert sent == []  # never reached the transport


def test_repr_does_not_leak_key(monkeypatch):
    monkeypatch.setenv(ANTHROPIC_ENV, FAKE_KEY)
    backend = CloudBackend(_anthropic_tier(), transport=_CaptureTransport(b"{}"))
    assert FAKE_KEY not in repr(backend)
    assert ANTHROPIC_ENV in repr(backend)  # the NAME is fine to show


def test_local_tier_rejected(monkeypatch):
    monkeypatch.setenv("ANVIL_LOCAL_KEY", FAKE_KEY)
    local = Tier(
        id="fast-local",
        base_url="http://127.0.0.1:30001/v1",
        dialect="openai",
        context_limit=32768,
        privacy="local",
        tool_support=True,
        auth_env="ANVIL_LOCAL_KEY",
    )
    with pytest.raises(ConfigError):
        CloudBackend(local)


def test_env_mapping_override_resolves_key():
    # The env source is injectable too (no process env needed for the happy path).
    transport = _CaptureTransport(
        json.dumps({"content": [{"type": "text", "text": "x"}]}).encode()
    )
    backend = CloudBackend(
        _anthropic_tier(),
        env={ANTHROPIC_ENV: FAKE_KEY},
        transport=transport,
    )
    list(backend.generate(_request("anthropic")))
    assert transport.calls[0]["headers"]["x-api-key"] == FAKE_KEY


def test_default_transport_uses_urllib(monkeypatch):
    # Without an injected transport, the backend must drive urllib.request.urlopen
    # (stdlib-only) — monkeypatch it so NO real network is touched, and assert the
    # auth header reached the urllib.Request.
    monkeypatch.setenv(ANTHROPIC_ENV, FAKE_KEY)
    captured = {}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"content": [{"type": "text", "text": "ok"}]}).encode()

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        captured["url"] = req.full_url
        return _FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    backend = CloudBackend(_anthropic_tier())  # default transport
    out = "".join(backend.generate(_request("anthropic")))

    assert out == "ok"
    # urllib capitalizes header names; look the key up case-insensitively.
    lower = {k.lower(): v for k, v in captured["headers"].items()}
    assert lower["x-api-key"] == FAKE_KEY
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
