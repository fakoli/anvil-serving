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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .backends import EchoBackend
from .dialects import Dialect
from .dialects.anthropic import AnthropicDialect
from .dialects.openai import OpenAIDialect
from .internal import Backend, DialectError

# Path -> dialect. Stateless, so module-level singletons are fine.
_ROUTES = {
    "/v1/chat/completions": OpenAIDialect(),
    "/v1/messages": AnthropicDialect(),
}


def _make_handler(backend: Backend, timeout: Optional[float]):
    class FrontDoorHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "anvil-front-door/0.1"
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
                          drainable: bool, n: int) -> None:
            """Respond to a pre-body framing/routing error WITHOUT desyncing a
            pooled keep-alive socket (RFC 7230 3.3.3/6.6).

            If the body length is known (``drainable``), drain it so the socket
            stays in sync and the connection survives — closing instead would, on
            an unread body, trigger an RST on Windows that truncates this very
            response. If the length is undeterminable (chunked / unparseable
            Content-Length), we cannot realign, so we must close. Generic
            envelope (these are transport-level, pre-dialect errors).
            """
            if drainable:
                if n:
                    try:
                        self.rfile.read(n)
                    except Exception:
                        self.close_connection = True  # short read -> can't realign
            else:
                self.close_connection = True
            self._error(status, etype, message)

        def _write_sse(self, dialect: Dialect, request) -> None:
            """Stream the backend's deltas as native SSE, flushed per event.

            HTTP/1.1: chunked transfer-encoding. HTTP/1.0 (no chunked support):
            close-delimited — raw frames, then close the socket (mirrors
            ``multiplexer.relay``).
            """
            chunked = self.request_version == "HTTP/1.1"
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
            deltas = backend.generate(request)
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
            if self.path.split("?", 1)[0].rstrip("/") in ("/healthz", "/health"):
                self._json(200, {"status": "ok",
                                 "dialects": sorted(d.name for d in _ROUTES.values())})
            else:
                self._error(404, "not_found", f"no route {self.path}")

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]

            # Resolve the request-body length up front so error paths can DRAIN a
            # known-length body and keep the keep-alive socket in sync, instead
            # of closing it. (A chunked upload or an unparseable Content-Length is
            # undrainable -> the early-return paths below must close.)
            te = (self.headers.get("Transfer-Encoding") or "").lower()
            cl = self.headers.get("Content-Length")
            n = 0
            drainable = "chunked" not in te
            if drainable and cl is not None:
                try:
                    n = int(cl)
                except (TypeError, ValueError):
                    drainable = False
                else:
                    if n < 0:
                        drainable = False

            dialect: Optional[Dialect] = _ROUTES.get(path)
            if dialect is None:  # unknown route (body, if well-framed, is drained)
                self._fail_framing(404, "not_found", f"no route {path}", drainable, n)
                return
            if "chunked" in te:  # we don't decode chunked uploads
                self._fail_framing(
                    411, "invalid_request",
                    "chunked request bodies are unsupported; send Content-Length",
                    drainable=False, n=0)
                return
            if cl is not None and not drainable:  # malformed/negative Content-Length
                self._fail_framing(400, "invalid_request",
                                   f"invalid Content-Length: {cl!r}",
                                   drainable=False, n=0)
                return

            raw = self.rfile.read(n) if n else b""  # body drained from here on
            try:
                body = json.loads(raw or b"{}")
            except Exception as e:
                self._error(400, "invalid_request", f"bad JSON body: {e}")
                return
            if not isinstance(body, dict):
                self._error(400, "invalid_request", "body must be a JSON object")
                return

            try:
                request = dialect.parse_request(body)
            except DialectError as e:  # dialect-specific rejection (e.g. max_tokens)
                self._error(e.status, e.etype, e.message, dialect=dialect)
                return
            except Exception as e:  # other malformed but JSON-parseable body
                self._error(400, "invalid_request", f"bad request: {e}", dialect=dialect)
                return

            if request.stream:
                self._write_sse(dialect, request)
            else:
                # Symmetric with the streaming close-on-error contract: a real
                # backend can raise here, so surface a clean 500 in the dialect's
                # native envelope rather than dropping the request with a traceback.
                try:
                    text = "".join(backend.generate(request))
                    payload = dialect.render(request, text)
                except Exception as e:
                    self._error(500, "internal_error",
                                f"backend error: {e}", dialect=dialect)
                    return
                self._json(200, payload)

        def log_message(self, *args) -> None:  # keep the server quiet
            pass

    FrontDoorHandler.timeout = timeout
    return FrontDoorHandler


def make_server(host: str = "127.0.0.1", port: int = 8000,
                backend: Optional[Backend] = None,
                timeout: Optional[float] = 120) -> ThreadingHTTPServer:
    """Build (but do not start) the front-door server.

    Pass ``port=0`` to bind an ephemeral port (read it back from
    ``server.server_address[1]``). ``backend`` defaults to :class:`EchoBackend`.
    ``timeout`` is the per-connection idle read timeout in seconds (finite by
    default so abandoned keep-alive sockets can't leak threads/FDs); pass
    ``None`` to disable. Call ``server.serve_forever()`` (typically on a
    background thread) to run.
    """
    if backend is None:
        backend = EchoBackend()
    httpd = ThreadingHTTPServer((host, port), _make_handler(backend, timeout))
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
