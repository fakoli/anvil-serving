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
* **Sampling-field forwarding (fx-sampling).** ``_build_body`` forwards
  ``top_p`` and stop sequences (dialect-correct names: OpenAI ``stop``,
  Anthropic ``stop_sequences``) from ``InternalRequest``, plus same-dialect-only
  ``top_k`` (Anthropic) and ``presence_penalty``/``frequency_penalty`` (OpenAI) —
  all only when present, so an absent field never changes the built body
  (regression-pinned). Deliberately NOT forwarded: ``logit_bias``, ``seed``,
  ``user``, ``metadata`` — these are provider-account/session-scoped (billing
  attribution, abuse tracking, deterministic-replay opt-in) rather than
  generation-quality knobs a coding harness sets, so blanket passthrough would
  leak caller-side identifiers/state into the upstream call for low harness
  value. A tier's ``extra_body`` is applied LAST and can override any of these
  (documented precedence — see the ``extra_body`` note below).
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
from ..dialects.translate import (
    anthropic_messages_to_openai,
    anthropic_tool_choice_to_openai,
    anthropic_tools_to_openai,
    has_image_artifacts,
    has_tool_artifacts,
    openai_messages_to_anthropic,
    openai_tool_choice_to_anthropic,
    openai_tools_to_anthropic,
)
from ..internal import InternalRequest, StructuredResult
from .local import split_into_deltas
from .sse import (
    AnthropicStreamAssembler,
    OpenAIStreamAssembler,
    iter_sse_events,
)

#: transport(url, *, data, headers, timeout) -> response body bytes.
Transport = Callable[..., bytes]

#: stream_transport(url, *, data, headers, timeout) -> an OPEN response object:
#: line-iterable (SSE framing), with ``.headers`` (mapping-like ``get``) and
#: ``.close()``.  The default wraps ``urllib.request.urlopen``; tests inject a
#: canned-bytes fake.  Distinct from ``Transport`` (which buffers the body).
StreamTransport = Callable[..., Any]

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


