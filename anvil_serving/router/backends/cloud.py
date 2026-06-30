"""Cloud-tier backend: outbound Anthropic / OpenAI-compatible inference (T006).

:class:`CloudBackend` implements the :class:`~anvil_serving.router.internal.Backend`
protocol against a remote provider described by a :class:`~anvil_serving.router.config.Tier`.
It speaks the tier's ``dialect`` to the tier's ``base_url`` and authenticates with
the key resolved from the tier's ``auth_env`` ENV-VAR NAME — the config carries
the *name*, never the secret (see :mod:`anvil_serving.router.config`).

Credential policy (the gate):

* The key is resolved **at construction** via ``os.environ[<tier.auth_env>]``.
* A missing/empty key raises :class:`MissingCredentialError` (a typed
  :class:`~anvil_serving.router.config.ConfigError`) naming the tier and the env
  var — at startup, NOT deep in a request, and never a silent no-auth call.
* The key is set on the outbound ``Authorization`` (OpenAI) or ``x-api-key``
  (Anthropic) header and is NEVER logged or placed in ``__repr__``.

Stdlib-only HTTP: the default transport uses :mod:`urllib.request`. The transport
is an injectable seam (``transport=``) so tests run hermetically with NO network.

Scope notes:

* **Provider model resolution.** When a tier is configured with a ``model`` field
  (the concrete provider model id, e.g. ``"claude-opus-4-20250514"``), that value
  is preferred over ``request.model`` so that routing tokens (e.g. ``"planning"``,
  ``"quick-edit"``) are never forwarded verbatim to the upstream provider, which
  would cause a 4xx rejection.  When ``tier.model`` is absent (``None``),
  ``request.model`` is used as before — backward-compatible for configs that do
  not set the field.
* **Dialect branching.** The per-dialect logic is split across
  :meth:`CloudBackend._endpoint` / ``_headers`` / ``_build_body`` / ``_extract_text``;
  a per-dialect adapter object would encapsulate it. Out of scope for T006.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional

from ..config import ConfigError, Tier
from ..internal import InternalRequest
from .local import split_into_deltas

#: transport(url, *, data, headers, timeout) -> response body bytes.
Transport = Callable[..., bytes]

# Dialects this backend can speak to a cloud provider.
_SUPPORTED_DIALECTS = ("openai", "anthropic")

# Anthropic's provider-relative endpoint path (base_url is the bare host).
_ANTHROPIC_PATH = "/v1/messages"
# OpenAI-compatible servers expose the Chat Completions API under /v1.
_OPENAI_VERSION_SEGMENT = "/v1"
_OPENAI_PATH = "/chat/completions"

# Anthropic requires this header; pin the stable Messages API version.
_ANTHROPIC_VERSION = "2023-06-01"

# Anthropic requires max_tokens; supply a floor when the inbound request (e.g. an
# OpenAI caller, where it's optional) didn't set one.
_DEFAULT_MAX_TOKENS = 1024


class MissingCredentialError(ConfigError):
    """No API key for a cloud tier: its ``auth_env`` env var is unset/empty.

    Raised at backend construction so a misconfigured deployment fails fast and
    loudly instead of issuing an unauthenticated upstream request. Carries the
    offending ``tier_id`` and ``env_var`` for the operator.
    """

    def __init__(self, *, tier_id: str, env_var: str):
        self.tier_id = tier_id
        self.env_var = env_var
        super().__init__(
            f"cloud tier {tier_id!r} has no API key: environment variable "
            f"{env_var!r} is unset or empty. Set it before starting the router "
            f"(e.g. export {env_var}=<your key>); the router refuses to send "
            f"unauthenticated cloud requests."
        )


class CloudBackendError(RuntimeError):
    """An upstream/transport failure talking to the cloud provider.

    Carries a sanitized message only — never the request headers or the key.
    """


def _urlopen_transport(url: str, *, data: bytes, headers: Mapping[str, str],
                       timeout: float) -> bytes:
    """Default stdlib transport: POST ``data`` to ``url`` and return the body.

    Wraps :func:`urllib.request.urlopen`. Errors are re-raised as
    :class:`CloudBackendError` with a message that cannot contain the key (the
    request object — which holds the auth header — is never stringified).
    """
    req = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:  # status carries no secret
        raise CloudBackendError(
            f"cloud provider returned HTTP {e.code} {e.reason}"
        ) from None
    except urllib.error.URLError as e:
        # Log the full reason server-side (may include upstream host / TLS detail)
        # and surface only a generic, client-safe message so the upstream hostname
        # and TLS internals cannot leak to callers via the 500 path.
        print(
            f"[anvil-serving] cloud upstream transport error: {e.reason}",
            file=sys.stderr,
            flush=True,
        )
        raise CloudBackendError("cloud upstream request failed") from None


class CloudBackend:
    """Route an :class:`InternalRequest` to a remote provider for one tier.

    Implements the :class:`~anvil_serving.router.internal.Backend` protocol:
    :meth:`generate` yields the completion as plain text deltas. The cloud call
    is non-streaming upstream; the reply is split into deltas so the front
    door's streaming path stays genuinely multi-chunk.
    """

    def __init__(
        self,
        tier: Tier,
        *,
        env: Optional[Mapping[str, str]] = None,
        transport: Optional[Transport] = None,
        timeout: float = 120.0,
        _require_key: bool = True,
    ):
        # ``_require_key`` is a PRIVATE opt-out for the local-relay subclass
        # (T012 RelayBackend): a privacy=local tier reuses this dialect machinery
        # but must NOT require a credential and must NOT trip the cloud-only gate
        # (local vLLM/SGLang servers usually need no auth). It stays True for all
        # real cloud use, so the fail-fast credential contract below is unchanged.
        if _require_key and tier.privacy != "cloud":
            # Defensive: this backend authenticates against a remote provider; a
            # local tier should be served by the in-process backends.
            raise ConfigError(
                f"CloudBackend requires a cloud tier; tier {tier.id!r} is "
                f"privacy={tier.privacy!r}"
            )
        if tier.dialect not in _SUPPORTED_DIALECTS:
            raise ConfigError(
                f"tier {tier.id!r}: CloudBackend cannot speak dialect {tier.dialect!r}"
            )

        environ: Mapping[str, str] = os.environ if env is None else env
        key = environ.get(tier.auth_env)
        # For CLOUD tiers, the credential is ALWAYS required regardless of
        # ``_require_key``.  The ``_require_key=False`` opt-out is only honored for
        # non-cloud (local relay) tiers where the upstream server typically needs no
        # auth.  Silently omitting the key on a cloud tier would send an
        # unauthenticated request to a paid provider — we must fail fast instead.
        # Unset, empty, OR whitespace-only (e.g. a trailing-newline key from
        # `$(cat keyfile)`) -> fail fast, named, typed. A blank key would otherwise
        # become a `x-api-key: ' '` 401 deep in a request, or make urllib raise an
        # opaque ValueError on the header value — never the clear startup error.
        require_credential = _require_key or (tier.privacy == "cloud")
        if require_credential and (not key or not key.strip()):
            raise MissingCredentialError(tier_id=tier.id, env_var=tier.auth_env)

        self._tier = tier
        self._key = (key or "").strip()  # private; never logged, never in __repr__
        self._timeout = timeout
        self._transport: Transport = transport or _urlopen_transport

    # ------------------------------------------------------------------ #
    # Backend protocol
    # ------------------------------------------------------------------ #
    def generate(self, request: InternalRequest) -> Iterator[str]:
        url = self._endpoint()
        headers = self._headers()
        data = json.dumps(self._build_body(request)).encode("utf-8")
        try:
            raw = self._transport(url, data=data, headers=headers, timeout=self._timeout)
        except urllib.error.URLError as exc:
            # A custom transport may raise URLError directly (the default
            # _urlopen_transport already converts it, but this is the safety net).
            # Log the full reason server-side; raise a generic, client-safe message.
            print(
                f"[anvil-serving] cloud tier {self._tier.id!r} upstream error: "
                f"{exc.reason}",
                file=sys.stderr,
                flush=True,
            )
            raise CloudBackendError(
                f"cloud upstream request failed (tier={self._tier.id!r})"
            ) from None
        text = self._extract_text(raw)
        for delta in split_into_deltas(text):
            yield delta

    # ------------------------------------------------------------------ #
    # request construction (the auth-bearing seam the tests inspect)
    # ------------------------------------------------------------------ #
    def _endpoint(self) -> str:
        base = self._tier.base_url.rstrip("/")
        if self._tier.dialect == "anthropic":
            return base + _ANTHROPIC_PATH
        # openai-compatible: the Chat Completions API lives under /v1. The config
        # only checks for a scheme, so base_url may or may not already carry the
        # /v1 segment; normalize both forms (``https://api.openai.com`` and
        # ``https://api.openai.com/v1``) to ``…/v1/chat/completions`` so a bare
        # host doesn't 404 every request.
        if not base.endswith(_OPENAI_VERSION_SEGMENT):
            base += _OPENAI_VERSION_SEGMENT
        return base + _OPENAI_PATH

    def _headers(self) -> Dict[str, str]:
        """Outbound headers, including the auth header built from the env key."""
        headers = {"Content-Type": "application/json"}
        if self._tier.dialect == "anthropic":
            headers["x-api-key"] = self._key
            headers["anthropic-version"] = _ANTHROPIC_VERSION
        else:  # openai-compatible
            headers["Authorization"] = f"Bearer {self._key}"
        return headers

    def _build_body(self, request: InternalRequest) -> Dict[str, Any]:
        # Prefer the tier's configured concrete provider model id over the routing
        # token in request.model. A routing token (e.g. "planning", "quick-edit")
        # forwarded verbatim to the upstream provider causes a 4xx rejection; the
        # tier's model field holds the real provider model name (close #43).
        upstream_model = self._tier.model or request.model
        if self._tier.dialect == "anthropic":
            # Anthropic's messages array is user/assistant only; the system
            # prompt rides the top-level `system` field.
            msgs = [
                {"role": m.role, "content": m.content}
                for m in request.messages
                if m.role != "system"
            ]
            body: Dict[str, Any] = {
                "model": upstream_model,
                "messages": msgs,
                "max_tokens": request.max_tokens or _DEFAULT_MAX_TOKENS,
                "stream": False,
            }
            if request.system:
                body["system"] = request.system
            if request.temperature is not None:
                body["temperature"] = request.temperature
            return body

        # openai-compatible: the system prompt rides as a role=system message.
        msgs = [{"role": m.role, "content": m.content} for m in request.messages]
        # Forward request.system faithfully. The OpenAI dialect leaves the system
        # message IN messages (so it's already present); an Anthropic-origin
        # request carries the system prompt ONLY on `.system` (no system message),
        # and dropping it here would silently lose the instruction. Prepend it
        # unless a system message is already present (avoids duplication).
        if request.system and not any(m.role == "system" for m in request.messages):
            msgs.insert(0, {"role": "system", "content": request.system})
        body = {
            "model": upstream_model,
            "messages": msgs,
            "stream": False,
        }
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            body["temperature"] = request.temperature
        return body

    def _extract_text(self, raw: bytes) -> str:
        try:
            data = json.loads(raw or b"{}")
        except (ValueError, TypeError) as e:
            raise CloudBackendError(f"cloud provider returned non-JSON body: {e}") from None
        if not isinstance(data, Mapping):
            raise CloudBackendError("cloud provider returned a non-object body")

        if self._tier.dialect == "anthropic":
            blocks = data.get("content") or []
            parts: List[str] = [
                str(b.get("text") or "")
                for b in blocks
                if isinstance(b, Mapping) and b.get("type") == "text"
            ]
            return "".join(parts)

        # openai-compatible
        choices = data.get("choices") or []
        if choices and isinstance(choices[0], Mapping):
            message = choices[0].get("message") or {}
            if isinstance(message, Mapping):
                return str(message.get("content") or "")
        return ""

    def __repr__(self) -> str:  # never leak the key
        return (
            f"CloudBackend(tier={self._tier.id!r}, dialect={self._tier.dialect!r}, "
            f"auth_env={self._tier.auth_env!r})"
        )
