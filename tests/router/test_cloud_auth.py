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
from anvil_serving.router.backends.cloud import CloudBackendError
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
    """An Anthropic-shaped request: system lives ONLY on `.system` (no system
    message in `messages`), as the Anthropic dialect produces it."""
    from anvil_serving.router.internal import InternalRequest, Message

    return InternalRequest(
        model="some-model",
        messages=[Message("user", "ping")],
        system="be terse",
        max_tokens=16,
        dialect=dialect,
    )


def _openai_shaped_request():
    """An OpenAI-shaped request: the system prompt is a leading role=system
    message AND mirrored on `.system` (what OpenAIDialect.parse_request yields)."""
    from anvil_serving.router.internal import InternalRequest, Message

    return InternalRequest(
        model="some-model",
        messages=[Message("system", "be terse"), Message("user", "ping")],
        system="be terse",
        max_tokens=16,
        dialect="openai",
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


# ── review fix 1: forward request.system to an OpenAI cloud tier ───────────────
def _openai_reply() -> bytes:
    return json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
    ).encode()


def test_openai_body_includes_system_from_anthropic_shaped_request(monkeypatch):
    # Anthropic-shaped request (system only on `.system`) routed to an OpenAI
    # cloud tier MUST carry the system instruction, not silently drop it.
    monkeypatch.setenv(OPENAI_ENV, FAKE_KEY)
    transport = _CaptureTransport(_openai_reply())
    backend = CloudBackend(_openai_cloud_tier(), transport=transport)

    list(backend.generate(_request("anthropic")))
    body = json.loads(transport.calls[0]["data"])
    roles = [m["role"] for m in body["messages"]]

    assert roles[0] == "system"                       # prepended
    assert body["messages"][0]["content"] == "be terse"
    assert roles.count("system") == 1                 # exactly one


def test_openai_body_does_not_duplicate_existing_system_message(monkeypatch):
    # OpenAI-shaped request already has a leading system message; do NOT add a
    # second one from `.system`.
    monkeypatch.setenv(OPENAI_ENV, FAKE_KEY)
    transport = _CaptureTransport(_openai_reply())
    backend = CloudBackend(_openai_cloud_tier(), transport=transport)

    list(backend.generate(_openai_shaped_request()))
    body = json.loads(transport.calls[0]["data"])
    roles = [m["role"] for m in body["messages"]]

    assert roles == ["system", "user"]                # not duplicated
    assert roles.count("system") == 1


# ── review fix 2: /v1 endpoint normalization for openai cloud tiers ────────────
@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.openai.com",       # bare host, no /v1 -> must not 404
        "https://api.openai.com/v1",    # already /v1
        "https://api.openai.com/v1/",   # trailing slash
    ],
)
def test_openai_endpoint_normalizes_to_v1(monkeypatch, base_url):
    monkeypatch.setenv(OPENAI_ENV, FAKE_KEY)
    tier = Tier(
        id="cloud-oai",
        base_url=base_url,
        dialect="openai",
        context_limit=128000,
        privacy="cloud",
        tool_support=True,
        auth_env=OPENAI_ENV,
    )
    transport = _CaptureTransport(_openai_reply())
    backend = CloudBackend(tier, transport=transport)

    list(backend.generate(_request("openai")))
    assert transport.calls[0]["url"] == "https://api.openai.com/v1/chat/completions"


# ── review fix 3: whitespace / newline keys fail fast (not a blank-auth 401) ───
@pytest.mark.parametrize("blank", [" ", "   ", "\n", "\t", " \n "])
def test_whitespace_only_key_raises_missing_credential(monkeypatch, blank):
    monkeypatch.setenv(ANTHROPIC_ENV, blank)
    with pytest.raises(MissingCredentialError) as ei:
        CloudBackend(_anthropic_tier())
    assert ANTHROPIC_ENV in str(ei.value)   # names the offending env var
    assert ei.value.env_var == ANTHROPIC_ENV


