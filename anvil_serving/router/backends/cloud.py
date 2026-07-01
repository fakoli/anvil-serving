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
import threading
import urllib.error
import urllib.request
from dataclasses import replace
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional

from ..config import (
    DIALECT_ANTHROPIC,
    DIALECT_OPENAI,
    PRIVACY_CLOUD,
    PRIVACY_LOCAL,
    ConfigError,
    Tier,
)
from ..internal import InternalRequest, StructuredResult
from .local import split_into_deltas

#: transport(url, *, data, headers, timeout) -> response body bytes.
Transport = Callable[..., bytes]

# Dialects this backend can speak to a cloud provider.
_SUPPORTED_DIALECTS = (DIALECT_OPENAI, DIALECT_ANTHROPIC)

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
                       timeout: float, max_bytes: Optional[int] = None) -> bytes:
    """Default stdlib transport: POST ``data`` to ``url`` and return the body.

    Wraps :func:`urllib.request.urlopen`. Errors are re-raised as
    :class:`CloudBackendError` with a message that cannot contain the key (the
    request object — which holds the auth header — is never stringified).

    ``max_bytes`` caps the response body: reads at most ``max_bytes`` bytes from
    the wire and raises :class:`CloudBackendError` if the body is larger. This
    prevents a runaway cloud provider from OOM-ing the router via an unexpectedly
    huge response body. ``None`` means unlimited (the previous behaviour).
    """
    req = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if max_bytes is not None:
                # Read one byte more than the cap to detect overflow without
                # buffering the entire body. A legitimate body of exactly
                # max_bytes bytes reads max_bytes+1 chars and len == max_bytes,
                # so it is allowed; only len > max_bytes is an overflow.
                chunk = resp.read(max_bytes + 1)
                if len(chunk) > max_bytes:
                    raise CloudBackendError(
                        f"cloud response body exceeded {max_bytes} bytes"
                    )
                return chunk
            return resp.read()
    except CloudBackendError:
        raise
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


# --------------------------------------------------------------------------- #
# genericity:T002 — optional GET /v1/models auto-derive for a local tier
# --------------------------------------------------------------------------- #
#: GET transport(url, *, headers, timeout) -> response body bytes. Distinct from
#: the POST-shaped `Transport` above (no request body).
DiscoveryTransport = Callable[..., bytes]

#: Default probe timeout — short and independent of the (possibly much longer)
#: relay/cloud request timeout, since this is a cheap startup discovery call.
_DEFAULT_DISCOVERY_TIMEOUT = 5.0


