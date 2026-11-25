"""Anvil Voice memo capture over the Capability Gateway (ADR-0048).

A browser or WebView client posts an Ogg Opus memo. The service converts it to
16 kHz mono PCM16 WAV with ffmpeg, transcribes it in gateway-sized segments
through ``POST /v1/audio/transcriptions`` with a route-bound device key, stores
the audio and transcript, and returns the transcript. ``/v1/memos/preview``
transcribes a trailing window for live text and stores nothing.

Trust model: the listener binds loopback only, and the ``Tailscale-User-Login``
header is trusted because ``tailscale serve`` is the only remote path to it.
Logs carry method, path, status, sizes, timing and allow decisions only; never
audio, transcript text, or identity values. Memo stores are evaluation-excluded.
"""
from __future__ import annotations

import base64
import email.parser
import email.policy
import http.client
import ipaddress
import json
import os
import re
import secrets
import struct
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Mapping, Optional

from ..control_plane.mcp.auth_file import AuthFileError, read_private_auth_file
from . import config as voice_config
from .corpus import EVALUATION_EXCLUDED_MARKER

ConfigError = voice_config.ConfigError

MEMOS_PATH = "/v1/memos"
PREVIEW_PATH = "/v1/memos/preview"
HEALTH_PATH = "/healthz"
TRANSCRIPTIONS_PATH = "/v1/audio/transcriptions"
IDENTITY_HEADER = "Tailscale-User-Login"
# Default: a local Studio/browser origin such as http://127.0.0.1:51358.
DEFAULT_ORIGIN_PATTERN = r"http://127\.0\.0\.1:\d{2,5}"
SAMPLE_RATE = 16_000
BYTES_PER_SECOND = SAMPLE_RATE * 2
# 130 s of PCM16 plus a header stays under the gateway's default 4 MiB decoded-input limit.
MAX_SEGMENT_SECONDS = 130
_MAX_RESPONSE_BYTES = 1024 * 1024
_KEYS = frozenset({
    "router_url", "router_key_env", "router_key_file", "route", "allowed_logins_env", "allowed_origin_pattern",
    "data_dir", "listen_host", "listen_port", "segment_seconds", "max_upload_bytes",
    "timeout_seconds",
})
_MARKER_TEXT = (
    "Captured voice memos: user content, not evidence.\n"
    "Never use this audio or these transcripts to train, fine-tune, or evaluate a model.\n"
    "anvil-serving STT corpus validation refuses audio under this directory (ADR-0048).\n"
)


class TranscriptionError(RuntimeError):
    """A content-free gateway transcription failure."""


@dataclass(frozen=True)
class MemoConfig:
    router_url: str
    router_key: str = field(repr=False)
    allowed_logins: frozenset = field(repr=False)
    data_dir: Path
    route: Optional[str] = None
    listen_host: str = "127.0.0.1"
    listen_port: int = 8790
    origin_pattern: re.Pattern = re.compile(DEFAULT_ORIGIN_PATTERN)
    segment_seconds: int = 110
    max_upload_bytes: int = 8 * 1024 * 1024
    timeout_seconds: float = 60.0


def _int(table, key, default, low, high):
    value = table.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise ConfigError("voice.memo.%s must be an integer from %d through %d" % (key, low, high))
    return value


def _str(table, key, default=None):
    value = table.get(key, default)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ConfigError("voice.memo.%s must be a non-empty string" % key)
    return value.strip() if value is not None else None


