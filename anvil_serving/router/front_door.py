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
client knows the body ended without relying on connection close.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .backends import EchoBackend
from .dialects import Dialect
from .dialects.anthropic import AnthropicDialect
from .dialects.openai import OpenAIDialect
from .internal import Backend

# Path -> dialect. Stateless, so module-level singletons are fine.
_ROUTES = {
    "/v1/chat/completions": OpenAIDialect(),
    "/v1/messages": AnthropicDialect(),
}


def _make_handler(backend: Backend):
    class FrontDoorHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "anvil-front-door/0.1"

        # --- helpers ---------------------------------------------------------
        def _json(self, status: int, obj) -> None:
            payload = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _error(self, status: int, etype: str, message: str) -> None:
            self._json(status, {"error": {"type": etype, "message": message}})

        def _write_sse(self, dialect: Dialect, request) -> None:
            """Stream the backend's deltas as native SSE, chunk-framed + flushed."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            deltas = backend.generate(request)
            for frame in dialect.stream(request, deltas):
                if not frame:
                    continue  # never emit a zero-length chunk (would end stream)
                self.wfile.write(b"%x\r\n" % len(frame))
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                self.wfile.flush()  # push each SSE event to the client immediately
            self.wfile.write(b"0\r\n\r\n")  # chunked terminator
            self.wfile.flush()

        # --- routes ----------------------------------------------------------
        def do_GET(self) -> None:
            if self.path.split("?", 1)[0].rstrip("/") in ("/healthz", "/health"):
                self._json(200, {"status": "ok",
                                 "dialects": sorted(d.name for d in _ROUTES.values())})
            else:
                self._error(404, "not_found", f"no route {self.path}")

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            dialect: Optional[Dialect] = _ROUTES.get(path)
            if dialect is None:
                self._error(404, "not_found", f"no route {path}")
                return

            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
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
            except Exception as e:  # malformed but JSON-parseable body
                self._error(400, "invalid_request", f"bad request: {e}")
                return

            if request.stream:
                self._write_sse(dialect, request)
            else:
                text = "".join(backend.generate(request))
                self._json(200, dialect.render(request, text))

        def log_message(self, *args) -> None:  # keep the server quiet
            pass

    return FrontDoorHandler


def make_server(host: str = "127.0.0.1", port: int = 8000,
                backend: Optional[Backend] = None) -> ThreadingHTTPServer:
    """Build (but do not start) the front-door server.

    Pass ``port=0`` to bind an ephemeral port (read it back from
    ``server.server_address[1]``). ``backend`` defaults to :class:`EchoBackend`.
    Call ``server.serve_forever()`` (typically on a background thread) to run.
    """
    if backend is None:
        backend = EchoBackend()
    httpd = ThreadingHTTPServer((host, port), _make_handler(backend))
    httpd.daemon_threads = True  # don't let connection threads block shutdown
    return httpd


def serve(host: str = "127.0.0.1", port: int = 8000,
          backend: Optional[Backend] = None) -> None:
    """Build and run the front door until interrupted."""
    httpd = make_server(host, port, backend)
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
