"""The protocol-standard front door (T001 / milestone M0).

ONE HTTP server that accepts BOTH wire dialects and streams responses back in
the caller's native SSE framing:

* ``POST /v1/messages``          -> Anthropic Messages (named-event SSE)
* ``POST /v1/chat/completions``  -> OpenAI Chat Completions (``data:`` / ``[DONE]``)

Plus, when the server is built with a
:class:`~anvil_serving.router.purpose.PurposeRouter` (``[[router.purpose_models]]``
configured — gpu-reservations:T010 / ADR-0017 §7), two non-streaming
purpose-model surfaces routed by MODEL NAME, never through chat routing:

* ``POST /v1/embeddings``        -> OpenAI Embeddings (relayed to the named serve)
* ``POST /v1/rerank``            -> Jina/Cohere-style rerank (relayed likewise)

Each request is parsed into a single :class:`~anvil_serving.router.internal.InternalRequest`
and passed through to ONE injectable :class:`~anvil_serving.router.internal.Backend`.

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
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Iterable, Optional, Sequence

from .audio import (
    AudioGateway,
    AudioGatewayError,
    SPEECH_PATH,
    TRANSCRIPTIONS_PATH,
    audio_purpose_for_path,
)
from .config import PURPOSE_EMBEDDING, PURPOSE_RERANK
from .decision_log import safe_correlation, summarize_decisions
from .dialects import Dialect
from .dialects.anthropic import AnthropicDialect
from .dialects.embeddings import (
    EMBEDDINGS_PATH,
    RERANK_PATH,
    parse_embeddings_request,
    parse_rerank_request,
)
from .dialects.openai import OpenAIDialect
from .dialects.responses import ResponsesDialect
from .discovery import models_payload
from .internal import (
    Backend,
    BackendClientError,
    DialectError,
    NoAvailableTierError,
)
from .purpose import PurposeError, PurposeRouter
from .gateway import ARTIFACT_PREFIX, MCP_PATH, ProtocolGateway
from ..control_plane.authorization import (
    AuthorizationPolicy,
    WORKLOADS_READ,
    check_scope,
)
from ..a2a.http import version_not_supported
from ..a2a.protocol import (
    A2A_LEGACY_DEFAULT_VERSION,
    A2A_PATH,
    A2A_VERSION,
    A2A_VERSION_HEADER,
    AGENT_CARD_PATH,
)
from ..media.errors import MediaError
from ..observability.workloads import WorkloadOutcome

# Path -> dialect. Stateless, so module-level singletons are fine.
_OPENAI_DIALECT = OpenAIDialect()
_ROUTES = {
    "/v1/chat/completions": _OPENAI_DIALECT,
    "/v1/responses": ResponsesDialect(),
    "/v1/messages": AnthropicDialect(),
}
DECISION_SUMMARY_ENDPOINT = "/v1/decisions"
#: Per-tier / per-serve live health snapshot (#292). Bearer-authed like every
#: route except GET /healthz; additive — /health and /v1/decisions are unchanged.
TIER_HEALTH_ENDPOINT = "/v1/health/tiers"
MODEL_CAPACITY_ENDPOINT = "/v1/models/capacity"
MODEL_CAPABILITIES_ENDPOINT = "/v1/models/capabilities"
MODEL_FINGERPRINTS_ENDPOINT = "/v1/models/fingerprints"
ROUTER_STATUS_ENDPOINT = "/v1/router/status"
ROUTER_STATS_ENDPOINT = "/v1/stats"
PROMETHEUS_ENDPOINT = "/metrics"
REQUEST_TRACE_PREFIX = "/v1/requests/"
REQUEST_TRACE_ROUTE = "/v1/requests/{request_id}"
TRANSITION_ENDPOINT = "/v1/admin/transition"
# Purpose-model surfaces (gpu-reservations:T010 / ADR-0017 §7). POST-only,
# routed by MODEL NAME via an injected PurposeRouter — active only when the
# server is built with one (purpose_models configured); otherwise both paths
# stay 404 exactly as before. Both speak the OpenAI error envelope.
_PURPOSE_PATHS = {
    EMBEDDINGS_PATH: PURPOSE_EMBEDDING,
    RERANK_PATH: PURPOSE_RERANK,
}
# Request/response voice gateway paths. They are active only when an
# AudioGateway is injected from configured ``[[router.audio_routes]]``.
_AUDIO_PATHS = (TRANSCRIPTIONS_PATH, SPEECH_PATH)


@dataclass(frozen=True)
class OperatorRoute:
    """An injected, scoped, read-only operator route."""

    method: str
    path: str
    scope: str
    callback: Callable[[str], bytes]


_MAX_OPERATOR_ROUTES = 8
_MAX_OPERATOR_PATH_BYTES = 256
_MAX_OPERATOR_QUERY_BYTES = 8192
_MAX_OPERATOR_RESPONSE_BYTES = 8 * 1024 * 1024
_OPERATOR_PATH_RE = re.compile(r"/[A-Za-z0-9][A-Za-z0-9._~-]*(?:/[A-Za-z0-9][A-Za-z0-9._~-]*)*")


def _validated_operator_routes(
    routes: Sequence[OperatorRoute] | None,
) -> tuple[OperatorRoute, ...]:
    """Return a copied, collision-free, bounded operator route registry."""
    if routes is None:
        return ()
    if isinstance(routes, (str, bytes)) or not isinstance(routes, Sequence):
        raise ValueError("operator routes must be a bounded sequence")
    try:
        declared_length = len(routes)
    except Exception:
        raise ValueError("operator routes must be a bounded sequence") from None
    if declared_length > _MAX_OPERATOR_ROUTES:
        raise ValueError("too many operator routes")

    protected_paths = set(_ROUTES) | {
        _HEALTHZ_PATH,
        "/health",
        "/v1/models",
        DECISION_SUMMARY_ENDPOINT,
        TIER_HEALTH_ENDPOINT,
        MODEL_CAPACITY_ENDPOINT,
        MODEL_CAPABILITIES_ENDPOINT,
        MODEL_FINGERPRINTS_ENDPOINT,
        ROUTER_STATUS_ENDPOINT,
        ROUTER_STATS_ENDPOINT,
        PROMETHEUS_ENDPOINT,
        TRANSITION_ENDPOINT,
        MCP_PATH,
        A2A_PATH,
        AGENT_CARD_PATH,
    } | set(_PURPOSE_PATHS) | set(_AUDIO_PATHS)
    copied: list[OperatorRoute] = []
    seen: set[tuple[str, str]] = set()
    for index in range(_MAX_OPERATOR_ROUTES + 1):
        try:
            route = routes[index]
        except IndexError:
            break
        except Exception:
            raise ValueError("operator routes must be a bounded sequence") from None
        if len(copied) >= _MAX_OPERATOR_ROUTES:
            raise ValueError("too many operator routes")
        if type(route) is not OperatorRoute:
            raise ValueError("operator route is invalid")
        method, path, scope, callback = (
            route.method,
            route.path,
            route.scope,
            route.callback,
        )
        if (
            type(method) is not str
            or method not in {"GET", "POST"}
            or type(scope) is not str
            or scope != WORKLOADS_READ
            or type(path) is not str
            or not path.isascii()
            or len(path) > _MAX_OPERATOR_PATH_BYTES
            or not _OPERATOR_PATH_RE.fullmatch(path)
            or path in protected_paths
            or path in {ARTIFACT_PREFIX.rstrip("/"), REQUEST_TRACE_PREFIX.rstrip("/")}
            or path.startswith(
                (ARTIFACT_PREFIX, REQUEST_TRACE_PREFIX, "/.well-known/", "/v1/audio/")
            )
            or not callable(callback)
        ):
            raise ValueError("operator route is invalid")
        key = (method, path)
        if key in seen:
            raise ValueError("operator route is invalid")
        seen.add(key)
        copied.append(OperatorRoute(method, path, scope, callback))
    try:
        stable_length = len(routes)
    except Exception:
        raise ValueError("operator routes must be a bounded sequence") from None
    if stable_length != declared_length or stable_length != len(copied):
        raise ValueError("operator routes must be a bounded sequence")
    return tuple(copied)

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

# Operation protocols have their own pool so long-lived A2A observation cannot
# consume direct-inference relay slots.  Jobs themselves remain durable and do
# not run in these request threads.
_PROTOCOL_CONCURRENCY_LIMIT: threading.BoundedSemaphore = threading.BoundedSemaphore(16)
_PROTOCOL_MAX_BODY_BYTES = 64 * 1024

# Drain waits must not consume a data-plane request slot.  This small separate
# pool bounds administrative waits for the single-operator deployment.
_MANAGEMENT_LIMIT: threading.BoundedSemaphore = threading.BoundedSemaphore(4)
# Mutations are serialized independently of status reads.  In particular, a
# readmit cannot race a long drain and invalidate its zero-active barrier.
_MANAGEMENT_MUTATION_LIMIT: threading.BoundedSemaphore = threading.BoundedSemaphore(1)
# Injected operator callbacks are read-only management work, not data-plane
# relay work; retain a separate bounded capacity before invoking one.
_OPERATOR_READ_LIMIT: threading.BoundedSemaphore = threading.BoundedSemaphore(4)

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


def _extract_operator_token(headers) -> Optional[str]:
    """Return one unambiguous scoped credential, or fail closed."""
    try:
        authorization = headers.get_all("Authorization") or []
        api_keys = headers.get_all("x-api-key") or []
    except Exception:
        return None
    if len(authorization) + len(api_keys) != 1:
        return None
    if authorization:
        header = authorization[0]
        if type(header) is not str:
            return None
        scheme, separator, value = header.partition(" ")
        if separator and scheme.strip().lower() == "bearer" and value.strip():
            return value.strip()
        return None
    header = api_keys[0]
    if type(header) is str and header.strip():
        return header.strip()
    return None


def _correlation_from_headers(headers) -> dict:
    """Read compact, opaque Workbench lineage without forwarding it upstream."""
    values = {
        "workbench_run_id": headers.get("X-Anvil-Workbench-Run-Id"),
        "task_id": headers.get("X-Anvil-Task-Id"),
        "request_id": headers.get("X-Request-Id") or headers.get("Request-Id"),
    }
    return {key: value for key, raw in values.items() if (value := safe_correlation(raw)) is not None}


def _new_request_correlation(headers) -> dict[str, str]:
    """Create trusted gateway lineage while retaining a safe caller id."""
    correlation = _correlation_from_headers(headers)
    gateway_request_id = f"req_{uuid.uuid4().hex}"
    correlation["gateway_request_id"] = gateway_request_id
    correlation.setdefault("request_id", gateway_request_id)
    return correlation


def _output_clamp_headers(request) -> dict[str, str]:
    """Return bounded client-visible metadata for an applied tier clamp."""
    raw = getattr(request, "raw", {})
    marker = raw.get("_anvil_output_clamp") if isinstance(raw, dict) else None
    if not isinstance(marker, dict):
        return {}
    requested = marker.get("requested")
    applied = marker.get("applied")
    if (
        isinstance(requested, bool)
        or not isinstance(requested, int)
        or isinstance(applied, bool)
        or not isinstance(applied, int)
        or requested <= applied
        or applied <= 0
    ):
        return {}
    return {
        "Warning": (
            '299 anvil-serving "max_tokens clamped from '
            f'{requested} to {applied}"'
        ),
        "X-Anvil-Warning": "max_tokens_clamped",
        "X-Anvil-Max-Tokens-Requested": str(requested),
        "X-Anvil-Max-Tokens-Applied": str(applied),
    }


def _make_handler(backend: Backend, timeout: Optional[float],
                  model_routes: Iterable[str], exhaustion_status: int = 503,
                  auth_token: Optional[str] = None,
                  purpose: Optional[PurposeRouter] = None,
                  audio: Optional[AudioGateway] = None,
                  gateway: Optional[ProtocolGateway] = None,
                  authorization_policy: Optional[AuthorizationPolicy] = None,
                  operator_routes: Sequence[OperatorRoute] | None = None):
    operator_route_map = {
        (route.method, route.path): route
        for route in _validated_operator_routes(operator_routes)
    }
    class FrontDoorHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        # Generic server token: no software name or version disclosed.
        server_version = "anvil"
        sys_version = ""
        # Finite idle timeout: with HTTP/1.1 keep-alive on a ThreadingHTTPServer,
        # an abandoned connection would otherwise pin a daemon thread blocked in
        # readline() forever (thread/FD leak). A timed-out read makes
        # handle_one_request set close_connection and the thread exits.
        # (Set to the configured value just below the class.)

        # --- helpers ---------------------------------------------------------
        def _reset_request_correlation(self) -> None:
            """Clear per-request state on a reused HTTP/1.1 handler."""
            self._anvil_correlation = None
            self._anvil_workload_stream = None
            self._anvil_delivery_outcome = None

        def _generate_deltas(self, request):
            """Retain delivery ownership before eager routing can fail."""
            tracked = getattr(backend, "generate_tracked", None)
            if not callable(tracked):
                return backend.generate(request)
            stream = tracked(
                request,
                gateway_request_id=(self._anvil_correlation or {}).get("gateway_request_id"),
            )
            self._anvil_workload_stream = stream
            return stream.start()

        def _workload_render_error(self) -> None:
            stream = self._anvil_workload_stream
            if stream is not None and not stream.generation_failed:
                self._anvil_delivery_outcome = WorkloadOutcome.ERROR

        def _start_request_correlation(self) -> None:
            """Stamp one authenticated inference request with trusted lineage."""
            self._anvil_correlation = _new_request_correlation(self.headers)

        def _correlation_headers(self) -> dict[str, str]:
            correlation = getattr(self, "_anvil_correlation", None)
            if not isinstance(correlation, dict):
                return {}
            gateway_request_id = correlation.get("gateway_request_id")
            request_id = correlation.get("request_id") or gateway_request_id
            if not gateway_request_id or not request_id:
                return {}
            return {
                "X-Anvil-Request-Id": gateway_request_id,
                "X-Request-Id": request_id,
            }

        def _log_inference_failure(
            self, status: int, scope: str, error: BaseException
        ) -> None:
            """Log bounded diagnostics without caller or upstream content."""
            gateway_request_id = self._correlation_headers().get(
                "X-Anvil-Request-Id", "-"
            )
            print(
                f"[anvil] {status} {scope}: {type(error).__name__} "
                f"gateway_request_id={gateway_request_id}",
                file=sys.stderr,
                flush=True,
            )

        def _json(self, status: int, obj, extra_headers=None) -> None:
            payload = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            # Advertise close whenever we're closing (request asked for it, or we
            # forced it on a framing error) so the client doesn't reuse the socket.
            if self.close_connection:
                self.send_header("Connection", "close")
            for _h_name, _h_val in self._correlation_headers().items():
                self.send_header(_h_name, _h_val)
            if extra_headers:
                for _h_name, _h_val in extra_headers.items():
                    self.send_header(_h_name, _h_val)
            self.end_headers()
            self.wfile.write(payload)

        def _text(
            self, status: int, payload: str, *, content_type: str,
            extra_headers=None,
        ) -> None:
            encoded = payload.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            if self.close_connection:
                self.send_header("Connection", "close")
            for _h_name, _h_val in self._correlation_headers().items():
                self.send_header(_h_name, _h_val)
            if extra_headers:
                for _h_name, _h_val in extra_headers.items():
                    self.send_header(_h_name, _h_val)
            self.end_headers()
            self.wfile.write(encoded)

        def _authenticated(self) -> bool:
            """True if this request carries a valid token, or auth is off.

            ``auth_token`` is resolved ONCE at server start (threaded in from
            ``serve.py``) — never re-read from ``os.environ`` per request.
            Comparison is constant-time (``hmac.compare_digest``) so response
            timing can't be used to guess the token byte-by-byte. The token
            itself is never logged: on failure only a generic message is sent.
            """
            if auth_token is None:
                return True  # [server].auth_env unset -> auth OFF
            supplied = _extract_bearer_token(self.headers)
            if supplied is None:
                return False
            return hmac.compare_digest(
                supplied.encode("utf-8"), auth_token.encode("utf-8")
            )

        def _operator_route(self, method: str) -> Optional[OperatorRoute]:
            path, _, _query = self.path.partition("?")
            return operator_route_map.get((method, path))

        def _operator_error(self, status: int, etype: str, message: str) -> None:
            """Reply before consuming an operator body and retire the socket."""
            self.close_connection = True
            self._json(
                status,
                {"error": {"type": etype, "message": message}},
                extra_headers={"Cache-Control": "no-store"},
            )
            self._flush_closing_response()

        def _operator_framing_is_bodyless(self) -> bool:
            transfer_encoding = self.headers.get_all("Transfer-Encoding") or []
            content_lengths = self.headers.get_all("Content-Length") or []
            return not transfer_encoding and (
                not content_lengths
                or (len(content_lengths) == 1 and content_lengths[0] == "0")
            )

        def _handle_operator_route(self, route: OperatorRoute) -> None:
            """Run one already-identified scoped route without body handling."""
            presented = _extract_operator_token(self.headers)
            decision = check_scope(authorization_policy, presented, route.scope)
            if not decision.allowed:
                self._operator_error(
                    403, "authorization_scope_denied", "authorization scope denied"
                )
                return
            if not self._operator_framing_is_bodyless():
                self._operator_error(
                    400, "invalid_request", "operator route must not include a body"
                )
                return
            _path, _separator, query = self.path.partition("?")
            try:
                query_bytes = query.encode("ascii", "strict")
            except UnicodeEncodeError:
                self._operator_error(400, "invalid_request", "invalid query")
                return
            if len(query_bytes) > _MAX_OPERATOR_QUERY_BYTES:
                self._operator_error(400, "invalid_request", "invalid query")
                return
            if not _OPERATOR_READ_LIMIT.acquire(blocking=False):
                self._operator_error(503, "server_busy", "operator route busy")
                return
            try:
                try:
                    payload = route.callback(query)
                except Exception:  # noqa: BLE001 - callback details stay private
                    self._operator_error(500, "internal_error", "operator route failed")
                    return
                if type(payload) is not bytes or len(payload) > _MAX_OPERATOR_RESPONSE_BYTES:
                    self._operator_error(500, "internal_error", "operator route failed")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)
                self.wfile.flush()
            finally:
                _OPERATOR_READ_LIMIT.release()

        def _protocol_json_error(self, status: int, code: str, message: str) -> None:
            self._json(status, {"error": {"type": code, "message": message}})

        def _protocol_body(self) -> dict | None:
            """Read one strictly framed, bounded protocol JSON body."""
            te_all = self.headers.get_all("Transfer-Encoding") or []
            cl_all = self.headers.get_all("Content-Length") or []
            if te_all:
                self.close_connection = True
                self._protocol_json_error(411, "invalid_request", "send Content-Length")
                return None
            if len(cl_all) > 1:
                self.close_connection = True
                self._protocol_json_error(400, "invalid_request", "duplicate Content-Length")
                return None
            raw_length = cl_all[0] if cl_all else "0"
            if not _DIGIT_RE.fullmatch(raw_length):
                self.close_connection = True
                self._protocol_json_error(400, "invalid_request", "invalid Content-Length")
                return None
            length = int(raw_length)
            if length > _PROTOCOL_MAX_BODY_BYTES:
                self.close_connection = True
                self._protocol_json_error(413, "payload_too_large", "protocol request too large")
                self._flush_closing_response()
                return None
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw or b"{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._protocol_json_error(400, "invalid_request", "body must be valid JSON")
                return None
            if not isinstance(body, dict):
                self._protocol_json_error(400, "invalid_request", "body must be a JSON object")
                return None
            return body

        def _reject_protocol_auth(self) -> None:
            """Return a uniform 401 while keeping a safe request stream aligned."""
            te_all = self.headers.get_all("Transfer-Encoding") or []
            cl_all = self.headers.get_all("Content-Length") or []
            drainable = not te_all and len(cl_all) <= 1
            length = 0
            if drainable and cl_all:
                if _DIGIT_RE.fullmatch(cl_all[0]):
                    length = int(cl_all[0])
                else:
                    drainable = False
            if drainable and 0 < length <= _PROTOCOL_MAX_BODY_BYTES:
                try:
                    self.rfile.read(length)
                except Exception:
                    self.close_connection = True
            elif not drainable or length > _PROTOCOL_MAX_BODY_BYTES:
                self.close_connection = True
            self._protocol_json_error(
                401, "authentication_error", "invalid or missing API key"
            )
            if self.close_connection:
                self._flush_closing_response()

        def _write_protocol_sse(self, frames: Iterable[bytes]) -> None:
            chunked = self.request_version == "HTTP/1.1"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            if chunked:
                self.send_header("Transfer-Encoding", "chunked")
            else:
                self.close_connection = True
                self.send_header("Connection", "close")
            self.end_headers()
            try:
                for frame in frames:
                    if chunked:
                        self.wfile.write(b"%x\r\n" % len(frame) + frame + b"\r\n")
                    else:
                        self.wfile.write(frame)
                    self.wfile.flush()
                if chunked:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionError):
                self.close_connection = True
            finally:
                close = getattr(frames, "close", None)
                if callable(close):
                    close()

        def _handle_protocol_post(self, route: str) -> None:
            if not self._authenticated():
                self._reject_protocol_auth()
                return
            body = self._protocol_body()
            if body is None:
                return
            if route == MCP_PATH:
                result = gateway.mcp_request(body)
                if result is None:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self._json(200, result, extra_headers={"Cache-Control": "no-store"})
                return
            requested_versions = self.headers.get_all(A2A_VERSION_HEADER) or []
            if len(requested_versions) == 1:
                requested_version = requested_versions[0].strip()
            elif not requested_versions:
                requested_version = ""
            else:
                requested_version = ",".join(requested_versions)
            negotiated_version = requested_version or A2A_LEGACY_DEFAULT_VERSION
            if negotiated_version != A2A_VERSION:
                self._json(
                    200,
                    version_not_supported(body.get("id"), negotiated_version),
                    extra_headers={"Cache-Control": "no-store"},
                )
                return
            if body.get("method") in {"SendStreamingMessage", "SubscribeToTask"}:
                if "text/event-stream" not in self.headers.get("Accept", ""):
                    self._json(200, gateway.a2a_request(body))
                    return
                stream = gateway.a2a_stream(body)
                if isinstance(stream, dict):
                    self._json(200, stream)
                else:
                    self._write_protocol_sse(stream)
                return
            self._json(200, gateway.a2a_request(body))

        def _handle_protocol_get(self, route: str) -> None:
            if self.headers.get_all("Transfer-Encoding") or self.headers.get_all("Content-Length"):
                self._reject_get_body_framing("protocol GET must not include a body")
                return
            if not self._authenticated():
                self._protocol_json_error(401, "authentication_error", "invalid or missing API key")
                return
            if route == AGENT_CARD_PATH:
                self._json(200, gateway.agent_card(), extra_headers={"Cache-Control": "no-store"})
                return
            if route in {MCP_PATH, A2A_PATH}:
                self._json(
                    405,
                    {"error": {"type": "method_not_allowed", "message": "this route only accepts POST requests"}},
                    extra_headers={"Allow": "POST"},
                )
                return
            encoded_id = route[len(ARTIFACT_PREFIX):]
            artifact_id = urllib.parse.unquote(encoded_id)
            if not artifact_id or "/" in artifact_id or "?" in artifact_id:
                self._protocol_json_error(404, "artifact_not_found", "artifact was not found")
                return
            range_header = self.headers.get("Range")
            try:
                start, end = _parse_artifact_range(range_header)
                payload = gateway.artifact(artifact_id, start=start, end=end)
            except MediaError as exc:
                self._protocol_json_error(exc.status, exc.code, exc.message)
                return
            status = 206 if range_header is not None else 200
            self.send_response(status)
            self.send_header("Content-Type", payload.artifact.media_type)
            self.send_header("Content-Length", str(len(payload.data)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "private, no-store")
            if status == 206:
                self.send_header("Content-Range", f"bytes {payload.start}-{payload.end}/{payload.total}")
            self.end_headers()
            self.wfile.write(payload.data)

        def _no_tier_response(self, e: NoAvailableTierError,
                              dialect: Optional[Dialect] = None) -> None:
            """Render a :class:`NoAvailableTierError` to the right HTTP status.

            An ``over_context`` error (the request exceeds its configured tier's
            context window) is a CALLER problem -> a clean **413 Payload Too
            Large**, refusing the over-sized request up front instead of
            forwarding it to a too-small tier that would 400 at the model. Every
            other dispatch failure is a server availability
            signal -> the operator-configured ``exhaustion_status`` (default 503,
            the keyless-handoff signal per ADR-0001 §Mechanism). The detailed
            message is logged server-side; the client gets a sanitised generic
            message (tier identities / remediation are internal-operator info).
            """
            if getattr(e, "kind", None) == "unknown_model":
                self._error(
                    404, "model_not_found", "unknown configured model",
                    dialect=dialect,
                )
                return
            if getattr(e, "kind", None) == "over_context":
                self._log_inference_failure(413, "over-context request", e)
                self._error(
                    413, "payload_too_large",
                    "request exceeds the context window of every available "
                    "tier; send a smaller request",
                    dialect=dialect,
                )
                return
            if getattr(e, "kind", None) == "media_limit":
                self._log_inference_failure(413, "over-media-limit request", e)
                self._error(
                    413,
                    "payload_too_large",
                    "request exceeds the configured media limits",
                    dialect=dialect,
                )
                return
            self._log_inference_failure(
                exhaustion_status, "no available tier", e
            )
            self._error(
                exhaustion_status, "service_unavailable",
                "the configured model service is unavailable",
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

        def _backend_client_error(
            self, error: BackendClientError, dialect: Dialect
        ) -> None:
            """Return a backend-declared, sanitized caller error."""
            self._log_inference_failure(
                error.status, "backend rejected request", error
            )
            self._error(
                error.status,
                error.etype,
                error.message,
                dialect=dialect,
            )

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

        def _flush_closing_response(self) -> None:
            """Let a close-after-response error reach the peer before TCP RST.

            A bounded, non-blocking receive after flushing is deliberately not
            a body drain: the declared body may be arbitrarily large.  It just
            absorbs bytes already queued by the peer so Windows does not
            truncate the error response when ``close_connection`` tears down an
            unread request body.
            """
            try:
                self.wfile.flush()
                prior_timeout = self.connection.gettimeout()
                try:
                    self.connection.settimeout(0.0)
                    self.connection.recv(_CLOSE_DRAIN_CAP)
                except OSError:
                    pass
                finally:
                    self.connection.settimeout(prior_timeout)
            except Exception:
                pass

        def _reject_get_body_framing(self, message: str) -> None:
            """Reject a framed GET without resetting its error response.

            A close with unread request bytes can produce TCP RST on Windows,
            truncating the response that explains the rejection.  Drain only
            a single, strictly framed body within the bounded close-drain cap;
            for every ambiguous or larger body, flush the response and absorb
            only bytes already queued by the peer before closing.
            """
            transfer_encoding = self.headers.get_all("Transfer-Encoding") or []
            content_lengths = self.headers.get_all("Content-Length") or []
            length = 0
            safely_framed = not transfer_encoding and len(content_lengths) == 1
            if safely_framed and _DIGIT_RE.fullmatch(content_lengths[0]):
                length = int(content_lengths[0])
            else:
                safely_framed = False
            unread = not safely_framed or length > _CLOSE_DRAIN_CAP
            if safely_framed and 0 < length <= _CLOSE_DRAIN_CAP:
                try:
                    unread = len(self.rfile.read(length)) != length
                except Exception:
                    unread = True
            self.close_connection = True
            self._error(400, "invalid_request", message)
            if unread:
                self._flush_closing_response()

        def _write_sse(self, dialect: Dialect, request) -> None:
            """Stream the backend's deltas as native SSE, flushed per event.

            HTTP/1.1: chunked transfer-encoding. HTTP/1.0 (no chunked support):
            close-delimited — raw frames, then close the socket (mirrors
            ``multiplexer.relay``).
            """
            chunked = self.request_version == "HTTP/1.1"
            requested_model = request.model

            # Resolve the direct backend's delta stream BEFORE committing a 200
            # so an unavailable tier can still produce a real HTTP error instead
            # of a 200 with an empty or truncated body.
            #
            # Any other exception from generate() is also surfaced as a clean 500
            # here, before the 200 is committed, so the client always sees a real
            # HTTP error status for pre-stream failures.
            try:
                deltas = self._generate_deltas(request)
            except NoAvailableTierError as e:
                # The configured exhaustion status lets the caller apply its own
                # transport retry policy.
                self._no_tier_response(e, dialect=dialect)
                return
            except BackendClientError as e:
                self._backend_client_error(e, dialect)
                return
            except Exception as e:
                self._log_inference_failure(500, "backend generate error", e)
                self._error(500, "internal_error", "internal error", dialect=dialect)
                return

            frames = None
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                for _h_name, _h_val in self._correlation_headers().items():
                    self.send_header(_h_name, _h_val)
                for _h_name, _h_val in _output_clamp_headers(request).items():
                    self.send_header(_h_name, _h_val)
                if chunked:
                    # Respect the request's Connection intent; BaseHTTPRequestHandler
                    # already populated close_connection from the request headers.
                    self.send_header(
                        "Connection",
                        "close" if self.close_connection else "keep-alive",
                    )
                    self.send_header("Transfer-Encoding", "chunked")
                else:
                    self.close_connection = True
                    self.send_header("Connection", "close")
                self.end_headers()

                # Build the dialect iterator only after headers, but keep it under
                # the same cleanup boundary as the eagerly acquired backend stream.
                _get_structured_fn = getattr(backend, "get_last_structured", None)
                frames = dialect.stream(
                    request,
                    deltas,
                    get_structured=(
                        _get_structured_fn if callable(_get_structured_fn) else None
                    ),
                    response_model=requested_model,
                )
                def _write_frame(frame: bytes) -> None:
                    if chunked:
                        # One write per event (wfile is unbuffered): size + frame + CRLF.
                        self.wfile.write(b"%x\r\n" % len(frame) + frame + b"\r\n")
                    else:
                        self.wfile.write(frame)
                    self.wfile.flush()  # push each SSE event to the client immediately

                try:
                    for frame in frames:
                        if not frame:
                            continue  # never emit a zero-length chunk (ends the stream)
                        _write_frame(frame)
                except (BrokenPipeError, ConnectionError):
                    raise  # client is gone; nothing left to signal
                except Exception as exc:
                    # ADR-0033 mid-stream honesty: a backend failure after the
                    # 200 was committed must not read as a complete response.
                    # Emit one generic terminal error frame (never upstream
                    # exception text), always close the chunked body, and drop
                    # the connection so length-blind clients also see the end.
                    self._log_inference_failure(
                        500, "stream error after headers", exc
                    )
                    self._workload_render_error()
                    error_frame_fn = getattr(dialect, "stream_error", None)
                    try:
                        if callable(error_frame_fn):
                            _write_frame(error_frame_fn())
                        if chunked:
                            self.wfile.write(b"0\r\n\r\n")
                            self.wfile.flush()
                    except OSError:
                        self._anvil_delivery_outcome = WorkloadOutcome.DISCONNECTED
                        pass  # client disconnected while we signalled failure
                    self.close_connection = True
                    return
                if chunked:
                    self.wfile.write(b"0\r\n\r\n")  # chunked terminator
                    self.wfile.flush()
            finally:
                # Deterministically close the generator chain on disconnect/error
                # so backends release resources (real backends hold upstream
                # sockets); generator .close() is idempotent.
                for gen in (frames, deltas):
                    closer = getattr(gen, "close_upstream", None) or getattr(gen, "close", None)
                    if closer is not None:
                        try:
                            closer()
                        except Exception:
                            pass

        # --- purpose-model surfaces (gpu-reservations:T010) -----------------
        def _handle_purpose(self, kind: str, body: dict) -> None:
            """Handle ``POST /v1/embeddings`` / ``POST /v1/rerank``.

            Validates the wire shape (``dialects.embeddings``), then routes BY
            MODEL NAME through the injected :class:`PurposeRouter` — never
            through the chat alias path. An unknown model name is
            a clean 404 naming the configured models (T010 acceptance
            criterion: no fallthrough to chat routing). Responses and errors
            speak the OpenAI envelope, the native shape for both surfaces.
            """
            try:
                if kind == PURPOSE_EMBEDDING:
                    parse_embeddings_request(body)
                else:
                    parse_rerank_request(body)
                payload = purpose.dispatch(
                    kind,
                    body,
                    correlation=dict(
                        getattr(self, "_anvil_correlation", None) or {}
                    ),
                )
            except DialectError as e:
                self._error(e.status, e.etype, e.message,
                            dialect=_OPENAI_DIALECT)
                return
            except PurposeError as e:
                self._error(e.status, e.etype, e.message,
                            dialect=_OPENAI_DIALECT)
                return
            except Exception as e:  # unexpected fault: bounded metadata only
                self._log_inference_failure(500, f"{kind} error", e)
                self._error(500, "internal_error", "internal error",
                            dialect=_OPENAI_DIALECT)
                return
            self._json(200, payload)

        # --- normalized one-shot audio gateway ----------------------------
        def _handle_audio(self, kind: str, body: dict) -> None:
            """Handle an authenticated JSON ``/v1/audio/*`` request.

            The dedicated audio seam keeps raw Dark STT/TTS protocol quirks
            out of callers and out of the generic chat/purpose pipelines.
            AudioGateway errors are sanitized: no raw audio, transcript,
            synthesis text, or upstream host reaches callers or router logs.
            """
            correlation = dict(getattr(self, "_anvil_correlation", None) or {})
            try:
                if kind == "stt":
                    payload = audio.dispatch_transcription(body, correlation=correlation)
                else:
                    payload = audio.dispatch_speech(body, correlation=correlation)
            except AudioGatewayError as e:
                self._error(e.status, e.etype, e.message, dialect=_OPENAI_DIALECT)
                return
            except Exception as e:  # noqa: BLE001 - never expose content/upstream details
                print(
                    f"[anvil] 500 audio gateway {kind} error: "
                    f"{type(e).__name__} gateway_request_id="
                    f"{correlation.get('gateway_request_id', '-')}",
                    file=sys.stderr, flush=True,
                )
                self._error(500, "internal_error", "internal audio gateway error",
                            dialect=_OPENAI_DIALECT)
                return
            self._json(200, payload)

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
            if not _MANAGEMENT_MUTATION_LIMIT.acquire(blocking=False):
                self._error(503, "server_busy", "transition mutation busy")
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
            finally:
                _MANAGEMENT_MUTATION_LIMIT.release()
            self._json(200, {"applied": True, "action": action, "result": result})

        # --- routes ----------------------------------------------------------
        def do_GET(self) -> None:
            self._reset_request_correlation()
            route = self.path.split("?", 1)[0].rstrip("/")
            operator_route = self._operator_route("GET")
            if operator_route is not None:
                self._handle_operator_route(operator_route)
                return
            if gateway is not None and (
                route in {AGENT_CARD_PATH, MCP_PATH, A2A_PATH}
                or route.startswith(ARTIFACT_PREFIX)
            ):
                if not _PROTOCOL_CONCURRENCY_LIMIT.acquire(blocking=False):
                    self.close_connection = True
                    self._protocol_json_error(503, "server_busy", "protocol gateway busy")
                    return
                try:
                    self._handle_protocol_get(route)
                finally:
                    _PROTOCOL_CONCURRENCY_LIMIT.release()
                return
            if route == TRANSITION_ENDPOINT:
                if not _MANAGEMENT_LIMIT.acquire(blocking=False):
                    self._error(503, "server_busy", "management busy; try again later")
                    return
                try:
                    has_body_framing = bool(
                        self.headers.get_all("Transfer-Encoding")
                        or self.headers.get_all("Content-Length")
                    )
                    if has_body_framing:
                        self._reject_get_body_framing(
                            "management GET must not include a body"
                        )
                    elif auth_token is None:
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
                        "routes": sorted(
                            list(_ROUTES)
                            + [DECISION_SUMMARY_ENDPOINT,
                               MODEL_CAPABILITIES_ENDPOINT,
                               MODEL_CAPACITY_ENDPOINT,
                               MODEL_FINGERPRINTS_ENDPOINT,
                               PROMETHEUS_ENDPOINT,
                               REQUEST_TRACE_ROUTE,
                               ROUTER_STATS_ENDPOINT,
                               ROUTER_STATUS_ENDPOINT,
                               TIER_HEALTH_ENDPOINT]
                            + (list(_PURPOSE_PATHS) if purpose is not None else [])
                            + (list(audio.paths) if audio is not None else [])
                        ),
                    })
                elif route == TIER_HEALTH_ENDPOINT:
                    # Live per-tier/per-serve readiness snapshot (#292): surface
                    # the backend's already-tracked availability for EVERY
                    # configured serve. A backend without a routing snapshot
                    # (plain echo/static) has no configured serves -> {"tiers": []}.
                    health_fn = getattr(backend, "tier_health", None)
                    if health_fn is None:
                        routing = getattr(self.server, "anvil_routing", None)
                        health_fn = getattr(routing, "tier_health", None)
                    self._json(200, health_fn() if callable(health_fn) else {"tiers": []})
                elif route == MODEL_CAPACITY_ENDPOINT:
                    capacity_fn = getattr(backend, "model_capacity", None)
                    if capacity_fn is None:
                        routing = getattr(self.server, "anvil_routing", None)
                        capacity_fn = getattr(routing, "model_capacity", None)
                    query = urllib.parse.parse_qs(
                        urllib.parse.urlparse(self.path).query,
                        keep_blank_values=True,
                    )
                    if not callable(capacity_fn):
                        self._json(200, {"object": "list", "data": []})
                        return
                    try:
                        capacity = capacity_fn(query)
                    except KeyError:
                        self._error(
                            404, "model_not_found", "unknown configured model"
                        )
                        return
                    except ValueError as exc:
                        self._error(400, "invalid_request", str(exc))
                        return
                    self._json(
                        200,
                        capacity,
                        extra_headers={"Cache-Control": "no-store"},
                    )
                elif route in (
                    MODEL_CAPABILITIES_ENDPOINT,
                    MODEL_FINGERPRINTS_ENDPOINT,
                ):
                    method_name = (
                        "model_capabilities"
                        if route == MODEL_CAPABILITIES_ENDPOINT
                        else "model_fingerprints"
                    )
                    metadata_fn = getattr(backend, method_name, None)
                    if metadata_fn is None:
                        routing = getattr(self.server, "anvil_routing", None)
                        metadata_fn = getattr(routing, method_name, None)
                    query = urllib.parse.parse_qs(
                        urllib.parse.urlparse(self.path).query,
                        keep_blank_values=True,
                    )
                    if not callable(metadata_fn):
                        self._json(200, {"object": "list", "data": []})
                        return
                    try:
                        metadata = metadata_fn(query)
                    except KeyError:
                        self._error(
                            404, "model_not_found", "unknown configured model"
                        )
                        return
                    except ValueError as exc:
                        self._error(400, "invalid_request", str(exc))
                        return
                    self._json(
                        200,
                        metadata,
                        extra_headers={"Cache-Control": "no-store"},
                    )
                elif route == ROUTER_STATUS_ENDPOINT:
                    if urllib.parse.urlparse(self.path).query:
                        self._error(
                            400,
                            "invalid_request",
                            "router status does not accept query parameters",
                        )
                        return
                    status_fn = getattr(backend, "router_status", None)
                    if status_fn is None:
                        routing = getattr(self.server, "anvil_routing", None)
                        status_fn = getattr(routing, "router_status", None)
                    if not callable(status_fn):
                        self._error(404, "not_found", f"no route {route}")
                        return
                    self._json(
                        200,
                        status_fn(),
                        extra_headers={"Cache-Control": "no-store"},
                    )
                elif route == ROUTER_STATS_ENDPOINT:
                    stats_fn = getattr(backend, "router_stats", None)
                    if stats_fn is None:
                        routing = getattr(self.server, "anvil_routing", None)
                        stats_fn = getattr(routing, "router_stats", None)
                    query = urllib.parse.parse_qs(
                        urllib.parse.urlparse(self.path).query,
                        keep_blank_values=True,
                    )
                    if not callable(stats_fn):
                        self._error(404, "not_found", f"no route {route}")
                        return
                    try:
                        stats = stats_fn(query)
                    except KeyError:
                        self._error(
                            404, "model_not_found", "unknown configured model"
                        )
                        return
                    except ValueError as exc:
                        self._error(400, "invalid_request", str(exc))
                        return
                    self._json(
                        200,
                        stats,
                        extra_headers={"Cache-Control": "no-store"},
                    )
                elif route.startswith(REQUEST_TRACE_PREFIX):
                    request_id = urllib.parse.unquote(
                        route[len(REQUEST_TRACE_PREFIX):]
                    )
                    trace_fn = getattr(backend, "request_trace", None)
                    if trace_fn is None:
                        routing = getattr(self.server, "anvil_routing", None)
                        trace_fn = getattr(routing, "request_trace", None)
                    if (
                        not request_id
                        or "/" in request_id
                        or urllib.parse.urlparse(self.path).query
                        or not callable(trace_fn)
                    ):
                        self._error(404, "not_found", "request record not found")
                        return
                    try:
                        trace = trace_fn(request_id)
                    except KeyError:
                        self._error(404, "not_found", "request record not found")
                        return
                    self._json(
                        200,
                        trace,
                        extra_headers={"Cache-Control": "no-store"},
                    )
                elif route == PROMETHEUS_ENDPOINT:
                    metrics_fn = getattr(backend, "prometheus_metrics", None)
                    if metrics_fn is None:
                        routing = getattr(self.server, "anvil_routing", None)
                        metrics_fn = getattr(routing, "prometheus_metrics", None)
                    query = urllib.parse.parse_qs(
                        urllib.parse.urlparse(self.path).query,
                        keep_blank_values=True,
                    )
                    if not callable(metrics_fn):
                        self._error(404, "not_found", f"no route {route}")
                        return
                    try:
                        metrics = metrics_fn(query)
                    except KeyError:
                        self._error(
                            404, "model_not_found", "unknown configured model"
                        )
                        return
                    except ValueError as exc:
                        self._error(400, "invalid_request", str(exc))
                        return
                    self._text(
                        200,
                        metrics,
                        content_type=(
                            "text/plain; version=0.0.4; charset=utf-8"
                        ),
                        extra_headers={"Cache-Control": "no-store"},
                    )
                elif route == "/v1/models":
                    discovery_fn = getattr(backend, "model_discovery", None)
                    if discovery_fn is None:
                        routing = getattr(self.server, "anvil_routing", None)
                        discovery_fn = getattr(routing, "model_discovery", None)
                    self._json(
                        200,
                        discovery_fn()
                        if callable(discovery_fn)
                        else models_payload(model_routes),
                    )
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
                elif route in _ROUTES or (
                    purpose is not None and route in _PURPOSE_PATHS
                ) or (audio is not None and route in audio.paths):
                    # Known POST-only route requested with GET → 405 Method Not
                    # Allowed with Allow: POST (RFC 7231 §6.5.5).  Use the
                    # dialect's native error envelope when one is bound to the
                    # path (a purpose path speaks OpenAI).
                    _dial405: Optional[Dialect] = _ROUTES.get(route)
                    if _dial405 is None and route in _PURPOSE_PATHS:
                        _dial405 = _OPENAI_DIALECT
                    if _dial405 is None and audio is not None and route in audio.paths:
                        _dial405 = _OPENAI_DIALECT
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
            self._reset_request_correlation()
            route = self.path.split("?", 1)[0].rstrip("/")
            operator_route = self._operator_route("POST")
            if operator_route is not None:
                self._handle_operator_route(operator_route)
                return
            if gateway is not None and route in {MCP_PATH, A2A_PATH}:
                if not _PROTOCOL_CONCURRENCY_LIMIT.acquire(blocking=False):
                    self.close_connection = True
                    self._protocol_json_error(503, "server_busy", "protocol gateway busy")
                    return
                try:
                    self._handle_protocol_post(route)
                finally:
                    _PROTOCOL_CONCURRENCY_LIMIT.release()
                return
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
                if self._anvil_workload_stream is not None:
                    # Commit only after buffered bytes and the last SSE trailer
                    # have actually crossed the handler's final flush boundary.
                    self.wfile.flush()
            except (OSError, ConnectionError):
                self._anvil_delivery_outcome = WorkloadOutcome.DISCONNECTED
                raise
            except BaseException:
                # Preserve a typed generation cancellation/timeout proposal;
                # only response rendering failures introduce a new error.
                self._workload_render_error()
                raise
            finally:
                try:
                    stream = self._anvil_workload_stream
                    if stream is not None:
                        stream.finish_delivery(self._anvil_delivery_outcome)
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
            # Purpose-model surfaces (T010): active only when a PurposeRouter
            # was injected — otherwise these paths fall through to the 404
            # below, exactly the pre-T010 behaviour. Errors (including the
            # framing rejections shared below) speak the OpenAI envelope, the
            # native error shape for /v1/embeddings and /v1/rerank.
            purpose_kind: Optional[str] = (
                _PURPOSE_PATHS.get(path) if purpose is not None else None
            )
            audio_kind: Optional[str] = audio_purpose_for_path(path)
            if audio_kind is not None and (audio is None or not audio.has_purpose(audio_kind)):
                audio_kind = None

            dialect: Optional[Dialect] = _ROUTES.get(path)
            if dialect is None and purpose_kind is not None:
                dialect = _OPENAI_DIALECT
            if dialect is None and audio_kind is not None:
                dialect = _OPENAI_DIALECT
            if (
                dialect is None and not is_transition
            ):
                # Unknown route — drain body if well-framed to keep the
                # keep-alive socket in sync, then 404.
                self._fail_framing(404, "not_found", f"no route {path}",
                                   drainable, n)
                return

            if not is_transition:
                self._start_request_correlation()

            # Audio requests carry a base64 blob, so cap the *encoded* body
            # before rfile.read() or json.loads() materializes it.  The small
            # audio-only pool protects the upstream hop, but is intentionally
            # acquired only after a complete JSON request is parsed: a client
            # that drips an otherwise legal body must not pin scarce TTS/STT
            # capacity while it is still being read.
            if audio_kind is not None:
                if has_te:
                    self._fail_framing(
                        411, "invalid_request",
                        "chunked request bodies are unsupported; send Content-Length",
                        drainable=False, n=0, dialect=dialect,
                    )
                    return
                if dup_cl:
                    self._fail_framing(
                        400, "invalid_request", "duplicate Content-Length headers",
                        drainable=False, n=0, dialect=dialect,
                    )
                    return
                if cl_invalid:
                    self._fail_framing(
                        400, "invalid_request", f"invalid Content-Length: {cl_all[0]!r}",
                        drainable=False, n=0, dialect=dialect,
                    )
                    return
                if n > min(MAX_BODY_BYTES, audio.max_request_body_bytes):
                    self._fail_framing(
                        413, "payload_too_large", "audio request body too large",
                        drainable=False, n=0, dialect=dialect,
                    )
                    self._flush_closing_response()
                    return
                raw = self.rfile.read(n) if n else b""
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
                if not audio.acquire():
                    self._error(503, "server_busy", "audio gateway busy; try again later",
                                dialect=dialect)
                    return
                try:
                    self._handle_audio(audio_kind, body)
                finally:
                    audio.release()
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
                self._flush_closing_response()
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

            # /v1/embeddings + /v1/rerank (T010): routed by MODEL NAME through
            # the PurposeRouter — never parsed as a chat dialect and never
            # dispatched to backend.generate() (no fallthrough to chat).
            if purpose_kind is not None:
                self._handle_purpose(purpose_kind, body)
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

            # Always overwrite caller JSON at this reserved key. Only the trusted
            # front-door lineage may reach routing, audit, or the upstream relay.
            request.raw["_anvil_correlation"] = dict(
                getattr(self, "_anvil_correlation", None) or {}
            )

            if request.stream:
                self._write_sse(dialect, request)
            else:
                # Symmetric with the streaming close-on-error contract: a real
                # backend can raise here, so surface a clean error in the
                # dialect's native envelope rather than dropping the request with
                # a traceback.
                requested_model = request.model
                try:
                    text = "".join(self._generate_deltas(request))
                    # Read structured fields AFTER the generator is drained so the
                    # backend's thread-local is fully populated (#42 / #52).
                    # Falls through to dialect defaults (structured=None) when the
                    # backend doesn't expose get_last_structured (text-path safety).
                    _get_fn = getattr(backend, "get_last_structured", None)
                    _structured = _get_fn() if callable(_get_fn) else None
                    payload = dialect.render(
                        request,
                        text,
                        structured=_structured,
                        response_model=requested_model,
                    )
                except NoAvailableTierError as e:
                    # Keyless handoff contract — see the streaming path above for
                    # the full rationale (ADR-0001 §Mechanism, advise-and-defer:T004).
                    # over_context -> 413; unbound/exhausted -> exhaustion_status.
                    self._no_tier_response(e, dialect=dialect)
                    return
                except BackendClientError as e:
                    self._backend_client_error(e, dialect)
                    return
                except Exception as e:
                    # Unexpected backend fault: log bounded metadata only; send
                    # a generic message so internal state is not disclosed.
                    self._log_inference_failure(500, "backend error", e)
                    self._workload_render_error()
                    self._error(500, "internal_error", "internal error",
                                dialect=dialect)
                    return
                self._json(
                    200,
                    payload,
                    extra_headers=_output_clamp_headers(request),
                )

        def log_message(self, *args) -> None:  # keep the server quiet
            pass

    FrontDoorHandler.timeout = timeout
    return FrontDoorHandler


def make_server(host: str, port: int,
                backend: Backend,
                timeout: Optional[float] = 120,
                model_routes: Iterable[str] = (),
                exhaustion_status: int = 503,
                auth_token: Optional[str] = None,
                purpose: Optional[PurposeRouter] = None,
                audio: Optional[AudioGateway] = None,
                gateway: Optional[ProtocolGateway] = None,
                authorization_policy: Optional[AuthorizationPolicy] = None,
                operator_routes: Sequence[OperatorRoute] | None = None,
) -> ThreadingHTTPServer:
    """Build (but do not start) the front-door server.

    Pass ``port=0`` to bind an ephemeral port (read it back from
    ``server.server_address[1]``).
    ``timeout`` is the per-connection idle read timeout in seconds (finite by
    default so abandoned keep-alive sockets can't leak threads/FDs); pass
    ``None`` to disable. ``model_routes`` are the complete configured aliases
    ``GET /v1/models`` advertises. ``exhaustion_status`` is the HTTP
    status returned when the configured tier is unavailable (default 503 —
    the keyless handoff signal; see :class:`~anvil_serving.router.config.RouterConfig`
    and ADR-0001 §Mechanism). ``auth_token`` is the RESOLVED secret (already read
    from ``os.environ`` once by the caller, e.g. ``serve.build_server`` from
    ``[server].auth_env`` — ADR-0004 / T001); ``None`` (the default) means auth
    is OFF, identical to today's behaviour. Every route requires this token
    (``Authorization: Bearer <t>`` or ``x-api-key: <t>``, constant-time compare)
    except ``GET /healthz``. ``purpose`` is an optional
    :class:`~anvil_serving.router.purpose.PurposeRouter` (gpu-reservations:T010):
    when set, ``POST /v1/embeddings`` and ``POST /v1/rerank`` are routed by
    model name through it (under the same token auth); when ``None`` (the
    default) both paths stay 404 exactly as before. ``audio`` is an optional
    :class:`~anvil_serving.router.audio.AudioGateway`; when set it requires a
    resolved ``auth_token`` and exposes the same-token-authenticated JSON
    ``POST /v1/audio/transcriptions`` and
    ``POST /v1/audio/speech`` routes. When ``None`` (the default), both audio
    routes stay 404. Dialects echo the caller-selected alias in the wire
    response. Call ``server.serve_forever()`` (typically on a background
    thread) to run.
    """
    if audio is not None and auth_token is None:
        raise ValueError("an AudioGateway requires a resolved front-door auth token")
    if gateway is not None and auth_token is None:
        raise ValueError("a ProtocolGateway requires a resolved front-door auth token")
    validated_operator_routes = _validated_operator_routes(operator_routes)
    httpd = ThreadingHTTPServer(
        (host, port),
        _make_handler(
            backend, timeout, model_routes, exhaustion_status, auth_token,
            purpose, audio, gateway, authorization_policy, validated_operator_routes,
        ),
    )
    httpd.daemon_threads = True  # don't let connection threads block shutdown
    return httpd


def _parse_artifact_range(value: str | None) -> tuple[int | None, int | None]:
    if value is None:
        return None, None
    match = re.fullmatch(r"bytes=([0-9]+)-([0-9]*)", value)
    if match is None:
        raise MediaError("artifact_range_invalid", "artifact range is invalid", status=416)
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else None
    if end is not None and end < start:
        raise MediaError("artifact_range_invalid", "artifact range is invalid", status=416)
    return start, end