def test_trailing_newline_key_is_stripped_before_header(monkeypatch):
    # A real key with a trailing newline (the `$(cat keyfile)` case) is usable —
    # it must be stripped, not rejected, and never reach the header with the \n.
    monkeypatch.setenv(ANTHROPIC_ENV, FAKE_KEY + "\n")
    transport = _CaptureTransport(
        json.dumps({"content": [{"type": "text", "text": "x"}]}).encode()
    )
    backend = CloudBackend(_anthropic_tier(), transport=transport)
    list(backend.generate(_request("anthropic")))

    assert transport.calls[0]["headers"]["x-api-key"] == FAKE_KEY  # no trailing \n


# ── Fix 3: cloud tier always requires a credential (chore/harden-exposure) ────
def test_cloud_tier_requires_key_even_with_require_key_false(monkeypatch):
    """_require_key=False must NOT bypass the cloud credential gate.

    A cloud-privacy tier must always raise MissingCredentialError when the
    key env var is unset — even when the private opt-out is passed.  This
    prevents a misconfigured RelayBackend (or other subclass) from silently
    sending unauthenticated requests to a paid provider.
    """
    monkeypatch.delenv(ANTHROPIC_ENV, raising=False)
    transport = _CaptureTransport(b"{}")

    with pytest.raises(MissingCredentialError) as ei:
        # The private _require_key=False opt-out is what RelayBackend passes;
        # a cloud-privacy tier must still reject construction without a key.
        CloudBackend(_anthropic_tier(), transport=transport, _require_key=False)

    assert ei.value.env_var == ANTHROPIC_ENV
    assert ei.value.tier_id == "cloud"
    assert transport.calls == []  # no request was sent


def test_local_tier_with_require_key_false_constructs_without_key():
    """_require_key=False on a LOCAL-privacy tier must still work (RelayBackend).

    Fix 3 must not break the RelayBackend use-case: a local relay that has no
    auth credential is legitimate and must not raise MissingCredentialError.
    """
    from anvil_serving.router.config import Tier

    local = Tier(
        id="fast-local",
        base_url="http://127.0.0.1:30001/v1",
        dialect="openai",
        context_limit=32768,
        privacy="local",
        tool_support=True,
        auth_env="ANVIL_FAST_LOCAL_KEY",
    )
    transport = _CaptureTransport(b"{}")
    # Must NOT raise even though ANVIL_FAST_LOCAL_KEY is not in the env.
    backend = CloudBackend(local, env={}, transport=transport, _require_key=False)
    assert backend._key == ""  # no key resolved — that is expected and fine


# ── Fix 4: URLError hostname not leaked in CloudBackendError (chore/harden-exposure) ──
def test_urlerror_hostname_not_leaked_in_cloudbackenderror(monkeypatch):
    """A transport that raises URLError with a hostname in the reason must NOT
    propagate the hostname through to the CloudBackendError message.

    This covers the case where the front door's 500 path would otherwise expose
    the upstream tier's hostname to the client.
    """
    import urllib.error

    UPSTREAM_HOST = "secret-internal-api.corp.example.com"

    def leaking_transport(url, *, data, headers, timeout):
        # Simulates a URLError whose reason contains the upstream hostname
        # (e.g. a TLS certificate mismatch or a DNS failure).
        raise urllib.error.URLError(
            reason=f"[SSL: CERTIFICATE_VERIFY_FAILED] hostname mismatch: "
                   f"{UPSTREAM_HOST!r} not in cert"
        )

    monkeypatch.setenv(ANTHROPIC_ENV, FAKE_KEY)
    backend = CloudBackend(_anthropic_tier(), transport=leaking_transport)

    with pytest.raises(CloudBackendError) as ei:
        list(backend.generate(_request("anthropic")))

    err_msg = str(ei.value)
    assert UPSTREAM_HOST not in err_msg, (
        f"upstream hostname {UPSTREAM_HOST!r} leaked into CloudBackendError: {err_msg!r}"
    )
    # The error IS a CloudBackendError (not URLError propagating uncaught).
    assert isinstance(ei.value, CloudBackendError)


# ── Fix (issue #47): _extract_text raises on structural malformation ───────────
#
# Rationale: returning "" for a malformed provider response masks the error
# as an empty completion; the verify gate then sees empty content and (depending
# on the verifier) may pass a structurally-broken response to the client.
# The fix raises CloudBackendError for missing/wrong-type structural fields so
# the fallback path treats it as a failed attempt rather than a valid answer.
#
# Two cases are carefully distinguished:
#   - structurally malformed  → CloudBackendError (not "")
#   - legitimately empty      → "" (valid completion with no content)