def load_memo_config(path: Optional[str] = None, *, env: Optional[Mapping[str, str]] = None) -> MemoConfig:
    """Load ``[voice.memo]`` and resolve its environment references, failing closed."""
    environ = os.environ if env is None else env
    data = voice_config.load_raw_manifest(path)
    table = (data.get("voice") or {}).get("memo")
    if not isinstance(table, dict):
        raise ConfigError("voice manifest has no [voice.memo] table")
    unknown = sorted(set(table) - _KEYS)
    if unknown:
        raise ConfigError("unknown voice.memo keys: %s" % ", ".join(unknown))

    router_url = _str(table, "router_url")
    parsed = urllib.parse.urlsplit(router_url or "")
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.query or parsed.fragment:
        raise ConfigError("voice.memo.router_url must be an http(s) gateway base URL")
    router_url = router_url.rstrip("/")
    if router_url.endswith("/v1"):
        router_url = router_url[:-3]

    def secret_reference(key):
        name = _str(table, key)
        if name is None:
            raise ConfigError("voice.memo.%s is required" % key)
        value = (environ.get(name) or "").strip()
        if not value:
            raise ConfigError("voice.memo.%s names %s, which is unset or empty" % (key, name))
        return value

    key_file = _str(table, "router_key_file")
    if (key_file is None) == (table.get("router_key_env") is None):
        raise ConfigError("voice.memo needs exactly one of router_key_env or router_key_file")
    if key_file is None:
        router_key = secret_reference("router_key_env")
    else:
        try:  # the protected file written by `router keys create --out`
            router_key = read_private_auth_file(key_file, max_bytes=4096).decode("utf-8").strip()
        except (AuthFileError, UnicodeDecodeError):
            raise ConfigError("voice.memo.router_key_file must be a private, non-empty key file") from None
        if not router_key:
            raise ConfigError("voice.memo.router_key_file must be a private, non-empty key file")
    logins = frozenset(s.strip() for s in secret_reference("allowed_logins_env").split(",") if s.strip())
    if not logins:
        raise ConfigError("voice.memo.allowed_logins_env resolves to no logins")

    data_dir = _str(table, "data_dir")
    if data_dir is None or not os.path.isabs(os.path.expanduser(data_dir)):
        raise ConfigError("voice.memo.data_dir must be an absolute path")

    listen_host = _str(table, "listen_host", "127.0.0.1")
    try:
        loopback = ipaddress.ip_address(listen_host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        # The identity header is only trustworthy when tailscale serve is the sole path in.
        raise ConfigError("voice.memo.listen_host must be a loopback address")

    pattern = _str(table, "allowed_origin_pattern", DEFAULT_ORIGIN_PATTERN)
    try:
        origin_pattern = re.compile(pattern)
    except re.error as exc:
        raise ConfigError("voice.memo.allowed_origin_pattern is not a valid regex") from exc

    timeout = table.get("timeout_seconds", 60)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 600:
        raise ConfigError("voice.memo.timeout_seconds must be from 1 through 600")

    return MemoConfig(
        router_url=router_url,
        router_key=router_key,
        allowed_logins=logins,
        data_dir=Path(os.path.expanduser(data_dir)),
        route=_str(table, "route"),
        listen_host=listen_host,
        listen_port=_int(table, "listen_port", 8790, 1, 65535),
        origin_pattern=origin_pattern,
        segment_seconds=_int(table, "segment_seconds", 110, 1, MAX_SEGMENT_SECONDS),
        max_upload_bytes=_int(table, "max_upload_bytes", 8 * 1024 * 1024, 1024, 32 * 1024 * 1024),
        timeout_seconds=float(timeout),
    )


def parse_multipart(content_type: Optional[str], body: bytes) -> dict:
    """Return ``{field: (filename, bytes)}`` for a multipart/form-data body."""
    if not content_type or not content_type.lower().startswith("multipart/form-data"):
        raise ValueError("expected multipart/form-data")
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body)
    if not message.is_multipart():
        raise ValueError("malformed multipart body")
    fields = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name:
            fields[name] = (part.get_filename(), part.get_payload(decode=True) or b"")
    return fields


def ogg_to_wav(ogg: bytes) -> bytes:
    """Ogg Opus to 16 kHz mono PCM16 WAV through ffmpeg pipes, with no temporary files."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-sample_fmt", "s16", "-f", "wav", "pipe:1"],
        input=ogg, capture_output=True, timeout=60, check=False)
    if result.returncode != 0 or len(result.stdout) <= 44:
        raise ValueError("audio could not be decoded")
    return result.stdout


def wav_header(pcm_len: int) -> bytes:
    return (b"RIFF" + struct.pack("<I", 36 + pcm_len) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE, BYTES_PER_SECOND, 2, 16)
            + b"data" + struct.pack("<I", pcm_len))


def split_wav(wav: bytes, seconds: int) -> list:
    """Split a 16 kHz mono PCM16 WAV into standalone WAV segments of at most ``seconds``."""
    index = wav.find(b"data", 12)
    if wav[:4] != b"RIFF" or index < 0:
        raise ValueError("not a WAV file")
    pcm = wav[index + 8:]  # piped ffmpeg output may carry a placeholder size; trust the bytes
    pcm = pcm[: len(pcm) - (len(pcm) % 2)]
    step = seconds * BYTES_PER_SECOND
    # ponytail: fixed cuts can split a word; cut on silence if seam accuracy matters.
    return [wav_header(len(piece)) + piece
            for piece in (pcm[i:i + step] for i in range(0, len(pcm), step)) if piece]


def _post_json(url: str, payload: dict, token: str, timeout: float):
    """POST without proxy discovery or redirects, so audio reaches only the configured gateway."""
    parts = urllib.parse.urlsplit(url)
    connection_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    # ponytail: per-socket-operation timeout, not a wall-clock deadline; the gateway bounds upstream time.
    connection = connection_cls(parts.hostname, parts.port, timeout=timeout)
    try:
        connection.request("POST", parts.path, body=json.dumps(payload).encode("ascii"), headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + token})
        response = connection.getresponse()
        return response.status, response.read(_MAX_RESPONSE_BYTES + 1)
    finally:
        connection.close()


def transcribe_segment(config: MemoConfig, wav: bytes) -> str:
    """Transcribe one WAV segment through the gateway's normalized STT endpoint."""
    payload = {"purpose": "stt", "format": "wav", "is_final": True,
               "audio_b64": base64.b64encode(wav).decode("ascii")}
    if config.route:
        payload["route"] = config.route
    status, raw = _post_json(config.router_url + TRANSCRIPTIONS_PATH, payload,
                             config.router_key, config.timeout_seconds)
    if status != 200:
        raise TranscriptionError("gateway returned HTTP %d" % status)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise TranscriptionError("gateway response too large")
    try:
        text = json.loads(raw.decode("utf-8"))["text"]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError):
        raise TranscriptionError("gateway returned an invalid transcript") from None
    if not isinstance(text, str):
        raise TranscriptionError("gateway returned an invalid transcript")
    return text.strip()


