"""Tests for issue #43: tier-configured model overrides the routing token (fix/preset-model-resolution).

The harness sends ``request.model`` as a routing token (e.g. ``"planning"``,
``"quick-edit"``). Before this fix, that token was forwarded verbatim to the
upstream provider, which would reject it with a 4xx.

Fix: when a :class:`~anvil_serving.router.config.Tier` has a ``model`` field set,
the backend uses it as the upstream ``model`` value; when the field is absent
(backward-compatible), ``request.model`` is used as before.

Coverage:
  - Config: tier TOML with ``model`` parses; without it, ``tier.model is None``.
  - Config: non-string ``model`` value raises ConfigError.
  - CloudBackend (anthropic dialect): tier.model wins over routing token.
  - CloudBackend (openai dialect): tier.model wins over routing token.
  - CloudBackend: tier without model falls back to request.model (back-compat).
  - RelayBackend: inherits the fix (local tier with model field).
"""

from __future__ import annotations

import json
import pathlib

import pytest

from anvil_serving.router.backends import CloudBackend
from anvil_serving.router.config import ConfigError, Tier, load
from anvil_serving.router.internal import InternalRequest, Message

FAKE_KEY = "sk-test-DEADBEEF-model-resolve"
ANTHROPIC_ENV = "ANVIL_TEST_ANTHROPIC_KEY"
OPENAI_ENV = "ANVIL_TEST_OPENAI_KEY"

# Path to the repo root (this file is tests/router/<name>.py -> parents[2] = root)
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


# ── helpers ──────────────────────────────────────────────────────────────────

def _tier(*, model: "str | None" = None, dialect: str = "anthropic",
          privacy: str = "cloud") -> Tier:
    return Tier(
        id="cloud" if privacy == "cloud" else "fast-local",
        base_url=(
            "https://api.anthropic.com"
            if dialect == "anthropic"
            else "http://127.0.0.1:30001/v1"
        ),
        dialect=dialect,
        context_limit=200000,
        privacy=privacy,
        tool_support=True,
        auth_env=ANTHROPIC_ENV if dialect == "anthropic" else OPENAI_ENV,
        model=model,
    )


def _request(routing_token: str = "planning") -> InternalRequest:
    return InternalRequest(
        model=routing_token,
        messages=[Message("user", "hello")],
        max_tokens=16,
    )


class _CaptureTransport:
    def __init__(self, reply: bytes):
        self._reply = reply
        self.calls: list[dict] = []

    def __call__(self, url, *, data, headers, timeout):
        self.calls.append({"url": url, "data": data, "headers": dict(headers)})
        return self._reply


def _anthropic_reply(text: str = "ok") -> bytes:
    return json.dumps({"content": [{"type": "text", "text": text}]}).encode()


def _openai_reply(text: str = "ok") -> bytes:
    return json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": text}}]}
    ).encode()


# ── Config: parsing ───────────────────────────────────────────────────────────

def test_tier_model_field_parses_when_present(tmp_path):
    """A tier TOML with ``model = \"...\"`` must parse into ``tier.model``."""
    cfg_text = """\
[router]
mapping_version = "test.model.0"

[[router.tiers]]
id            = "cloud"
base_url      = "https://api.anthropic.com"
dialect       = "anthropic"
context_limit = 200000
privacy       = "cloud"
tool_support  = true
auth_env      = "ANTHROPIC_API_KEY"
model         = "claude-opus-4-20250514"
"""
    p = tmp_path / "cfg.toml"
    p.write_text(cfg_text, encoding="utf-8")
    cfg = load(str(p))
    assert cfg.tier("cloud").model == "claude-opus-4-20250514"


def test_tier_model_field_absent_gives_none(tmp_path):
    """A tier TOML without ``model`` must produce ``tier.model is None`` (back-compat)."""
    cfg_text = """\
[router]
mapping_version = "test.model.1"

[[router.tiers]]
id            = "fast-local"
base_url      = "http://127.0.0.1:30001/v1"
dialect       = "openai"
context_limit = 32768
privacy       = "local"
tool_support  = true
auth_env      = "ANVIL_FAST_LOCAL_KEY"
"""
    p = tmp_path / "cfg.toml"
    p.write_text(cfg_text, encoding="utf-8")
    cfg = load(str(p))
    assert cfg.tier("fast-local").model is None