def _urlopen_get_transport(
    url: str, *, headers: Mapping[str, str], timeout: float
) -> bytes:
    """Default stdlib GET transport for ``/v1/models`` discovery."""
    req = urllib.request.Request(url, headers=dict(headers), method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _models_endpoint(base_url: str) -> str:
    """``{base_url}/v1/models``, normalizing a base_url that may or may not
    already carry the ``/v1`` segment (mirrors :meth:`CloudBackend._endpoint`)."""
    base = base_url.rstrip("/")
    if not base.endswith(_OPENAI_VERSION_SEGMENT):
        base += _OPENAI_VERSION_SEGMENT
    return base + "/models"


def discover_single_model(
    tier: Tier,
    *,
    transport: Optional[DiscoveryTransport] = None,
    timeout: float = _DEFAULT_DISCOVERY_TIMEOUT,
) -> Tier:
    """Resolve ``tier.model`` from ``GET {base_url}/v1/models`` when it is unset.

    * ``tier.model`` already set, or a non-``local`` tier -> returned unchanged,
      no probe issued. **Explicit config always wins**; this is purely a local-
      serve convenience for a tier that never got a ``model =`` line.
    * Upstream reachable and advertises exactly ONE model -> the tier is
      returned with that id adopted as ``model`` (so the router forwards the
      served-model-name upstream instead of the routing token — genericity:R001).
    * Upstream reachable but advertises ZERO or MORE THAN ONE model -> raises
      :class:`ConfigError` naming the tier, the URL, and the candidate ids: an
      ambiguous/empty catalog is a real misconfiguration the operator must
      resolve by setting ``model =`` explicitly — it must fail loudly at
      startup, not silently 404 (or route to the wrong model) on every request.
    * A NETWORK or parse failure (connection refused, timeout, DNS, non-JSON
      body, unexpected shape) -> **non-fatal**: the tier is returned unchanged
      (``model`` stays ``None``, a warning is printed to stderr) so a cold or
      still-booting local serve does not prevent the router from starting; the
      existing ``request.model`` fallback (the routing token forwarded as-is)
      still applies for that tier until it is reachable.
    """
    if tier.model is not None or tier.privacy != PRIVACY_LOCAL:
        return tier

    url = _models_endpoint(tier.base_url)
    _transport = transport or _urlopen_get_transport
    try:
        raw = _transport(url, headers={}, timeout=timeout)
        data = json.loads(raw or b"{}")
    except Exception as exc:  # noqa: BLE001 - any transport/parse fault is non-fatal here
        print(
            f"[anvil-serving] tier {tier.id!r}: /v1/models auto-derive skipped "
            f"({type(exc).__name__}: {exc}); model stays unset for now (falls "
            f"back to forwarding the request's routing token)",
            file=sys.stderr,
            flush=True,
        )
        return tier

    entries = data.get("data") if isinstance(data, Mapping) else None
    ids: List[str] = []
    if isinstance(entries, list):
        for e in entries:
            if isinstance(e, Mapping) and isinstance(e.get("id"), str) and e["id"]:
                ids.append(e["id"])

    if len(ids) != 1:
        raise ConfigError(
            f"tier {tier.id!r}: /v1/models at {url!r} advertised {len(ids)} "
            f"model(s) {ids!r}; auto-derive requires exactly one candidate. "
            f"Set model = \"<served-model-name>\" explicitly on this tier."
        )
    return replace(tier, model=ids[0])


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
        max_response_bytes: Optional[int] = None,
        _require_key: bool = True,
    ):
        # ``_require_key`` is a PRIVATE opt-out for the local-relay subclass
        # (T012 RelayBackend): a privacy=local tier reuses this dialect machinery
        # but must NOT require a credential and must NOT trip the cloud-only gate
        # (local vLLM/SGLang servers usually need no auth). It stays True for all
        # real cloud use, so the fail-fast credential contract below is unchanged.
        if _require_key and tier.privacy != PRIVACY_CLOUD:
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
        require_credential = _require_key or (tier.privacy == PRIVACY_CLOUD)
        if require_credential and (not key or not key.strip()):
            raise MissingCredentialError(tier_id=tier.id, env_var=tier.auth_env)

        self._tier = tier
        self._key = (key or "").strip()  # private; never logged, never in __repr__
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes
        self._transport: Transport = transport or _urlopen_transport
        # Per-thread structured-result store: populated during generate() so the
        # response_view_factory (T012) and the dialect layer (#42) can read
        # finish_reason + tool_calls after the stream is drained.
        self._thread_local: threading.local = threading.local()

    # ------------------------------------------------------------------ #
    # Structured-result side channel (#42 / #52)
    # ------------------------------------------------------------------ #
    def get_last_structured(self) -> Optional[StructuredResult]:
        """Return the structured fields from the most recent ``generate()`` on this thread.

        Thread-safe: ``threading.local`` isolates per-request state so concurrent
        connections to the same backend never observe each other's result.
        Returns ``None`` before the first call or when the generate() stream was
        interrupted before reaching the structured-extraction point.
        """
        return getattr(self._thread_local, "last_result", None)

    def _extract_structured(self, raw: bytes) -> StructuredResult:
        """Extract ``finish_reason`` and normalized ``tool_calls`` from the upstream response.

        Called inside ``generate()`` after the raw body is received, before text
        extraction. Never raises — parse failures return an empty
        :class:`~anvil_serving.router.internal.StructuredResult`.
        """
        try:
            data = json.loads(raw or b"{}")
        except (ValueError, TypeError):
            return StructuredResult()
        if not isinstance(data, Mapping):
            return StructuredResult()

        if self._tier.dialect == DIALECT_ANTHROPIC:
            finish_reason = data.get("stop_reason")
            blocks = data.get("content") or []
            tool_calls: Optional[List[Dict[str, Any]]] = None
            if isinstance(blocks, list):
                tc_list = []
                for b in blocks:
                    if isinstance(b, Mapping) and b.get("type") == "tool_use":
                        tc_list.append({
                            "name": str(b.get("name") or ""),
                            "id": str(b.get("id") or ""),
                            "arguments": b.get("input"),  # already-parsed dict
                        })
                if tc_list:
                    tool_calls = tc_list
            return StructuredResult(finish_reason=finish_reason, tool_calls=tool_calls)

        # openai-compatible
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], Mapping):
            return StructuredResult()
        first = choices[0]
        finish_reason = first.get("finish_reason")
        message = first.get("message") or {}
        raw_tc = message.get("tool_calls") if isinstance(message, Mapping) else None
        tool_calls = None
        if isinstance(raw_tc, list):
            tc_list = []
            for tc in raw_tc:
                if not isinstance(tc, Mapping):
                    continue
                fn = tc.get("function") or {}
                tc_list.append({
                    "name": str(fn.get("name") or ""),
                    "id": str(tc.get("id") or ""),
                    "arguments": fn.get("arguments") or "",  # JSON string
                })
            if tc_list:
                tool_calls = tc_list
        return StructuredResult(finish_reason=finish_reason, tool_calls=tool_calls)

    # ------------------------------------------------------------------ #
    # Backend protocol
    # ------------------------------------------------------------------ #
    def generate(self, request: InternalRequest) -> Iterator[str]:
        url = self._endpoint()
        headers = self._headers()
        data = json.dumps(self._build_body(request)).encode("utf-8")
        try:
            # Pass max_bytes to the default _urlopen_transport (keyword-only).
            # Custom transports that do not accept max_bytes are called without it
            # so they remain backward-compatible; the post-read size guard below
            # still applies to their output.
            if self._transport is _urlopen_transport and self._max_response_bytes is not None:
                raw = self._transport(
                    url, data=data, headers=headers, timeout=self._timeout,
                    max_bytes=self._max_response_bytes,
                )
            else:
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
        # Post-read cap for custom transports (or the default when they have
        # already returned the full body). Guards against a runaway response that
        # slipped past the read-cap in the transport layer.
        if self._max_response_bytes is not None and len(raw) > self._max_response_bytes:
            raise CloudBackendError(
                f"cloud response body exceeded max_response_bytes="
                f"{self._max_response_bytes} (tier={self._tier.id!r})"
            )
        # Populate structured side channel BEFORE text extraction so the
        # thread-local is always set (even if _extract_text() raises). The
        # response_view_factory and dialect layer read this after the stream
        # is drained to build a live ResponseView (#42 / #52).
        self._thread_local.last_result = self._extract_structured(raw)
        text = self._extract_text(raw)
        for delta in split_into_deltas(text):
            yield delta

    # ------------------------------------------------------------------ #
    # request construction (the auth-bearing seam the tests inspect)
    # ------------------------------------------------------------------ #
    def _endpoint(self) -> str:
        base = self._tier.base_url.rstrip("/")
        if self._tier.dialect == DIALECT_ANTHROPIC:
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
        if self._tier.dialect == DIALECT_ANTHROPIC:
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
        if self._tier.dialect == DIALECT_ANTHROPIC:
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
            # genericity:T003 -- per-tier extra_body merged verbatim (e.g. a local
            # server's thinking-disable knob). Applied LAST so an operator who
            # explicitly configures a colliding key gets the override they asked
            # for; absent extra_body this is a no-op (no regression).
            body.update(self._tier.extra_body or {})
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
        # genericity:T003 -- see the Anthropic branch above for the rationale.
        body.update(self._tier.extra_body or {})
        return body

    def _extract_text(self, raw: bytes) -> str:
        """Extract the completion text from the provider's response bytes.

        Distinguishes two kinds of absent text:

        * **Structurally malformed** — a required field is absent or has the wrong
          type (e.g. ``"choices"`` key missing, ``choices[0]`` is not an object,
          ``"message"`` absent).  These raise :class:`CloudBackendError` rather
          than silently returning ``""`` so the verify gate sees an error/fallback
          instead of a spurious empty answer.  The structural detail is printed to
          stderr; the exception carries a sanitised, client-safe message.
        * **Legitimately empty** — the provider returned a well-formed response
          with no text content (e.g. ``"choices": []``, or ``"content": ""``).
          These return ``""`` — a valid completion with zero tokens.
        """
        try:
            data = json.loads(raw or b"{}")
        except (ValueError, TypeError) as e:
            raise CloudBackendError(f"cloud provider returned non-JSON body: {e}") from None
        if not isinstance(data, Mapping):
            raise CloudBackendError("cloud provider returned a non-object body")

        def _malformed(detail: str) -> CloudBackendError:
            print(
                f"[anvil-serving] cloud tier {self._tier.id!r}: malformed response: {detail}",
                file=sys.stderr,
                flush=True,
            )
            return CloudBackendError(
                f"cloud provider returned a malformed response "
                f"(tier={self._tier.id!r}; see stderr for detail)"
            )

        if self._tier.dialect == DIALECT_ANTHROPIC:
            if "content" not in data:
                raise _malformed("'content' field absent") from None
            blocks = data["content"]
            if not isinstance(blocks, list):
                raise _malformed(
                    f"'content' is not a list (got {type(blocks).__name__!r})"
                ) from None
            # blocks == [] is a valid empty completion; filter for text blocks only.
            parts: List[str] = [
                str(b.get("text") or "")
                for b in blocks
                if isinstance(b, Mapping) and b.get("type") == "text"
            ]
            return "".join(parts)

        # openai-compatible
        if "choices" not in data:
            raise _malformed("'choices' field absent") from None
        choices = data["choices"]
        if not isinstance(choices, list):
            raise _malformed(f"'choices' is not a list (got {type(choices).__name__!r})") from None
        if not choices:
            return ""  # valid: server returned an empty candidate set (legitimately empty)
        first = choices[0]
        if not isinstance(first, Mapping):
            raise _malformed(f"choices[0] is not an object (got {type(first).__name__!r})") from None
        if "message" not in first:
            raise _malformed("choices[0] missing 'message' field") from None
        message = first["message"]
        if not isinstance(message, Mapping):
            raise _malformed(
                f"choices[0].message is not an object (got {type(message).__name__!r})"
            ) from None
        return str(message.get("content") or "")

    def __repr__(self) -> str:  # never leak the key
        return (
            f"CloudBackend(tier={self._tier.id!r}, dialect={self._tier.dialect!r}, "
            f"auth_env={self._tier.auth_env!r})"
        )
