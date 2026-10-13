"""Bounded native Memory bridge for one Connect principal.

The caller presents a public-client OIDC access token. The bridge reuses the
native Agents admission contract, substitutes one protected, principal-owned
router credential, and forwards only the native Memory REST surface. Neither
credential is returned, logged, persisted by this process, or sent in argv.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import stat
import threading
import time
from urllib.parse import urlsplit

from ..memory_access import BANK as _BANK
from ..native_agents.auth import AdmissionDenied, NativeAuthenticator, protected_read, strict_json


CAPABILITIES = ("banks", "list", "detail", "invalidate", "restore")
MAX_BODY = 64 * 1024
MAX_RESPONSE = 2 * 1024 * 1024
_PRINCIPAL = re.compile(r"human:[0-9a-f]{64}\Z")
_KEY_ID = re.compile(r"key_[0-9a-f]{16}\Z")
_SECRET = re.compile(r"ask_[A-Za-z0-9_-]{43}\Z")
_EPOCH = re.compile(r"[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_DEPLOYMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


class GatewayError(Exception):
    """Safe caller-facing failure without credential or provider detail."""

    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


def _canonical_router_origin(raw: object) -> str:
    if type(raw) is not str or len(raw) > 256 or any(ord(char) < 33 or ord(char) > 126 for char in raw):
        raise ValueError("invalid router origin")
    try:
        parsed = urlsplit(raw)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment or parsed.path not in ("", "/")
                or parsed.port is None or not 1024 <= parsed.port <= 65535
                or parsed.netloc != f"127.0.0.1:{parsed.port}"):
            raise ValueError("invalid router origin")
    except ValueError:
        raise ValueError("invalid router origin") from None
    return f"http://127.0.0.1:{parsed.port}"


def validate_config(value: object) -> dict:
    fields = {"version", "deployment_id", "listen_port", "expected_principal", "credential_name",
              "credential_sha256", "router_origin", "timeout_seconds", "auth"}
    if type(value) is not dict or set(value) != fields or value.get("version") != 1:
        raise ValueError("invalid native Memory configuration")
    if type(value["listen_port"]) is not int or isinstance(value["listen_port"], bool) or not 1024 <= value["listen_port"] <= 65535:
        raise ValueError("invalid listen port")
    if type(value["deployment_id"]) is not str or not _DEPLOYMENT.fullmatch(value["deployment_id"]):
        raise ValueError("invalid deployment id")
    if type(value["expected_principal"]) is not str or not _PRINCIPAL.fullmatch(value["expected_principal"]):
        raise ValueError("invalid expected principal")
    if value["credential_name"] != "memory-key":
        raise ValueError("invalid credential name")
    if type(value["credential_sha256"]) is not str or not _DIGEST.fullmatch(value["credential_sha256"]):
        raise ValueError("invalid credential digest")
    timeout = value["timeout_seconds"]
    if type(timeout) is not int or isinstance(timeout, bool) or not 1 <= timeout <= 300:
        raise ValueError("invalid router timeout")
    # NativeAuthenticator performs the closed auth-schema validation. Constructing
    # it has no provider or filesystem side effects.
    try:
        NativeAuthenticator(value["auth"])
    except AdmissionDenied:
        raise ValueError("invalid native authentication configuration") from None
    # NativeAuthenticator normalizes client IDs to an internal tuple. Keep the
    # original validated JSON shape so serve() can construct the live instance
    # once without feeding that private normalized form back through validation.
    return {**value, "router_origin": _canonical_router_origin(value["router_origin"])}


def load_config(path: str | Path) -> dict:
    try:
        value = strict_json(protected_read(path, 1024 * 1024))
        return validate_config(value)
    except AdmissionDenied:
        raise ValueError("invalid native Memory configuration") from None


def credential_path(name: str, deployment_id: str, environ: dict | os._Environ = os.environ) -> Path:
    directory = environ.get("CREDENTIALS_DIRECTORY", "")
    if (name != "memory-key" or type(deployment_id) is not str or not _DEPLOYMENT.fullmatch(deployment_id)
            or not directory or "\x00" in directory):
        raise ValueError("systemd credential is unavailable")
    root = Path(directory)
    expected = Path("/run/credentials") / f"anvil-native-memory-{deployment_id}.service"
    if not root.is_absolute() or root != expected:
        raise ValueError("systemd credential is unavailable")
    return root / name


def read_systemd_credential(filename: str | Path, maximum: int) -> bytes:
    """Read one pinned systemd credential without weakening host file custody."""
    path = Path(filename)
    try:
        expected_root = Path("/run/credentials")
        if not path.is_absolute() or path.name != "memory-key" or path.parent.parent != expected_root:
            raise AdmissionDenied()
        uid, gid = os.geteuid(), os.getegid()
        for parent in reversed(path.parents):
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise AdmissionDenied()
            if parent == path.parent:
                if (info.st_uid not in {0, uid} or info.st_gid not in {0, gid}
                        or stat.S_IMODE(info.st_mode) not in {0o500, 0o550}):
                    raise AdmissionDenied()
            elif info.st_uid != 0 or info.st_mode & 0o022:
                raise AdmissionDenied()
        if not os.statvfs(path.parent).f_flag & getattr(os, "ST_RDONLY", 1):
            raise AdmissionDenied()
        directory = path.parent.stat()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_uid not in {0, uid} or before.st_gid not in {0, gid}
                    or stat.S_IMODE(before.st_mode) not in {0o400, 0o440}
                    or before.st_dev != directory.st_dev or not 1 <= before.st_size <= maximum):
                raise AdmissionDenied()
            chunks, size = [], 0
            while size <= maximum:
                part = os.read(fd, min(65536, maximum + 1 - size))
                if not part:
                    break
                chunks.append(part)
                size += len(part)
            after = os.fstat(fd)
            def identity(value):
                return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                        value.st_uid, value.st_gid, value.st_mode, value.st_nlink)
            if size > maximum or identity(before) != identity(after):
                raise AdmissionDenied()
            return b"".join(chunks)
        finally:
            os.close(fd)
    except (OSError, ValueError):
        raise AdmissionDenied() from None


def _memory_credential(raw: bytes, expected_sha256: str, now: float) -> dict:
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256):
        raise GatewayError(503, "memory_unavailable")
    try:
        value = strict_json(raw)
    except AdmissionDenied:
        raise GatewayError(503, "memory_unavailable") from None
    fields = {"schema", "principal", "generation", "epoch", "key_id", "secret", "expires_at", "previous"}
    if (type(value) is not dict or set(value) != fields or value.get("schema") != "anvil-user-memory-key/v1"
            or type(value.get("principal")) is not str or not _PRINCIPAL.fullmatch(value["principal"])
            or type(value.get("generation")) is not str
            or not re.fullmatch(r"[1-9][0-9]{0,19}", value["generation"])
            or int(value["generation"]) >= 2**64
            or type(value.get("epoch")) is not str or not _EPOCH.fullmatch(value["epoch"])
            or type(value.get("key_id")) is not str or not _KEY_ID.fullmatch(value["key_id"])
            or type(value.get("secret")) is not str or not _SECRET.fullmatch(value["secret"])
            or type(value.get("expires_at")) is not int or isinstance(value["expires_at"], bool)
            or not now < value["expires_at"]):
        raise GatewayError(503, "memory_unavailable")
    previous = value["previous"]
    if previous is not None:
        if (type(previous) is not dict or set(previous) != {"key_id", "secret", "retired"}
                or previous["key_id"] is not None and (type(previous["key_id"]) is not str or not _KEY_ID.fullmatch(previous["key_id"]))
                or type(previous["secret"]) is not str or not _SECRET.fullmatch(previous["secret"])
                or previous["secret"] == value["secret"] or previous["retired"] is not True):
            raise GatewayError(503, "memory_unavailable")
    return value


def router_request(origin: str, body: bytes, credential: str, timeout: int) -> tuple[int, dict]:
    """Fixed loopback request with no redirects, proxies, or unbounded reads."""
    parsed = urlsplit(origin)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=min(timeout, 10))
    held_socket: list[socket.socket | None] = [None]
    expired = threading.Event()

    def cancel() -> None:
        expired.set()
        active = connection.sock or held_socket[0]
        if active is not None:
            try:
                active.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    timer = threading.Timer(timeout, cancel)
    timer.daemon = True
    timer.start()
    try:
        connection.connect()
        held_socket[0] = connection.sock
        if expired.is_set():
            raise GatewayError(504, "memory_timeout")
        connection.request("POST", "/v1/memory", body=body, headers={
            "Authorization": "Bearer " + credential,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Connection": "close",
            "User-Agent": "AnvilConnect-NativeMemory/1.0",
        })
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE + 1)
        if expired.is_set():
            raise GatewayError(504, "memory_timeout")
        if len(raw) > MAX_RESPONSE:
            raise GatewayError(503, "memory_unavailable")
        if response.status == 401:
            # This credential belongs to the bridge, not the phone. Do not turn
            # a server-side rotation fault into a native OIDC sign-in failure.
            raise GatewayError(503, "memory_unavailable")
        if response.status not in {200, 400, 403, 404, 413, 422, 429, 503, 504}:
            raise GatewayError(503, "memory_unavailable")
        if response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise GatewayError(503, "memory_unavailable")
        try:
            value = strict_json(raw)
        except AdmissionDenied:
            raise GatewayError(503, "memory_unavailable") from None
        if type(value) is not dict:
            raise GatewayError(503, "memory_unavailable")
        return response.status, value
    except GatewayError:
        raise
    except (OSError, http.client.HTTPException, ValueError):
        if expired.is_set():
            raise GatewayError(504, "memory_timeout") from None
        raise GatewayError(503, "memory_unavailable") from None
    finally:
        timer.cancel()
        connection.close()


class NativeMemoryGateway:
    def __init__(self, config: dict, auth: NativeAuthenticator, receipt: Path, *,
                 read=read_systemd_credential, transport=router_request, clock=time.time):
        self.config, self.auth, self.receipt = config, auth, receipt
        self.read, self.transport, self.clock = read, transport, clock
        self._upstream = threading.BoundedSemaphore(4)

    def _authorize(self, bearer: str):
        try:
            admission = self.auth.authorize(bearer)
        except AdmissionDenied:
            raise GatewayError(401, "unauthorized") from None
        if admission.resource != "native-agents" or admission.principal != self.config["expected_principal"]:
            raise GatewayError(403, "memory_forbidden")
        try:
            raw = self.read(self.receipt, 4096)
        except AdmissionDenied:
            raise GatewayError(503, "memory_unavailable") from None
        credential = _memory_credential(raw, self.config["credential_sha256"], self.clock())
        if credential["principal"] != admission.principal or credential["generation"] != str(admission.generation):
            raise GatewayError(403, "memory_forbidden")
        return admission, credential, raw

    def capabilities(self, bearer: str) -> dict:
        admission, _credential, _raw = self._authorize(bearer)
        return {"version": 1, "owner_id": admission.principal, "capabilities": list(CAPABILITIES)}

    def memory(self, bearer: str, body: object) -> tuple[int, dict]:
        admission, credential, original = self._authorize(bearer)
        _validate_envelope(body, admission.principal)
        raw = json.dumps(body, separators=(",", ":")).encode()
        if len(raw) > MAX_BODY:
            raise GatewayError(413, "request_too_large")
        if not self._upstream.acquire(blocking=False):
            raise GatewayError(503, "memory_busy")
        try:
            status, result = self.transport(self.config["router_origin"], raw, credential["secret"],
                                            self.config["timeout_seconds"])
        finally:
            self._upstream.release()
        if not self.auth.recheck(admission):
            raise GatewayError(401, "unauthorized")
        try:
            current = self.read(self.receipt, 4096)
        except AdmissionDenied:
            raise GatewayError(503, "memory_unavailable") from None
        if not hmac.compare_digest(current, original):
            raise GatewayError(503, "memory_unavailable")
        _memory_credential(current, self.config["credential_sha256"], self.clock())
        return status, result


def _validate_envelope(body: object, principal: str) -> None:
    if type(body) is not dict or body.get("alias") != "memory.user" or body.get("operation") not in CAPABILITIES:
        raise GatewayError(422, "invalid_request")
    operation = body["operation"]
    fields = {"alias", "operation", "arguments"} if operation == "banks" else {"alias", "operation", "arguments", "bank"}
    if set(body) not in (fields, fields - {"bank"}) or type(body.get("arguments")) is not dict:
        raise GatewayError(422, "invalid_request")
    bank = body.get("bank")
    if bank is not None and (type(bank) is not str or not _BANK.fullmatch(bank)):
        raise GatewayError(422, "invalid_request")
    if operation == "banks":
        arguments = body["arguments"]
        if (set(arguments) - {"native", "expected_principal", "limit", "offset"}
                or arguments.get("native") is not True
                or arguments.get("expected_principal") != principal):
            raise GatewayError(403, "memory_forbidden")


def handler(gateway: NativeMemoryGateway, *, header_timeout: float = 10):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "AnvilNativeMemory"

        def log_message(self, *_args):
            pass

        def handle(self):
            expired = threading.Event()
            def cancel_headers() -> None:
                expired.set()
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            self._header_expired = expired
            self._header_timer = threading.Timer(header_timeout, cancel_headers)
            self._header_timer.daemon = True
            self._header_timer.start()
            try:
                super().handle()
            finally:
                self._header_timer.cancel()

        def parse_request(self):
            result = super().parse_request()
            self._header_timer.cancel()
            if self._header_expired.is_set():
                self.close_connection = True
                return False
            return result

        def do_GET(self):
            self._dispatch()

        def do_POST(self):
            self._dispatch()

        def do_PUT(self):
            self._send(405, {"code": "method_not_allowed"})

        def do_DELETE(self):
            self._send(405, {"code": "method_not_allowed"})

        def do_PATCH(self):
            self._send(405, {"code": "method_not_allowed"})

        do_HEAD = do_PUT
        do_OPTIONS = do_PUT
        do_CONNECT = do_PUT
        do_TRACE = do_PUT

        def _send(self, status: int, value: dict) -> None:
            raw = json.dumps(value, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(raw)

        def _bearer(self) -> str:
            values = self.headers.get_all("Authorization", [])
            if len(values) != 1 or not values[0].startswith("Bearer "):
                raise GatewayError(401, "unauthorized")
            bearer = values[0][7:]
            if not 1 <= len(bearer) <= 8192 or any(ord(char) < 33 or ord(char) > 126 for char in bearer):
                raise GatewayError(401, "unauthorized")
            return bearer

        def _length(self) -> int:
            if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) > 1:
                raise GatewayError(400, "invalid_request")
            raw = self.headers.get("Content-Length", "0")
            if not raw.isdigit():
                raise GatewayError(400, "invalid_request")
            size = int(raw)
            if size > MAX_BODY:
                raise GatewayError(413, "request_too_large")
            return size

        def _dispatch(self) -> None:
            try:
                bearer = self._bearer()
                size = self._length()
                if self.command == "GET":
                    if self.path != "/v1/native/memory/capabilities" or size:
                        raise GatewayError(404 if not size else 400, "not_found" if not size else "invalid_request")
                    self._send(200, gateway.capabilities(bearer))
                    return
                if self.command != "POST" or self.path != "/v1/memory":
                    raise GatewayError(404, "not_found")
                if size == 0 or self.headers.get_content_type() != "application/json":
                    raise GatewayError(400, "invalid_request")
                self.connection.settimeout(10)
                expired = threading.Event()
                def cancel_read() -> None:
                    expired.set()
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                timer = threading.Timer(11, cancel_read)
                timer.daemon = True
                timer.start()
                try:
                    raw = self.rfile.read(size)
                finally:
                    timer.cancel()
                if expired.is_set() or len(raw) != size:
                    raise GatewayError(400, "invalid_request")
                try:
                    body = strict_json(raw)
                except AdmissionDenied:
                    raise GatewayError(400, "invalid_request") from None
                status, result = gateway.memory(bearer, body)
                self._send(status, result)
            except GatewayError as error:
                self._send(error.status, {"code": error.code})
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                self.close_connection = True
            except Exception:
                self._send(503, {"code": "memory_unavailable"})

    return Handler


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, maximum_connections: int = 32, **kwargs):
        self.slots = threading.BoundedSemaphore(maximum_connections)
        super().__init__(*args, **kwargs)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(10)
        return request, address

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def serve(config_path: str | Path) -> None:
    config = load_config(config_path)
    auth = NativeAuthenticator(config["auth"])
    receipt = credential_path(config["credential_name"], config["deployment_id"])
    # Fail before binding if the protected credential is unavailable or differs.
    _memory_credential(read_systemd_credential(receipt, 4096), config["credential_sha256"], time.time())
    gateway = NativeMemoryGateway(config, auth, receipt)
    server = BoundedServer(("127.0.0.1", config["listen_port"]), handler(gateway))
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m anvil_serving.native_memory_gateway", allow_abbrev=False)
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    serve(args.config)