def transcribe_memo(config: MemoConfig, ogg: bytes) -> str:
    segments = split_wav(ogg_to_wav(ogg), config.segment_seconds)
    return " ".join(t for t in (transcribe_segment(config, s) for s in segments) if t)


_FAILURES = (TranscriptionError, ValueError, OSError, http.client.HTTPException, subprocess.TimeoutExpired)


def prepare_store(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = data_dir / EVALUATION_EXCLUDED_MARKER
    if not marker.exists():
        marker.write_text(_MARKER_TEXT, encoding="utf-8")


def make_memo_server(config: MemoConfig, *,
                     transcribe: Optional[Callable[[bytes], str]] = None) -> ThreadingHTTPServer:
    """Bind the memo listener; ``transcribe`` is injectable for hermetic tests."""
    prepare_store(config.data_dir)
    transcriber = transcribe or (lambda ogg: transcribe_memo(config, ogg))

    def origin_allowed(origin):
        return bool(origin) and config.origin_pattern.fullmatch(origin) is not None

    class Handler(BaseHTTPRequestHandler):
        server_version = "anvil-voice-memo/1"

        def log_message(self, fmt, *args):  # default request lines can carry query strings
            pass

        def _log(self, status, started, note=""):
            print(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "method": self.command, "path": self.path.split("?", 1)[0], "status": status,
                "bytes_in": int(self.headers.get("Content-Length") or 0),
                "ms": round((time.monotonic() - started) * 1000),
                "login_ok": self.headers.get(IDENTITY_HEADER) in config.allowed_logins,
                "origin_ok": origin_allowed(self.headers.get("Origin")), "note": note,
            }), file=sys.stderr, flush=True)

        def _send(self, status, payload, started, note=""):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            origin = self.headers.get("Origin")
            if origin_allowed(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            self._log(status, started, note)

        def do_GET(self):
            started = time.monotonic()
            if self.path == HEALTH_PATH:
                return self._send(200, {"ok": True}, started)
            self._send(404, {"error": "not found"}, started)

        def do_OPTIONS(self):
            started = time.monotonic()
            path = self.path.split("?", 1)[0]
            if path not in (MEMOS_PATH, PREVIEW_PATH) or not origin_allowed(self.headers.get("Origin")):
                return self._send(403, {"error": "origin not allowed"}, started, "preflight refused")
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Access-Control-Allow-Methods", "POST")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")
            self.end_headers()
            self._log(204, started)

        def do_POST(self):
            started = time.monotonic()
            path = self.path.split("?", 1)[0]
            if path not in (MEMOS_PATH, PREVIEW_PATH):
                return self._send(404, {"error": "not found"}, started)
            if self.headers.get(IDENTITY_HEADER) not in config.allowed_logins:
                return self._send(403, {"error": "tailnet identity not allowed"}, started, "login refused")
            origin = self.headers.get("Origin")
            if origin and not origin_allowed(origin):
                return self._send(403, {"error": "origin not allowed"}, started, "origin refused")
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if not 0 < length <= config.max_upload_bytes:
                self.close_connection = True  # never read an unbounded or unannounced body
                return self._send(413, {"error": "upload missing or too large"}, started)
            try:
                fields = parse_multipart(self.headers.get("Content-Type"), self.rfile.read(length))
                audio = fields.get("audio", (None, b""))[1]
                if not audio:
                    raise ValueError("missing audio field")
                duration_ms = int((fields.get("durationMs") or (None, b"0"))[1] or 0)
            except ValueError as error:
                return self._send(400, {"error": str(error)}, started)

            if path == PREVIEW_PATH:  # live text while recording: transcribe, store nothing
                try:
                    return self._send(200, {"transcript": transcriber(audio)}, started, "preview")
                except _FAILURES as error:
                    return self._send(502, {"error": "transcription failed"}, started, type(error).__name__)

            memo_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
            (config.data_dir / (memo_id + ".ogg")).write_bytes(audio)  # keep audio even if STT fails
            try:
                transcript = transcriber(audio)
            except _FAILURES as error:
                return self._send(502, {"error": "transcription failed", "id": memo_id},
                                  started, type(error).__name__)
            memo = {"id": memo_id, "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "durationMs": duration_ms, "transcript": transcript}
            (config.data_dir / (memo_id + ".json")).write_text(
                json.dumps(memo, ensure_ascii=False), encoding="utf-8")
            self._send(200, memo, started)

    return ThreadingHTTPServer((config.listen_host, config.listen_port), Handler)