def _urlopen_stream_transport(url: str, *, data: bytes,
                              headers: Mapping[str, str],
                              timeout: float):
    """Default streaming transport: POST and return the OPEN response.

    The caller iterates the response line-by-line (SSE events arrive as they
    are generated upstream) and closes it. Errors are sanitized exactly like
    :func:`_urlopen_transport` — the request object (which holds the auth
    header) is never stringified.
    """
    req = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:  # status carries no secret
        raise CloudBackendError(
            f"cloud provider returned HTTP {e.code} {e.reason}"
        ) from None
    except urllib.error.URLError as e:
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
    :meth:`generate` yields the completion as plain text deltas. A STREAMING
    request (``request.stream``) issues a real ``stream: true`` upstream call
    and yields the model's own deltas as they arrive (TTFT is genuinely
    upstream-bound); a non-streaming request keeps the buffered call, whose
    reply is split into deltas so the front door's streaming path stays
    multi-chunk. Custom buffered transports (hermetic tests) never engage the
    streaming path unless a ``stream_transport`` companion is injected.
    """

    def __init__(
        self,
        tier: Tier,
        *,
        env: Optional[Mapping[str, str]] = None,
        transport: Optional[Transport] = None,
        timeout: float = 120.0,
        max_response_bytes: Optional[int] = None,
        stream_transport: Optional[StreamTransport] = None,
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
        # Streaming seam. A caller-injected stream_transport always enables the
        # streaming path (hermetic tests); otherwise streaming engages only on
        # the DEFAULT buffered transport — a custom buffered transport implies
        # a hermetic/test setup that must stay off the network.
        self._stream_transport: Optional[StreamTransport] = stream_transport
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

        # Real token accounting, normalized to Anthropic wire names. None when
        # the upstream reported none (the dialects then keep their estimates).
        def _usage_from(raw_usage: Any, in_key: str, out_key: str) -> Optional[Dict[str, int]]:
            if not isinstance(raw_usage, Mapping):
                return None
            i, o = raw_usage.get(in_key), raw_usage.get(out_key)
            if (
                isinstance(i, int) and not isinstance(i, bool) and i >= 0
                and isinstance(o, int) and not isinstance(o, bool) and o >= 0
            ):
                return {"input_tokens": i, "output_tokens": o}
            return None

        if self._tier.dialect == DIALECT_ANTHROPIC:
            usage = _usage_from(data.get("usage"), "input_tokens", "output_tokens")
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
            return StructuredResult(
                finish_reason=finish_reason, tool_calls=tool_calls, usage=usage,
            )

        # openai-compatible
        usage = _usage_from(data.get("usage"), "prompt_tokens", "completion_tokens")
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], Mapping):
            return StructuredResult(usage=usage)
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
        return StructuredResult(
            finish_reason=finish_reason, tool_calls=tool_calls, usage=usage,
        )

    # ------------------------------------------------------------------ #
    # Backend protocol
    # ------------------------------------------------------------------ #
    def generate(self, request: InternalRequest) -> Iterator[str]:
        """Dispatch: true streaming upstream for a streaming request, else buffered.

        Streaming engages when the caller asked to stream (``request.stream``)
        AND either a ``stream_transport`` was injected (hermetic tests) or this
        backend is on the default urllib transport (production). A custom
        BUFFERED transport with no stream companion keeps the old buffered
        path so existing hermetic setups never touch the network.
        """
        if request.stream and (
            self._stream_transport is not None
            or self._transport is _urlopen_transport
        ):
            return self._generate_streaming(request)
        return self._generate_buffered(request)

    def _generate_buffered(self, request: InternalRequest) -> Iterator[str]:
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

    def _generate_streaming(self, request: InternalRequest) -> Iterator[str]:
        """True streaming relay: ``stream: true`` upstream, SSE parsed
        incrementally, REAL model deltas yielded as they arrive.

        The per-dialect assembler (:mod:`.sse`) accumulates ``finish_reason``,
        tool calls, and usage alongside the text; the thread-local structured
        result is populated once the stream ends, exactly like the buffered
        path, so the dialect renderers and the verify chain see identical
        shapes. If the upstream ignores ``stream: true`` (or a proxy buffered
        it away) and answers plain JSON, the body is parsed via the buffered
        extractors instead — behaviourally a non-streaming reply, never an
        error. The response is always closed, including on client disconnect
        (``GeneratorExit``).
        """
        url = self._endpoint()
        headers = self._headers()
        body = self._build_body(request)
        # Ask the upstream to stream. extra_body always wins (documented
        # precedence): an operator who explicitly pinned `stream`/`stream_options`
        # keeps their override.
        extra = self._tier.extra_body or {}
        if "stream" not in extra:
            body["stream"] = True
        if (
            self._tier.dialect == DIALECT_OPENAI
            and body.get("stream") is True
            and "stream_options" not in extra
        ):
            # Standard since 2024 on OpenAI-compatible servers (incl. vLLM /
            # SGLang): the final chunk carries the REAL usage block.
            body["stream_options"] = {"include_usage": True}
        data = json.dumps(body).encode("utf-8")

        # Cleared up-front so a mid-stream interruption can never leave a STALE
        # result from a previous request on this thread.
        self._thread_local.last_result = None

        transport = self._stream_transport or _urlopen_stream_transport
        try:
            resp = transport(url, data=data, headers=headers, timeout=self._timeout)
        except CloudBackendError:
            raise
        except urllib.error.URLError as exc:
            # Safety net for injected transports (the default already converts).
            print(
                f"[anvil-serving] cloud tier {self._tier.id!r} upstream error: "
                f"{exc.reason}",
                file=sys.stderr,
                flush=True,
            )
            raise CloudBackendError(
                f"cloud upstream request failed (tier={self._tier.id!r})"
            ) from None

        try:
            resp_headers = getattr(resp, "headers", None)
            ctype = ""
            if resp_headers is not None:
                ctype = str(resp_headers.get("Content-Type") or "").lower()
            if "text/event-stream" not in ctype:
                # Upstream ignored stream:true — parse the plain JSON body with
                # the buffered extractors (same result, no streaming).
                raw = resp.read()
                if (self._max_response_bytes is not None
                        and len(raw) > self._max_response_bytes):
                    raise CloudBackendError(
                        f"cloud response body exceeded max_response_bytes="
                        f"{self._max_response_bytes} (tier={self._tier.id!r})"
                    )
                self._thread_local.last_result = self._extract_structured(raw)
                text = self._extract_text(raw)
                yield from split_into_deltas(text)
                return

            assembler = (
                AnthropicStreamAssembler()
                if self._tier.dialect == DIALECT_ANTHROPIC
                else OpenAIStreamAssembler()
            )
            total = 0
            for event, payload in iter_sse_events(resp):
                delta = assembler.feed(event, payload)
                if not delta:
                    continue
                if self._max_response_bytes is not None:
                    total += len(delta.encode("utf-8", "surrogatepass"))
                    if total > self._max_response_bytes:
                        raise CloudBackendError(
                            f"cloud response body exceeded max_response_bytes="
                            f"{self._max_response_bytes} (tier={self._tier.id!r})"
                        )
                yield delta
            self._thread_local.last_result = assembler.result()
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001 - best-effort close
                pass

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

        # Wire fidelity for tool-carrying requests: the flattened
        # request.messages drop tools/tool_choice and the tool_use/tool_result
        # history an agent loop rides on. When the raw wire body carries tool
        # structure, forward it — verbatim when the caller's dialect matches
        # this tier's, translated otherwise. Tool-free requests keep the
        # original flattened body byte-identical (regression safety).
        raw: Mapping[str, Any] = (
            request.raw if isinstance(request.raw, Mapping) else {}
        )
        preserve_tools = (
            request.dialect in _SUPPORTED_DIALECTS and has_tool_artifacts(raw)
        )
        # Wire fidelity for image-carrying requests (gpu-reservations:T011):
        # the flattened request.messages keep only text, so an OCR/vision
        # request relayed from the flattened form silently loses the image the
        # caller sent — the tier then answers a text-only prompt and the
        # failure is invisible. SAME-DIALECT only: the raw messages are
        # forwarded verbatim (like the same-dialect tool path). Cross-dialect
        # image translation is deliberately out of scope — such a request
        # keeps the pre-T011 flattened behaviour.
        preserve_images = (
            request.dialect in _SUPPORTED_DIALECTS
            and request.dialect == self._tier.dialect
            and has_image_artifacts(raw)
        )

        if self._tier.dialect == DIALECT_ANTHROPIC:
            # Anthropic's messages array is user/assistant only; the system
            # prompt rides the top-level `system` field.
            # preserve_images implies request.dialect == tier dialect, so it
            # always selects this verbatim branch, never the translated one.
            if preserve_images or (
                preserve_tools and request.dialect == DIALECT_ANTHROPIC
            ):
                msgs: List[Dict[str, Any]] = [
                    dict(m) for m in raw.get("messages") or ()
                    if isinstance(m, Mapping) and m.get("role") != "system"
                ]
            elif preserve_tools:
                msgs = openai_messages_to_anthropic(raw.get("messages"))
            else:
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
            # Sampling fields (fx-sampling): only set when the caller actually sent
            # them, so a tool-free / sampling-field-free request still builds the
            # exact byte-identical body as before this change (regression pin —
            # test_tool_free_request_body_is_unchanged / test_extra_body_absent_body_unchanged).
            if request.top_p is not None:
                body["top_p"] = request.top_p
            if request.stop:
                body["stop_sequences"] = request.stop
            # top_k is Anthropic-only (no OpenAI equivalent); forward it same-dialect
            # only — never invented for a translated OpenAI-origin request.
            if request.dialect == DIALECT_ANTHROPIC and raw.get("top_k") is not None:
                body["top_k"] = raw["top_k"]
            if preserve_tools:
                if request.dialect == DIALECT_ANTHROPIC:
                    tools = raw.get("tools")
                    choice = raw.get("tool_choice")
                else:
                    tools = openai_tools_to_anthropic(raw.get("tools"))
                    choice = openai_tool_choice_to_anthropic(raw.get("tool_choice"))
                if tools:
                    body["tools"] = tools
                if choice is not None:
                    body["tool_choice"] = choice
            # genericity:T003 -- per-tier extra_body merged verbatim (e.g. a local
            # server's thinking-disable knob). Applied LAST so an operator who
            # explicitly configures a colliding key gets the override they asked
            # for; absent extra_body this is a no-op (no regression). This is also
            # the documented precedence for the sampling fields above: a tier's
            # extra_body_defaults are SOFT (the request wins); extra_body is the HARD override.
            self._apply_tier_extra_body(body)
            return body

        # openai-compatible: the system prompt rides as a role=system message.
        # preserve_images implies same-dialect (see above), so it always takes
        # this verbatim branch.
        if preserve_images or (preserve_tools and request.dialect == DIALECT_OPENAI):
            msgs = [
                dict(m) for m in raw.get("messages") or ()
                if isinstance(m, Mapping)
            ]
        elif preserve_tools:
            msgs = anthropic_messages_to_openai(raw.get("messages"))
        else:
            msgs = [{"role": m.role, "content": m.content} for m in request.messages]
        # Forward request.system faithfully. The OpenAI dialect leaves the system
        # message IN messages (so it's already present); an Anthropic-origin
        # request carries the system prompt ONLY on `.system` (no system message),
        # and dropping it here would silently lose the instruction. Prepend it
        # unless a system message is already present (avoids duplication).
        if request.system and not any(
            (m.get("role") if isinstance(m, Mapping) else None) == "system"
            for m in msgs
        ):
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
        # Sampling fields (fx-sampling): only set when present -- see the
        # Anthropic branch above for the byte-identical-body rationale.
        if request.top_p is not None:
            body["top_p"] = request.top_p
        if request.stop:
            # OpenAI's wire form for a single stop string is the bare string, but
            # a list is always accepted, and InternalRequest.stop is already the
            # normalized list -- forward the list form, valid for 1..4 entries.
            body["stop"] = request.stop
        # presence_penalty / frequency_penalty are OpenAI-only (no Anthropic
        # equivalent); forward same-dialect only -- never invented for a
        # translated Anthropic-origin request.
        if request.dialect == DIALECT_OPENAI:
            if raw.get("presence_penalty") is not None:
                body["presence_penalty"] = raw["presence_penalty"]
            if raw.get("frequency_penalty") is not None:
                body["frequency_penalty"] = raw["frequency_penalty"]
            # reasoning_effort (gpt-oss / harmony): forward the CALLER's value verbatim, so it lands
            # in the body BEFORE _apply_tier_extra_body and thus OVERRIDES a tier's soft
            # extra_body_defaults reasoning_effort — this is what makes OpenClaw's per-message
            # reasoning selector actually take effect (a tier `extra_body` hard-override still wins).
            if raw.get("reasoning_effort") is not None:
                body["reasoning_effort"] = raw["reasoning_effort"]
        if preserve_tools:
            if request.dialect == DIALECT_OPENAI:
                tools = raw.get("tools")
                choice = raw.get("tool_choice")
            else:
                tools = anthropic_tools_to_openai(raw.get("tools"))
                choice = anthropic_tool_choice_to_openai(raw.get("tool_choice"))
            if tools:
                body["tools"] = tools
            if choice is not None:
                body["tool_choice"] = choice
        # genericity:T003 -- see the Anthropic branch above for the rationale.
        # extra_body_defaults are SOFT (the request wins); extra_body is the HARD override.
        self._apply_tier_extra_body(body)
        return body

    def _apply_tier_extra_body(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Apply the tier's SOFT defaults (request wins, via ``setdefault``) then the HARD
        ``extra_body`` (tier wins, via ``update``). A key present in both -> ``extra_body`` wins.
        This is what lets a tier set e.g. ``reasoning_effort`` as a DEFAULT that a caller (OpenClaw's
        reasoning selector) can override per request, without loosening the hard-override contract."""
        for k, v in (self._tier.extra_body_defaults or {}).items():
            body.setdefault(k, v)
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