def test_tier_model_non_string_raises(tmp_path):
    """A non-string ``model`` value must raise ConfigError."""
    cfg_text = """\
[router]
mapping_version = "test.model.2"

[[router.tiers]]
id            = "cloud"
base_url      = "https://api.anthropic.com"
dialect       = "anthropic"
context_limit = 200000
privacy       = "cloud"
tool_support  = true
auth_env      = "ANTHROPIC_API_KEY"
model         = 42
"""
    p = tmp_path / "cfg.toml"
    p.write_text(cfg_text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load(str(p))


# ── Backend: tier.model preferred over routing token ─────────────────────────

def test_anthropic_tier_model_overrides_routing_token(monkeypatch):
    """CloudBackend (anthropic): tier.model replaces the routing token upstream."""
    monkeypatch.setenv(ANTHROPIC_ENV, FAKE_KEY)
    transport = _CaptureTransport(_anthropic_reply())
    tier = _tier(model="claude-opus-4-20250514", dialect="anthropic")
    backend = CloudBackend(tier, env={ANTHROPIC_ENV: FAKE_KEY}, transport=transport)

    list(backend.generate(_request("planning")))

    body = json.loads(transport.calls[0]["data"])
    assert body["model"] == "claude-opus-4-20250514", (
        f"expected tier model, got {body['model']!r}"
    )
    assert body["model"] != "planning"


def test_openai_tier_model_overrides_routing_token(monkeypatch):
    """CloudBackend (openai): tier.model replaces the routing token upstream."""
    transport = _CaptureTransport(_openai_reply())
    tier = _tier(model="gpt-4o-2024-08-06", dialect="openai")
    backend = CloudBackend(
        tier, env={OPENAI_ENV: FAKE_KEY}, transport=transport
    )

    list(backend.generate(_request("quick-edit")))

    body = json.loads(transport.calls[0]["data"])
    assert body["model"] == "gpt-4o-2024-08-06"
    assert body["model"] != "quick-edit"


def test_tier_without_model_falls_back_to_request_model(monkeypatch):
    """CloudBackend: tier without model -> request.model forwarded (back-compat)."""
    transport = _CaptureTransport(_anthropic_reply())
    tier = _tier(model=None, dialect="anthropic")
    backend = CloudBackend(tier, env={ANTHROPIC_ENV: FAKE_KEY}, transport=transport)

    list(backend.generate(_request("claude-opus-4-20250514")))

    body = json.loads(transport.calls[0]["data"])
    assert body["model"] == "claude-opus-4-20250514"


# ── RelayBackend: inherits the fix ────────────────────────────────────────────

def test_relay_backend_tier_model_overrides_routing_token():
    """RelayBackend (local tier): tier.model is preferred over the routing token."""
    from anvil_serving.router.serve import RelayBackend

    transport = _CaptureTransport(_openai_reply())
    local_tier = _tier(model="Qwen/Qwen3-Coder-30B", dialect="openai", privacy="local")
    backend = RelayBackend(local_tier, env={}, transport=transport)

    list(backend.generate(_request("heavy")))

    body = json.loads(transport.calls[0]["data"])
    assert body["model"] == "Qwen/Qwen3-Coder-30B"
    assert body["model"] != "heavy"


def test_relay_backend_without_model_falls_back_to_request_model():
    """RelayBackend (local tier): no tier.model -> request.model forwarded (back-compat)."""
    from anvil_serving.router.serve import RelayBackend

    transport = _CaptureTransport(_openai_reply())
    local_tier = _tier(model=None, dialect="openai", privacy="local")
    backend = RelayBackend(local_tier, env={}, transport=transport)

    list(backend.generate(_request("local-model-name")))

    body = json.loads(transport.calls[0]["data"])
    assert body["model"] == "local-model-name"


# ── Example config: local tiers set model= so the preset token is not forwarded ──

def test_example_toml_local_tiers_have_model():
    """example.toml sets ``model`` on every local tier (genericity:R001): the router
    forwards the served-model-name upstream, not the routing token, so the shipped
    default config does not 404 on the first request. (The RelayBackend fallback to
    ``request.model`` when a tier omits ``model`` is still covered by
    ``test_relay_backend_without_model_falls_back_to_request_model`` above.)"""
    cfg = load(str(REPO_ROOT / "configs" / "example.toml"))
    for tier in cfg.tiers:
        if tier.privacy == "local":
            assert tier.model, (
                f"tier {tier.id!r} is missing model=; the preset token would be "
                f"forwarded upstream and 404 (genericity:R001)"
            )