# --- OpenAI dialect: malformed payloads -----------------------------------------

@pytest.mark.parametrize("body, label", [
    # missing 'choices' key entirely
    (b'{"id":"x","object":"chat.completion"}', "missing-choices"),
    # 'choices' is not a list (wrong type)
    (b'{"choices": "not-a-list"}', "choices-not-list"),
    # choices[0] is not a Mapping
    (b'{"choices": ["a-string"]}', "choices-elem-not-object"),
    # choices[0] missing 'message' field
    (b'{"choices": [{"finish_reason": "stop"}]}', "missing-message"),
    # choices[0].message is not a Mapping
    (b'{"choices": [{"message": "bare-string"}]}', "message-not-object"),
])
def test_openai_malformed_payload_raises(monkeypatch, body, label):
    """A structurally malformed OpenAI response raises CloudBackendError, not ''."""
    monkeypatch.setenv(OPENAI_ENV, FAKE_KEY)
    transport = _CaptureTransport(body)
    backend = CloudBackend(_openai_cloud_tier(), transport=transport)

    with pytest.raises(CloudBackendError):
        list(backend.generate(_request("openai")))


def test_openai_empty_choices_is_valid_empty_completion(monkeypatch):
    """choices: [] is a legitimate empty completion — must return '', not raise."""
    monkeypatch.setenv(OPENAI_ENV, FAKE_KEY)
    body = json.dumps({"choices": []}).encode()
    transport = _CaptureTransport(body)
    backend = CloudBackend(_openai_cloud_tier(), transport=transport)

    out = list(backend.generate(_request("openai")))
    assert "".join(out) == ""


def test_openai_null_content_is_valid_empty_completion(monkeypatch):
    """message.content == null is a valid empty completion — return '', not raise."""
    monkeypatch.setenv(OPENAI_ENV, FAKE_KEY)
    body = json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": None}}]}
    ).encode()
    transport = _CaptureTransport(body)
    backend = CloudBackend(_openai_cloud_tier(), transport=transport)

    out = list(backend.generate(_request("openai")))
    assert "".join(out) == ""


# --- Anthropic dialect: malformed payloads --------------------------------------

@pytest.mark.parametrize("body, label", [
    # missing 'content' key entirely
    (b'{"type":"message","role":"assistant"}', "missing-content"),
    # 'content' is not a list (wrong type)
    (b'{"content": "bare-string"}', "content-not-list"),
    # 'content' is null (structurally wrong for Anthropic)
    (b'{"content": null}', "content-null"),
])
def test_anthropic_malformed_payload_raises(monkeypatch, body, label):
    """A structurally malformed Anthropic response raises CloudBackendError, not ''."""
    monkeypatch.setenv(ANTHROPIC_ENV, FAKE_KEY)
    transport = _CaptureTransport(body)
    backend = CloudBackend(_anthropic_tier(), transport=transport)

    with pytest.raises(CloudBackendError):
        list(backend.generate(_request("anthropic")))


def test_anthropic_empty_content_list_is_valid_empty_completion(monkeypatch):
    """content: [] (no text blocks) is a valid empty completion — return '', not raise."""
    monkeypatch.setenv(ANTHROPIC_ENV, FAKE_KEY)
    body = json.dumps({"content": []}).encode()
    transport = _CaptureTransport(body)
    backend = CloudBackend(_anthropic_tier(), transport=transport)

    out = list(backend.generate(_request("anthropic")))
    assert "".join(out) == ""


def test_anthropic_non_text_blocks_only_is_empty_completion(monkeypatch):
    """A content list with only non-text blocks (e.g. tool_use) returns ''."""
    monkeypatch.setenv(ANTHROPIC_ENV, FAKE_KEY)
    body = json.dumps({
        "content": [{"type": "tool_use", "id": "tu1", "name": "bash", "input": {}}]
    }).encode()
    transport = _CaptureTransport(body)
    backend = CloudBackend(_anthropic_tier(), transport=transport)

    out = list(backend.generate(_request("anthropic")))
    assert "".join(out) == ""  # no text blocks → legitimately empty text
