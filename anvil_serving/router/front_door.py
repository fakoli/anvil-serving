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

import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterable, Optional

from .backends import EchoBackend
from .dialects import Dialect
from .dialects.anthropic import AnthropicDialect
from .dialects.openai import OpenAIDialect
from .discovery import models_payload
from .intent import PRESETS, Preset
from .internal import Backend, DialectError, NoAvailableTierError

# Path -> dialect. Stateless, so module-level singletons are fine.
_ROUTES = {
    "/v1/chat/completions": OpenAIDialect(),
    "/v1/messages": AnthropicDialect(),
}

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

# Pre-compiled pattern: a valid Content-Length is one or more ASCII digits,
# nothing else (no sign, underscores, whitespace, or Unicode digits).
_DIGIT_RE: re.Pattern = re.compile(r"[0-9]+")


def _make_handler(backend: Backend, timeout: Optional[float],
                  presets: Iterable[Preset]):
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
        def _json(self, status: int, obj) -> None:
            payload = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            # Advertise close whenever we're closing (request asked for it, or we
            # forced it on a framing error) so the client doesn't reuse the socket.
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

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
            # answer a clean 503 instead of a 200 + empty/truncated body. (For a
            # plain generator backend this call is a no-op: its body runs lazily,
            # so the M0 mid-stream close-on-error contract below is unchanged.)
            #
            # Any other exception from generate() is also surfaced as a clean 500
            # here, before the 200 is committed, so the client always sees a real
            # HTTP error status for pre-stream failures.
            try:
                deltas = backend.generate(request)
            except NoAvailableTierError as e:
                # Log the detail server-side; send a generic message to the client
                # (tier identities / remediation are internal-operator information).
                print(f"[anvil] 503 no available tier: {e}", file=sys.stderr)
                self._error(
                    503, "service_unavailable",
                    "no quality-gated tier is available for this request",
                    dialect=dialect,
                )
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
            frames = dialect.stream(request, deltas)
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

        # --- routes ----------------------------------------------------------
        def do_GET(self) -> None:
            # Drain any unexpected request body to keep the keep-alive socket in
            # sync.  GETs are conventionally bodyless; a caller that sends one
            # leaves bytes on the wire that would desync the connection for the
            # next pipelined request.  Drain up to MAX_BODY_BYTES; close if the
            # claimed length exceeds the cap or the header is malformed.
            cl_get = self.headers.get("Content-Length")
            if cl_get is not None:
                if _DIGIT_RE.fullmatch(cl_get):
                    get_n = int(cl_get)
                    if 0 < get_n <= MAX_BODY_BYTES:
                        try:
                            self.rfile.read(get_n)
                        except Exception:
                            self.close_connection = True
                    elif get_n > MAX_BODY_BYTES:
                        self.close_connection = True
                else:
                    self.close_connection = True

            if not _CONCURRENCY_LIMIT.acquire(blocking=False):
                self.close_connection = True
                self._error(503, "server_busy", "server busy; try again later")
                return
            try:
                route = self.path.split("?", 1)[0].rstrip("/")
                if route in ("/healthz", "/health"):
                    self._json(200, {
                        "status": "ok",
                        "dialects": sorted(d.name for d in _ROUTES.values()),
                    })
                elif route == "/v1/models":
                    # Preset discovery: list the configured presets (intent tokens)
                    # as OpenAI-shaped "models" so a harness model picker can find
                    # them. Derived from the canonical presets passed in.
                    self._json(200, models_payload(presets))
                else:
                    self._error(404, "not_found", f"no route {self.path}")
            finally:
                _CONCURRENCY_LIMIT.release()

        def do_POST(self) -> None:
            # Acquire the concurrency semaphore before doing any work.  The
            # request line and headers are already parsed by handle_one_request,
            # so we can send a proper 503 if the server is saturated.
            if not _CONCURRENCY_LIMIT.acquire(blocking=False):
                self.close_connection = True
                self._error(503, "server_busy", "server busy; try again later")
                return
            try:
                self._post_inner()
            finally:
                _CONCURRENCY_LIMIT.release()

        def _post_inner(self) -> None:
            """Core POST dispatch, called under the concurrency semaphore."""
            path = self.path.split("?", 1)[0]

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

            # --- Route check (establishes dialect for dialect-aware errors) --
            dialect: Optional[Dialect] = _ROUTES.get(path)
            if dialect is None:  # unknown route — drain if body is well-framed
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
                    payload = dialect.render(request, text)
                except NoAvailableTierError as e:
                    # No quality-gated tier is bound for this work class -> 503.
                    # Log the detail (tier names, remediation) server-side; send
                    # a generic message to the client.
                    print(f"[anvil] 503 no available tier: {e}", file=sys.stderr)
                    self._error(
                        503, "service_unavailable",
                        "no quality-gated tier is available for this request",
                        dialect=dialect,
                    )
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
                presets: Optional[Iterable[Preset]] = None) -> ThreadingHTTPServer:
    """Build (but do not start) the front-door server.

    Pass ``port=0`` to bind an ephemeral port (read it back from
    ``server.server_address[1]``). ``backend`` defaults to :class:`EchoBackend`.
    ``timeout`` is the per-connection idle read timeout in seconds (finite by
    default so abandoned keep-alive sockets can't leak threads/FDs); pass
    ``None`` to disable. ``presets`` are the work-class tokens ``GET /v1/models``
    advertises; defaults to the canonical :data:`~anvil_serving.router.intent.PRESETS`
    (injectable like ``backend`` for tests). Call ``server.serve_forever()``
    (typically on a background thread) to run.
    """
    if backend is None:
        backend = EchoBackend()
    if presets is None:
        presets = PRESETS
    httpd = ThreadingHTTPServer((host, port),
                                _make_handler(backend, timeout, presets))
    httpd.daemon_threads = True  # don't let connection threads block shutdown
    return httpd


def serve(host: str = "127.0.0.1", port: int = 8000,
          backend: Optional[Backend] = None,
          timeout: Optional[float] = 120) -> None:
    """Build and run the front door until interrupted."""
    httpd = make_server(host, port, backend, timeout)
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
