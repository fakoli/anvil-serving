"""The protocol-standard front door (T001 / milestone M0).

ONE HTTP server that accepts BOTH wire dialects and streams responses back in
the caller's native SSE framing:

* ``POST /v1/messages``          -> Anthropic Messages (named-event SSE)
* ``POST /v1/chat/completions``  -> OpenAI Chat Completions (``data:`` / ``[DONE]``)

Each request is parsed into a single :class:`~anvil_serving.router.internal.InternalRequest`
and passed through to ONE injectable :class:`~anvil_serving.router.internal.Backend`.
Intent routing, multiple tiers, and verify/fallback are LATER tasks — not here.

Design constraints (binding):

* **Stdlib only** — ``http.server`` (``ThreadingHTTPServer`` +
  ``BaseHTTPRequestHandler``). No FastAPI/uvicorn/aiohttp.
* **Bind 127.0.0.1, never localhost** (``localhost`` triggers a ~21s IPv6 stall
  on Windows — a documented project gotcha).
* **Flush after every SSE chunk** so streaming is real, not buffered.

Streaming uses HTTP/1.1 ``Transfer-Encoding: chunked`` (what real OpenAI /
Anthropic servers do): each SSE event is written as one chunk and flushed
immediately, and the stream is terminated by the ``0\\r\\n\\r\\n`` trailer so the
client knows the body ended without relying on connection close. HTTP/1.0
clients (no chunked encoding) get a close-delimited stream instead, mirroring
``multiplexer.relay``.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterable, Optional

from .backends import EchoBackend
from .decision_log import summarize_decisions
from .dialects import Dialect
from .dialects.anthropic import AnthropicDialect
from .dialects.openai import OpenAIDialect
from .discovery import models_payload, ROUTE_ENDPOINT
from .intent import PRESETS, Preset, WORK_CLASS_TO_PRESET
from .internal import (
    Backend,
    DialectError,
    InternalRequest,
    NoAvailableTierError,
    normalize_messages,
)

# Path -> dialect. Stateless, so module-level singletons are fine.
_ROUTES = {
    "/v1/chat/completions": OpenAIDialect(),
    "/v1/messages": AnthropicDialect(),
}
DECISION_SUMMARY_ENDPOINT = "/v1/decisions"
TRANSITION_ENDPOINT = "/v1/admin/transition"

# --------------------------------------------------------------------------- #
# Resource caps (DoS protection)
# --------------------------------------------------------------------------- #

#: Maximum request body size in bytes.  Requests whose Content-Length exceeds
#: this value are rejected with 413 before any body bytes are read.
#: Default: 32 MiB.  Override via the ``ANVIL_MAX_BODY_BYTES`` env var.
MAX_BODY_BYTES: int = int(os.environ.get("ANVIL_MAX_BODY_BYTES", str(32 * 1024 * 1024)))

#: Maximum number of requests being processed concurrently.  When all slots are
#: occupied, the next incoming request receives an immediate 503.
#: Default: 64.  Override via the ``ANVIL_MAX_CONCURRENCY`` env var.
MAX_CONCURRENCY: int = int(os.environ.get("ANVIL_MAX_CONCURRENCY", "64"))

#: Shared bounded semaphore across all handler instances/threads.
_CONCURRENCY_LIMIT: threading.BoundedSemaphore = threading.BoundedSemaphore(
    MAX_CONCURRENCY
)

# Drain waits must not consume a data-plane request slot.  This small separate
# pool bounds administrative waits for the single-operator deployment.
_MANAGEMENT_LIMIT: threading.BoundedSemaphore = threading.BoundedSemaphore(4)

#: Maximum bytes to drain from the socket after sending a 413 (or a response
#: to an oversized GET body) before closing, so the OS can push the response
#: through before the RST that accompanies a close with unread data.
#: Non-blocking: only what is already in the OS receive buffer is consumed.
_CLOSE_DRAIN_CAP: int = 64 * 1024  # 64 KiB

# Pre-compiled pattern: a valid Content-Length is one or more ASCII digits,
# nothing else (no sign, underscores, whitespace, or Unicode digits).
_DIGIT_RE: re.Pattern = re.compile(r"[0-9]+")

# --------------------------------------------------------------------------- #
# Front-door token auth (ADR-0004 / T001)
# --------------------------------------------------------------------------- #
#: Liveness route that stays unauthenticated even when auth is on (container
#: healthchecks must not need a token).
_HEALTHZ_PATH = "/healthz"


def _extract_bearer_token(headers) -> Optional[str]:
    """Pull the caller's token from ``Authorization: Bearer <t>`` or ``x-api-key: <t>``.

    Returns ``None`` when neither header is present or the ``Authorization``
    header isn't the ``Bearer`` scheme -- callers treat ``None`` as "no token
    supplied", which always fails auth (never compared as an empty string).
    """
    auth_header = headers.get("Authorization")
    if auth_header:
        scheme, _, value = auth_header.partition(" ")
        if scheme.strip().lower() == "bearer" and value.strip():
            return value.strip()
        return None
    api_key = headers.get("x-api-key")
    if api_key and api_key.strip():
        return api_key.strip()
    return None


def _make_handler(backend: Backend, timeout: Optional[float],
                  presets: Iterable[Preset], exhaustion_status: int = 503,
                  auth_token: Optional[str] = None):
    class FrontDoorHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        # Generic server token: no software name or version disclosed.
        server_version = "anvil"
        # Finite idle timeout: with HTTP/1.1 keep-alive on a ThreadingHTTPServer,
        # an abandoned connection would otherwise pin a daemon thread blocked in
        # readline() forever (thread/FD leak). A timed-out read makes
        # handle_one_request set close_connection and the thread exits.
        # (Set to the configured value just below the class.)

        # --- helpers ---------------------------------------------------------
        def _json(self, status: int, obj, extra_headers=None) -> None:
            payload = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            # Advertise close whenever we're closing (request asked for it, or we
            # forced it on a framing error) so the client doesn't reuse the socket.
            if self.close_connection:
                self.send_header("Connection", "close")
            if extra_headers:
                for _h_name, _h_val in extra_headers.items():
                    self.send_header(_h_name, _h_val)
            self.end_headers()
            self.wfile.write(payload)

        def _authenticated(self) -> bool:
            """True if this request carries a valid token, or auth is off.

            ``auth_token`` is resolved ONCE at server start (threaded in from
            ``serve.py``) — never re-read from ``os.environ`` per request.
            Comparison is constant-time (``hmac.compare_digest``) so response
            timing can't be used to guess the token byte-by-byte. The token
            itself is never logged: on failure only a generic message is sent.
            """
            if auth_token is None:
                return True  # [server].auth_env unset -> auth OFF (back-compat)
            supplied = _extract_bearer_token(self.headers)
            if supplied is None:
                return False
            return hmac.compare_digest(
                supplied.encode("utf-8"), auth_token.encode("utf-8")
            )

        def _no_tier_response(self, e: NoAvailableTierError,
                              dialect: Optional[Dialect] = None) -> None:
            """Render a :class:`NoAvailableTierError` to the right HTTP status.

            An ``over_context`` error (the request exceeds every tier's
            context window) is a CALLER problem -> a clean **413 Payload Too
            Large**, refusing the over-sized request up front instead of
            forwarding it to a too-small tier that would 400 at the model. Every
            other kind (``unbound`` / ``exhausted``) is a server availability
            signal -> the operator-configured ``exhaustion_status`` (default 503,
            the keyless-handoff signal per ADR-0001 §Mechanism). The detailed
            message is logged server-side; the client gets a sanitised generic
            message (tier identities / remediation are internal-operator info).
            """
            if getattr(e, "kind", None) == "over_context":
                print(f"[anvil] 413 over-context request: {e}", file=sys.stderr)
                self._error(
                    413, "payload_too_large",
                    "request exceeds the context window of every available "
                    "tier; send a smaller request",
                    dialect=dialect,
                )
                return
            print(f"[anvil] {exhaustion_status} no available tier: {e}",
                  file=sys.stderr)
            self._error(
                exhaustion_status, "service_unavailable",
                "no quality-gated tier is available for this request",
                dialect=dialect,
            )

        def _error(self, status: int, etype: str, message: str,
                   dialect: Optional[Dialect] = None) -> None:
            # Errors raised once a dialect is known speak that dialect's native
            # error envelope; pre-routing/transport errors use a generic shape.
            if dialect is not None:
                self._json(status, dialect.render_error(status, etype, message))
            else:
                self._json(status, {"error": {"type": etype, "message": message}})

        def _fail_framing(self, status: int, etype: str, message: str,
                          drainable: bool, n: int,
                          dialect: Optional[Dialect] = None) -> None:
            """Respond to a pre-body framing/routing error WITHOUT desyncing a
            pooled keep-alive socket (RFC 7230 3.3.3/6.6).

            If the body length is known (``drainable``) AND within the body-size
            cap, drain it so the socket stays in sync and the connection survives
            — closing instead would, on an unread body, trigger an RST on Windows
            that truncates this very response.  If the body exceeds the cap or the
            length is undeterminable (Transfer-Encoding / unparseable
            Content-Length), we cannot safely realign, so we must close.

            When a dialect is known (request routed to a known path), the error
            envelope is rendered in that dialect's native shape.
            """
            if drainable:
                if 0 < n <= MAX_BODY_BYTES:
                    try:
                        self.rfile.read(n)
                    except Exception:
                        self.close_connection = True  # short read -> can't realign
                elif n > MAX_BODY_BYTES:
                    # Body is too large to drain safely; close instead.
                    self.close_connection = True
            else:
                self.close_connection = True
            self._error(status, etype, message, dialect=dialect)

        def _write_sse(self, dialect: Dialect, request) -> None:
            """Stream the backend's deltas as native SSE, flushed per event.

            HTTP/1.1: chunked transfer-encoding. HTTP/1.0 (no chunked support):
            close-delimited — raw frames, then close the socket (mirrors
            ``multiplexer.relay``).
            """
            chunked = self.request_version == "HTTP/1.1"

            # Resolve the backend's delta stream BEFORE committing a 200 so a
            # PRE-stream routing failure can still be a real HTTP error. A routing
            # backend (T012) selects its tier eagerly at generate()-call time, so
            # if no quality-gated tier is bound for this work class it raises
            # NoAvailableTierError HERE — before any header is sent — and we
            # answer a clean exhaustion status instead of a 200 + empty/truncated
            # body. (For a plain generator backend this call is a no-op: its body
            # runs lazily, so the M0 mid-stream close-on-error contract below is
            # unchanged.)
            #
            # Any other exception from generate() is also surfaced as a clean 500
            # here, before the 200 is committed, so the client always sees a real
            # HTTP error status for pre-stream failures.
            try:
                deltas = backend.generate(request)
            except NoAvailableTierError as e:
                # Keyless handoff contract (ADR-0001 §Mechanism, advise-and-defer:T004).
                # exhaustion_status is the signal the gateway's transport failover
                # keys on to re-run this request on the native subscription provider.
                # Default 503 is chosen because OpenClaw classifies it as "overloaded"
                # (transport failover) — pending live validation in T005. Configurable
                # via [router].exhaustion_status to match a different gateway's trigger.
                # C3 holds: the commit-window fully buffered + verified the local
                # tier's response before raising — nothing local was streamed before
                # this point. This is an honest availability signal, not partial output.
                # (An over_context error is instead a clean 413 — see
                # _no_tier_response — refused up front, never forwarded.)
                self._no_tier_response(e, dialect=dialect)
                return
            except Exception as e:
                print(f"[anvil] 500 backend error in generate(): {e}", file=sys.stderr)
                self._error(500, "internal_error", "internal error", dialect=dialect)
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            if chunked:
                # Reflect the connection intent already computed from the request's
                # Connection header (RFC 7230 6.1): if the client sent
                # `Connection: close`, BaseHTTPRequestHandler set
                # close_connection=True and we must NOT override it back to
                # keep-alive (that would leave a close-expecting, read-to-EOF
                # client hanging until its own timeout).
                self.send_header("Connection",
                                 "close" if self.close_connection else "keep-alive")
                self.send_header("Transfer-Encoding", "chunked")
            else:
                # HTTP/1.0 has no chunked encoding; the body is close-delimited.
                self.close_connection = True
                self.send_header("Connection", "close")
            self.end_headers()

            # Close-on-error contract (M0): the 200 + headers are already sent, so
            # a backend exception mid-stream cannot become an error status. It
            # propagates, the socket closes, and the client sees a truncated
            # stream (IncompleteRead) rather than a hang. This is intentional;
            # mid-stream verify/fallback is a later task (T008/T009).
            # Resolve the optional structured-fields accessor from the backend
            # BEFORE building the frame iterator.  The accessor is invoked by
            # dialect.stream() AFTER all deltas are consumed — so it is safe to
            # pass a bound method reference here; it does not call through yet.
            # Backends that don't expose get_last_structured (EchoBackend, tests)
            # get get_structured=None → dialect falls back to hardcoded defaults
            # (text-path stays byte-identical, regression-safe) (#42 / #52).
            _get_structured_fn = getattr(backend, "get_last_structured", None)
            frames = dialect.stream(
                request,
                deltas,
                get_structured=_get_structured_fn if callable(_get_structured_fn) else None,
            )
            try:
                for frame in frames:
                    if not frame:
                        continue  # never emit a zero-length chunk (ends the stream)
                    if chunked:
                        # One write per event (wfile is unbuffered): size + frame + CRLF.
                        self.wfile.write(b"%x\r\n" % len(frame) + frame + b"\r\n")
                    else:
                        self.wfile.write(frame)
                    self.wfile.flush()  # push each SSE event to the client immediately
                if chunked:
                    self.wfile.write(b"0\r\n\r\n")  # chunked terminator
                    self.wfile.flush()
            finally:
                # Deterministically close the generator chain on disconnect/error
                # so backends release resources (real backends hold upstream
                # sockets); generator .close() is idempotent.
                for gen in (frames, deltas):
                    closer = getattr(gen, "close", None)
                    if closer is not None:
                        try:
                            closer()
                        except Exception:
                            pass

        # --- /v1/route decision endpoint (advise-and-defer:T007) ------------
        def _handle_route_decision(self, body: dict) -> None:
            """Handle ``POST /v1/route`` — routing brain, no backend serving.

            Accepts a ``/v1/chat/completions``-shaped body plus an optional
            ``signals`` object ``{work_class, token_estimate, urgency}`` (T007
            contract).  If ``signals.work_class`` is present it is mapped to
            the corresponding preset id via :data:`WORK_CLASS_TO_PRESET` (so
            ``"bounded-edit"`` -> ``"quick-edit"``) before being set as the
            request ``model``; ``intent.resolve`` then classifies it as a
            declared-preset rather than inferring from the message content.

            Calls ``backend.decide(request)`` if the backend exposes that
            method (i.e. is a :class:`~anvil_serving.router.serve.RoutingBackend`).
            A plain echo/static backend returns 503 ("routing brain not
            available") — intentional: the decision endpoint has no meaning
            without a routing backend.

            **Never** calls ``backend.generate()`` or any tier backend.
            """
            # Extract signals override (optional; must be a dict).
            raw_signals = body.get("signals")
            signals: dict = raw_signals if isinstance(raw_signals, dict) else {}

            # Build an InternalRequest from the completions-shaped body.
            messages = normalize_messages(body.get("messages") or [])
            model = str(body.get("model") or "")
            max_tokens = body.get("max_tokens")

            # signals.work_class: map taxonomy key → preset id so
            # intent.resolve() treats it as a declared-preset.
            if signals.get("work_class"):
                wc = str(signals["work_class"])
                model = WORK_CLASS_TO_PRESET.get(wc, wc)

            # signals.token_estimate: optional max_tokens override.
            if signals.get("token_estimate") is not None:
                try:
                    max_tokens = int(signals["token_estimate"])
                except (TypeError, ValueError):
                    pass  # ignore non-integer; keep body's max_tokens

            request = InternalRequest(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                stream=False,
                dialect="route",
                raw=dict(body),
            )

            # The routing brain lives on RoutingBackend.decide(); a plain
            # static/echo backend has no decide() → this endpoint has no
            # meaning without a routing backend.
            decide_fn = getattr(backend, "decide", None)
            if decide_fn is None:
                self._error(
                    503, "service_unavailable",
                    "routing brain not available (server is not configured "
                    "with a routing backend)",
                )
                return

            try:
                result = decide_fn(request)
            except NoAvailableTierError as e:
                # over_context -> a clean 413 (caller sent too large a request):
                # the decision endpoint must not imply a too-small tier is
                # servable. Every other kind keeps /v1/route's existing 503.
                if getattr(e, "kind", None) == "over_context":
                    print(
                        f"[anvil] 413 /v1/route over-context request: {e}",
                        file=sys.stderr,
                    )
                    self._error(
                        413, "payload_too_large",
                        "request exceeds the context window of every available "
                        "tier; send a smaller request",
                    )
                    return
                print(
                    f"[anvil] 503 /v1/route no available tier: {e}",
                    file=sys.stderr,
                )
                self._error(
                    503, "service_unavailable",
                    "no quality-gated tier is available for this request",
                )
                return
            except Exception as e:
                print(f"[anvil] 500 /v1/route error: {e}", file=sys.stderr)
                self._error(500, "internal_error", "internal error")
                return

            self._json(200, result)

        def _transition_status(self, tier_id: Optional[str]) -> None:
            status_fn = getattr(backend, "transition_status", None)
            if not callable(status_fn):
                self._error(503, "service_unavailable", "transition management unavailable")
                return
            try:
                self._json(200, status_fn(tier_id))
            except (KeyError, ValueError):
                self._error(400, "invalid_transition", "invalid transition request")
            except Exception:  # noqa: BLE001 - management errors are content-free
                self._error(503, "transition_failed", "transition operation failed")

        def _handle_transition(self, body: dict) -> None:
            action = body.get("action")
            tier_id = body.get("tier_id")
            if action == "status":
                self._transition_status(tier_id if isinstance(tier_id, str) else None)
                return
            if not isinstance(tier_id, str) or not tier_id:
                self._error(400, "invalid_transition", "tier_id is required")
                return
            if action in ("quiesce", "readmit"):
                if body.get("confirm") is not True or body.get("dry_run", True) is not False:
                    self._json(200, {
                        "applied": False,
                        "dry_run": True,
                        "action": action,
                        "tier_id": tier_id,
                    })
                    return
            try:
                if action == "quiesce":
                    fn = getattr(backend, "quiesce_tier")
                    result = fn(tier_id, str(body.get("reason") or "promotion"))
                elif action == "drain":
                    timeout_value = body.get("timeout")
                    if isinstance(timeout_value, bool) or not isinstance(timeout_value, (int, float)):
                        raise ValueError("bad timeout")
                    result = getattr(backend, "drain_tier")(tier_id, float(timeout_value))
                elif action == "readmit":
                    result = getattr(backend, "readmit_tier")(tier_id)
                else:
                    self._error(400, "invalid_transition", "unsupported transition action")
                    return
            except (AttributeError, KeyError, ValueError):
                self._error(400, "invalid_transition", "invalid transition request")
                return
            except Exception:  # noqa: BLE001 - never expose upstream/container errors
                self._error(503, "transition_failed", "transition operation failed")
                return
            self._json(200, {"applied": True, "action": action, "result": result})

        # --- routes ----------------------------------------------------------
        def do_GET(self) -> None:
            route = self.path.split("?", 1)[0].rstrip("/")
            if route == TRANSITION_ENDPOINT:
                if not _MANAGEMENT_LIMIT.acquire(blocking=False):
                    self._error(503, "server_busy", "management busy; try again later")
                    return
                try:
                    if auth_token is None:
                        self._error(404, "not_found", f"no route {route}")
                    elif not self._authenticated():
                        self._error(401, "authentication_error", "invalid or missing API key")
                    else:
                        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                        raw_tier = (query.get("tier_id") or [None])[0]
                        self._transition_status(raw_tier)
                finally:
                    _MANAGEMENT_LIMIT.release()
                return
            # Acquire the concurrency semaphore FIRST — before draining any body
            # bytes — so a flood of GETs with large bodies is gated here, not
            # outside the limiter (mirrors do_POST).
            if not _CONCURRENCY_LIMIT.acquire(blocking=False):
                self.close_connection = True
                self._error(503, "server_busy", "server busy; try again later")
                return
            try:
                # Drain any unexpected request body to keep the keep-alive socket
                # in sync.  GETs are conventionally bodyless; a caller that sends
                # one leaves bytes on the wire that would desync the connection for
                # the next pipelined request.  Drain up to MAX_BODY_BYTES before
                # the response; if the claimed length exceeds the cap, close after
                # the response and do a bounded post-response drain (see below) so
                # the response is not RST-truncated.
                # A GET carrying Transfer-Encoding has a body we do not decode
                # (mirrors the POST-side 411 stance): the byte count is
                # unknowable, so the keep-alive socket cannot be realigned —
                # close after the response instead of desyncing the next
                # pipelined request.
                _get_has_te = bool(self.headers.get_all("Transfer-Encoding"))
                if _get_has_te:
                    self.close_connection = True
                # TE takes precedence over Content-Length (RFC 7230 3.3.3): with
                # TE present the body is NOT CL-framed, so skip the CL drain.
                cl_get = None if _get_has_te else self.headers.get("Content-Length")
                _post_drain = False  # True when we must drain after the response
                if cl_get is not None:
                    if _DIGIT_RE.fullmatch(cl_get):
                        get_n = int(cl_get)
                        if 0 < get_n <= MAX_BODY_BYTES:
                            try:
                                self.rfile.read(get_n)
                            except Exception:
                                self.close_connection = True
                        elif get_n > MAX_BODY_BYTES:
                            # Too large to drain up-front: close after the
                            # response + bounded post-response drain so the
                            # 200/404/405 reaches the client before RST.
                            self.close_connection = True
                            _post_drain = True
                    else:
                        self.close_connection = True

                route = self.path.split("?", 1)[0].rstrip("/")
                # Every route requires auth EXCEPT GET /healthz -- container
                # healthchecks must not need a token (ADR-0004). The `/health`
                # alias is NOT exempt: only the literal `/healthz` path is.
                if route != _HEALTHZ_PATH and not self._authenticated():
                    self._json(401, {"error": {
                        "type": "authentication_error",
                        "message": "invalid or missing API key",
                    }})
                elif route in ("/healthz", "/health"):
                    self._json(200, {
                        "status": "ok",
                        "dialects": sorted(d.name for d in _ROUTES.values()),
                        # Advertise the decision endpoint alongside dialect routes
                        # (ROUTE_ENDPOINT from discovery.py, T007).
                        "routes": sorted(list(_ROUTES) + [ROUTE_ENDPOINT, DECISION_SUMMARY_ENDPOINT]),
                    })
                elif route == "/v1/models":
                    # Preset discovery: list the configured presets (intent tokens)
                    # as OpenAI-shaped "models" so a harness model picker can find
                    # them. Derived from the canonical presets passed in.
                    self._json(200, models_payload(presets))
                elif route == DECISION_SUMMARY_ENDPOINT:
                    query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    raw_limit = (query.get("limit") or ["20"])[0]
                    try:
                        limit = int(raw_limit)
                    except (TypeError, ValueError):
                        self._error(400, "invalid_request", "limit must be an integer")
                        return
                    if limit < 1 or limit > 500:
                        self._error(400, "invalid_request", "limit must be between 1 and 500")
                        return
                    decision_log = getattr(backend, "_decision_log", None)
                    if decision_log is None:
                        routing = getattr(self.server, "anvil_routing", None)
                        decision_log = getattr(routing, "_decision_log", None)
                    if decision_log is None:
                        summary = summarize_decisions([], limit=limit)
                    elif hasattr(decision_log, "summary"):
                        summary = decision_log.summary(limit=limit)
                    else:
                        summary = summarize_decisions(getattr(decision_log, "records", ()), limit=limit)
                    self._json(200, summary)
                elif route in _ROUTES or route == ROUTE_ENDPOINT:
                    # Known POST-only route requested with GET → 405 Method Not
                    # Allowed with Allow: POST (RFC 7231 §6.5.5).  Use the
                    # dialect's native error envelope when one is bound to the
                    # path; /v1/route (not dialect-backed) uses the generic shape.
                    _dial405: Optional[Dialect] = _ROUTES.get(route)
                    _msg405 = "this route only accepts POST requests"
                    self._json(
                        405,
                        (_dial405.render_error(405, "method_not_allowed", _msg405)
                         if _dial405 is not None
                         else {"error": {"type": "method_not_allowed",
                                         "message": _msg405}}),
                        extra_headers={"Allow": "POST"},
                    )
                else:
                    self._error(404, "not_found", f"no route {self.path}")

                # Post-response bounded drain for oversized GET bodies: flush the
                # response then take whatever body bytes are already in the OS
                # receive buffer (non-blocking, no waiting for more data) so TCP
                # can deliver the response before the RST from close_connection.
                # Mirrors the 413 drain in _post_inner — same RST-race mitigation.
                if _post_drain:
                    try:
                        self.wfile.flush()
                        _t = self.connection.gettimeout()
                        try:
                            self.connection.settimeout(0.0)
                            self.connection.recv(_CLOSE_DRAIN_CAP)
                        except OSError:
                            pass
                        finally:
                            self.connection.settimeout(_t)
                    except Exception:
                        pass
            finally:
                _CONCURRENCY_LIMIT.release()

        def do_POST(self) -> None:
            route = self.path.split("?", 1)[0].rstrip("/")
            if route == TRANSITION_ENDPOINT:
                if not _MANAGEMENT_LIMIT.acquire(blocking=False):
                    self._error(503, "server_busy", "management busy; try again later")
                    return
                try:
                    self._post_inner()
                finally:
                    _MANAGEMENT_LIMIT.release()
                return
            # Acquire the concurrency semaphore before doing any work.  The
            # request line and headers are already parsed by handle_one_request,
            # so we can send a proper 503 if the server is saturated.
            if not _CONCURRENCY_LIMIT.acquire(blocking=False):
                self.close_connection = True
                # self.path is already known here; resolve the dialect so the
                # 503 envelope speaks the caller's native wire format (Anthropic
                # vs OpenAI) rather than always the generic shape.
                _busy_dialect: Optional[Dialect] = _ROUTES.get(
                    self.path.split("?", 1)[0].rstrip("/")
                )
                self._error(503, "server_busy", "server busy; try again later",
                            dialect=_busy_dialect)
                return
            try:
                self._post_inner()
            finally:
                _CONCURRENCY_LIMIT.release()

        def _post_inner(self) -> None:
            """Core POST dispatch, called under the concurrency semaphore."""
            # Normalize exactly like do_GET (query split + trailing-slash strip)
            # so POST /v1/messages/ routes the same as POST /v1/messages instead
            # of 404ing on the slash.
            path = self.path.split("?", 1)[0].rstrip("/")
            is_transition = path == TRANSITION_ENDPOINT

            # --- Strict framing: gather and validate headers -----------------
            #
            # Transfer-Encoding: we don't decode chunked bodies.  Reject ANY
            # request carrying a TE header, including obfuscated/duplicate ones
            # (get_all to catch request-smuggling vectors).
            te_all = self.headers.get_all("Transfer-Encoding") or []
            has_te = bool(te_all)

            # Content-Length: strict parse.
            # * Duplicate CL headers: reject (request smuggling, RFC 7230 3.3.2).
            # * Non-ASCII-digit CL (underscores, sign, whitespace, Unicode):
            #   reject.  Python's int() is too permissive here.
            cl_all = self.headers.get_all("Content-Length") or []
            dup_cl = len(cl_all) > 1
            # Use the single raw CL string (or None if absent / duplicated).
            cl_raw = cl_all[0] if len(cl_all) == 1 else None

            n = 0
            cl_invalid = False
            if not has_te and not dup_cl and cl_raw is not None:
                if _DIGIT_RE.fullmatch(cl_raw):
                    n = int(cl_raw)
                    # Non-negative guaranteed by the ^[0-9]+$ match.
                else:
                    cl_invalid = True

            # drainable: the body byte count is known, valid, and we can
            # safely read exactly n bytes to realign the keep-alive stream.
            # (Even drainable bodies are capped at MAX_BODY_BYTES inside
            # _fail_framing to bound the drain work.)
            drainable = not has_te and not dup_cl and not cl_invalid

            # --- Auth check (ADR-0004 / T001) ---------------------------------
            #
            # Every POST route requires auth (there is no POST /healthz route
            # at all -- the only unauthenticated route is GET /healthz, handled
            # in do_GET). Checked BEFORE the route/dialect lookup below so an
            # unauthenticated caller gets a uniform 401 regardless of whether
            # the path exists (no route-enumeration oracle). Drains the body
            # via the same drainable/n framing state just computed, exactly
            # like the other pre-body rejections below, so a pooled keep-alive
            # socket stays in sync.
            if not self._authenticated():
                self._fail_framing(
                    401, "authentication_error", "invalid or missing API key",
                    drainable, n,
                )
                return
            if is_transition and auth_token is None:
                self._fail_framing(404, "not_found", f"no route {path}", drainable, n)
                return

            # --- Route check (establishes dialect for dialect-aware errors) --
            #
            # /v1/route is the standalone routing-decision endpoint (T007).
            # It is NOT dialect-backed — it accepts a completions-shaped body
            # and returns a decision JSON — so it is treated separately from
            # the SSE-streaming dialect routes.  Pre-body framing errors on
            # this path use the generic error envelope (dialect=None); the
            # body is parsed and dispatched AFTER the shared framing checks.
            is_route_decision = (path == ROUTE_ENDPOINT)

            dialect: Optional[Dialect] = _ROUTES.get(path)
            if dialect is None and not is_route_decision and not is_transition:
                # Unknown route — drain body if well-framed to keep the
                # keep-alive socket in sync, then 404.
                self._fail_framing(404, "not_found", f"no route {path}",
                                   drainable, n)
                return

            # --- Reject any Transfer-Encoding header (411) -------------------
            if has_te:
                self._fail_framing(
                    411, "invalid_request",
                    "chunked request bodies are unsupported; send Content-Length",
                    drainable=False, n=0, dialect=dialect,
                )
                return

            # --- Reject duplicate Content-Length headers (400) ---------------
            if dup_cl:
                self._fail_framing(
                    400, "invalid_request",
                    "duplicate Content-Length headers",
                    drainable=False, n=0, dialect=dialect,
                )
                return

            # --- Reject non-digit / malformed Content-Length (400) -----------
            if cl_invalid:
                self._fail_framing(
                    400, "invalid_request",
                    f"invalid Content-Length: {cl_all[0]!r}",
                    drainable=False, n=0, dialect=dialect,
                )
                return

            # --- Body size cap: reject before reading (413) ------------------
            if n > MAX_BODY_BYTES:
                self._fail_framing(
                    413, "payload_too_large",
                    "request body too large",
                    drainable=False, n=0, dialect=dialect,
                )
                # Bounded drain: flush the 413 response, then take whatever body
                # bytes are already in the OS receive buffer (non-blocking — no
                # waiting for more data) so TCP can push the 413 to the client
                # before the RST from close_connection.  We cannot safely drain
                # the full body (it may be gigabytes); this read is capped and
                # non-blocking so it never blocks the thread.
                try:
                    self.wfile.flush()
                    _t = self.connection.gettimeout()
                    try:
                        self.connection.settimeout(0.0)
                        self.connection.recv(_CLOSE_DRAIN_CAP)
                    except OSError:
                        pass
                    finally:
                        self.connection.settimeout(_t)
                except Exception:
                    pass
                return

            raw = self.rfile.read(n) if n else b""  # body drained from here on
            try:
                body = json.loads(raw or b"{}")
            except Exception as e:
                self._error(400, "invalid_request", f"bad JSON body: {e}",
                            dialect=dialect)
                return
            if not isinstance(body, dict):
                self._error(400, "invalid_request", "body must be a JSON object",
                            dialect=dialect)
                return

            if is_transition:
                self._handle_transition(body)
                return

            # /v1/route: run the routing brain and return the decision;
            # never parse with a dialect and never call backend.generate().
            if is_route_decision:
                self._handle_route_decision(body)
                return

            try:
                request = dialect.parse_request(body)
            except DialectError as e:  # dialect-specific rejection (e.g. max_tokens)
                self._error(e.status, e.etype, e.message, dialect=dialect)
                return
            except Exception as e:  # other malformed but JSON-parseable body
                self._error(400, "invalid_request", f"bad request: {e}",
                            dialect=dialect)
                return

            if request.stream:
                self._write_sse(dialect, request)
            else:
                # Symmetric with the streaming close-on-error contract: a real
                # backend can raise here, so surface a clean error in the
                # dialect's native envelope rather than dropping the request with
                # a traceback.
                try:
                    text = "".join(backend.generate(request))
                    # Read structured fields AFTER the generator is drained so the
                    # backend's thread-local is fully populated (#42 / #52).
                    # Falls through to dialect defaults (structured=None) when the
                    # backend doesn't expose get_last_structured (text-path safety).
                    _get_fn = getattr(backend, "get_last_structured", None)
                    _structured = _get_fn() if callable(_get_fn) else None
                    payload = dialect.render(request, text, structured=_structured)
                except NoAvailableTierError as e:
                    # Keyless handoff contract — see the streaming path above for
                    # the full rationale (ADR-0001 §Mechanism, advise-and-defer:T004).
                    # over_context -> 413; unbound/exhausted -> exhaustion_status.
                    self._no_tier_response(e, dialect=dialect)
                    return
                except Exception as e:
                    # Unexpected backend fault: log the detail server-side; send
                    # a generic message so internal state is not disclosed.
                    print(f"[anvil] 500 backend error: {e}", file=sys.stderr)
                    self._error(500, "internal_error", "internal error",
                                dialect=dialect)
                    return
                self._json(200, payload)

        def log_message(self, *args) -> None:  # keep the server quiet
            pass

    FrontDoorHandler.timeout = timeout
    return FrontDoorHandler


def make_server(host: str = "127.0.0.1", port: int = 8000,
                backend: Optional[Backend] = None,
                timeout: Optional[float] = 120,
                presets: Optional[Iterable[Preset]] = None,
                exhaustion_status: int = 503,
                auth_token: Optional[str] = None) -> ThreadingHTTPServer:
    """Build (but do not start) the front-door server.

    Pass ``port=0`` to bind an ephemeral port (read it back from
    ``server.server_address[1]``). ``backend`` defaults to :class:`EchoBackend`.
    ``timeout`` is the per-connection idle read timeout in seconds (finite by
    default so abandoned keep-alive sockets can't leak threads/FDs); pass
    ``None`` to disable. ``presets`` are the work-class tokens ``GET /v1/models``
    advertises; defaults to the canonical :data:`~anvil_serving.router.intent.PRESETS`
    (injectable like ``backend`` for tests). ``exhaustion_status`` is the HTTP
    status returned when all quality-gated tiers are exhausted (default 503 —
    the keyless handoff signal; see :class:`~anvil_serving.router.config.RouterConfig`
    and ADR-0001 §Mechanism). ``auth_token`` is the RESOLVED secret (already read
    from ``os.environ`` once by the caller, e.g. ``serve.build_server`` from
    ``[server].auth_env`` — ADR-0004 / T001); ``None`` (the default) means auth
    is OFF, identical to today's behaviour. Every route requires this token
    (``Authorization: Bearer <t>`` or ``x-api-key: <t>``, constant-time compare)
    except ``GET /healthz``. Call ``server.serve_forever()`` (typically on a
    background thread) to run.
    """
    if backend is None:
        backend = EchoBackend()
    if presets is None:
        presets = PRESETS
    httpd = ThreadingHTTPServer(
        (host, port),
        _make_handler(backend, timeout, presets, exhaustion_status, auth_token),
    )
    httpd.daemon_threads = True  # don't let connection threads block shutdown
    return httpd


def serve(host: str = "127.0.0.1", port: int = 8000,
          backend: Optional[Backend] = None,
          timeout: Optional[float] = 120,
          auth_token: Optional[str] = None) -> None:
    """Build and run the front door until interrupted."""
    httpd = make_server(host, port, backend, timeout, auth_token=auth_token)
    actual_host, actual_port = httpd.server_address[:2]
    print(f"anvil-serving front door on http://{actual_host}:{actual_port}  "
          f"(routes: {', '.join(sorted(_ROUTES))})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    # Default echo backend so the verification curl works out of the box.
    serve("127.0.0.1", 8000, EchoBackend())
