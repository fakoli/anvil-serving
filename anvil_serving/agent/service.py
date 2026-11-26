"""Anvil Agent: a glasses client's bridge to the operator's own agents (ADR-0049).

Anvil Agent is its own product family. A browser or WebView glasses client
sends a spoken (Ogg Opus) or typed turn to ``POST /v1/agent/turn``. The service
transcribes speech through the gateway's ``POST /v1/audio/transcriptions`` with a
route-bound device key, asks the selected agent backend on the operator's host,
and returns the reply. It stores no turn data. Backends are declared in
``[agent.backends.<id>]``: a Hermes API server (``hermes-api``) or Pi through
pi-web's mobile bridge (``pi-web``). Their credentials stay server-side.

Agents drive the glasses through one MCP tool, ``memomind_glasses``, served at
``POST /mcp``. Its ops mirror MemoMind's ``memomind_control`` op codes (NOTIFY,
TP, LS, RC, MR, ASK, STATUS), limited to what a glasses plugin can do. The client
long-polls ``GET /v1/glasses/events`` while it is in the foreground; notify cards
persist in a bounded ring and control ops expire. Microphone ops only ask: the
wearer must press the glasses button before anything records.

Trust model: the listener binds loopback only, and the ``Tailscale-User-Login``
header is trusted because ``tailscale serve`` is the only remote path to it.
Logs carry method, path, status, sizes, timing and allow decisions only; never
audio, transcripts, replies, questions, answers or identity values.
"""
from __future__ import annotations

import collections
import email.parser
import email.policy
import http.client
import ipaddress
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from typing import Callable, Mapping, Optional

from .. import __version__
from ..control_plane.mcp.auth_file import AuthFileError, read_private_auth_file
from ..envfile import resolve_env_value
from ..paths import config_path
from ..scribe.service import TranscriptionError, _BoundedServer, parse_multipart, transcribe_memo

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - requires-python >= 3.11
    tomllib = None

CONFIG_NAME = "agent.toml"
_MAX_CONFIG_BYTES = 256 * 1024
IDENTITY_HEADER = "Tailscale-User-Login"
DEFAULT_ORIGIN_PATTERN = r"http://127\.0\.0\.1:\d{2,5}"
DEFAULT_PORT = 8792
HEALTH_PATH = "/healthz"
MCP_PATH = "/mcp"
AGENTS_PATH = "/v1/agents"
TURN_PATH = "/v1/agent/turn"
HERMES_TURN_PATH = "/v1/hermes/turn"  # v0.1 client alias for agent=hermes
NOTIFY_PATH = "/v1/glasses/notify"
EVENTS_PATH = "/v1/glasses/events"
ANSWER_PATH = "/v1/glasses/answer"
POST_PATHS = (TURN_PATH, HERMES_TURN_PATH, NOTIFY_PATH, ANSWER_PATH, MCP_PATH)
CORS_PATHS = (TURN_PATH, HERMES_TURN_PATH, NOTIFY_PATH, ANSWER_PATH, EVENTS_PATH, AGENTS_PATH)
BACKEND_KINDS = ("hermes-api", "pi-web")
MAX_SEGMENT_SECONDS = 130
MAX_TEXT_CHARS = 4000
_MAX_RESPONSE_BYTES = 1024 * 1024
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}\Z")
_KEYS = frozenset({
    "router_url", "router_key_env", "router_key_file", "route", "allowed_logins_env", "allowed_origin_pattern",
    "listen_host", "listen_port", "segment_seconds", "max_upload_bytes", "timeout_seconds",
    "turn_timeout_seconds", "max_recording_seconds", "max_concurrent_transcriptions", "backends",
})
_SLOT_WAIT_SECONDS = 30  # a spoken turn waits this long for a decode slot, then gets 503
# Request bodies held in memory at once. An agent turn or ASK keeps its body for up to its timeout,
# so this caps buffered bodies at MAX_INFLIGHT_POSTS x max_upload_bytes; JSON bodies are far smaller
# (MAX_JSON_BODY, MAX_MCP_BATCH) so what they expand into stays small too. ponytail: fixed; make it
# config if several glasses or agents share one service.
MAX_INFLIGHT_POSTS = 8
MAX_JSON_BODY = 64 * 1024  # MCP and notify bodies; only turns and answers carry audio
MAX_MCP_BATCH = 16  # MCP 2025-06-18 dropped batching; older clients send a few messages at most
# The wearer's answers get their own slots: an ASK holds a general slot while it waits for one.
MAX_INFLIGHT_ANSWERS = 2
# email.parser materializes every part, so an 8 MiB body of tiny parts costs ~15x its size in memory
# and seconds of CPU. Clients send at most four fields; refuse anything near this before parsing.
MAX_FORM_PARTS = 16
# email.parser also builds every part of a nested multipart/* or message/* part, which the outer count
# never sees. Clients send flat forms, so any part header declaring one is refused (\s spans folding).
_NESTED_PART = re.compile(rb"(?im)^content-type:\s*(multipart|message)/")
_SOCKET_TIMEOUT_SECONDS = 30  # an idle or stalled socket is dropped (socket reads only, not server waits)
_BODY_DEADLINE_SECONDS = 60  # a body that drips in slower than this is refused
_MCP_METHODS = frozenset({"initialize", "ping", "tools/list", "tools/call", "notifications/initialized"})
_BACKEND_KEYS = frozenset({"kind", "label", "url", "key_env", "key_file", "session"})

HUD_HINT = ("The user is talking to you through MemoMind smart glasses with a tiny text display. "
            "Reply in plain text, at most two short sentences, no markdown or lists unless asked. "
            "Spoken messages come from speech-to-text, so allow for transcription errors.")
# pi-web's bridge has no system-message channel, so the hint is prefixed to the user's text.
PI_HINT = "Sent from MemoMind smart glasses with a tiny display: reply in at most two short plain sentences."
PI_POLL_SECONDS = 0.7

# Glasses events. ponytail: in memory, last 50; a restart drops queued cards.
MAX_EVENTS = 50
MAX_WAIT_S = 25  # long-poll cap, well under proxy idle timeouts
CONTROL_TTL_S = 60  # control ops expire, so a command sent while the phone slept can't fire hours later
PRESENCE_S = 35  # the glasses app counts as open while it polled within this window
GLASSES_OPS = ("NOTIFY", "TP", "LS", "RC", "MR", "ASK", "STATUS")
MIC_OPS = ("LS", "RC", "MR")
MCP_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")


class ConfigError(ValueError):
    """A fail-closed, secret-free Anvil Agent configuration error."""


class BackendHTTPError(OSError):
    """An agent backend answered with a non-200 status; its body is never kept."""

    def __init__(self, code):
        super().__init__("backend answered HTTP %d" % code)
        self.code = code


@dataclass(frozen=True)
class Backend:
    kind: str
    label: str
    url: str
    key: str = field(repr=False)
    session: Optional[str] = None


@dataclass(frozen=True)
class AgentConfig:
    # router_url, router_key, route, segment_seconds, timeout_seconds and
    # max_recording_seconds are the attributes scribe.service.transcribe_memo
    # reads; keep the names aligned (test_spoken_turn_transcribes_through_the_gateway).
    router_url: str
    router_key: str = field(repr=False)
    allowed_logins: frozenset = field(repr=False)
    backends: Mapping = field(default_factory=dict)
    route: Optional[str] = None
    listen_host: str = "127.0.0.1"
    listen_port: int = DEFAULT_PORT
    origin_pattern: re.Pattern = re.compile(DEFAULT_ORIGIN_PATTERN)
    segment_seconds: int = 110
    max_upload_bytes: int = 8 * 1024 * 1024
    timeout_seconds: float = 60.0
    # A full agent turn can run tools; the client waits a little longer than this.
    turn_timeout_seconds: float = 150.0
    # Spoken turns and answers are short; captions and memos go to Anvil Scribe.
    max_recording_seconds: int = 300
    max_concurrent_transcriptions: int = 2


def _int(table, key, default, low, high):
    value = table.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise ConfigError("agent.%s must be an integer from %d through %d" % (key, low, high))
    return value


def _str(table, key, default=None, prefix="agent"):
    value = table.get(key, default)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ConfigError("%s.%s must be a non-empty string" % (prefix, key))
    return value.strip() if value is not None else None


def _seconds(table, key, default):
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 600:
        raise ConfigError("agent.%s must be from 1 through 600" % key)
    return float(value)


def _http_base(value, name):
    parsed = urllib.parse.urlsplit(value or "")
    try:
        parsed.port  # raises on a non-numeric or out-of-range port
    except ValueError:
        raise ConfigError("%s has an invalid port" % name) from None
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.query or parsed.fragment:
        raise ConfigError("%s must be an http(s) base URL" % name)
    return value.rstrip("/")


def _secret(table, prefix, env_key, file_key, environ, base_dir):
    """Exactly one of an environment reference or a protected file; never a literal. A relative
    file sits beside the config, so the same file works on the host and in the container."""
    key_file = _str(table, file_key, prefix=prefix)
    if (key_file is None) == (table.get(env_key) is None):
        raise ConfigError("%s needs exactly one of %s or %s" % (prefix, env_key, file_key))
    if key_file is None:
        name = _str(table, env_key, prefix=prefix)
        value = resolve_env_value(name, env=environ)[0]
        if not value:
            raise ConfigError("%s.%s names %s, which is unset or empty" % (prefix, env_key, name))
        return value
    try:
        value = read_private_auth_file(os.path.join(base_dir, key_file), max_bytes=4096).decode("utf-8").strip()
    except (AuthFileError, UnicodeDecodeError):
        value = ""
    if not value:
        raise ConfigError("%s.%s must be a private, non-empty key file" % (prefix, file_key))
    return value


def _backends(table, environ, base_dir):
    raw = table.get("backends", {})
    if not isinstance(raw, dict) or not 1 <= len(raw) <= 8:
        raise ConfigError("agent.backends must declare from 1 through 8 backends")
    backends = {}
    for backend_id, spec in raw.items():  # declaration order is the client's picker order
        prefix = "agent.backends.%s" % backend_id
        if _SLUG.fullmatch(backend_id) is None or not isinstance(spec, dict):
            raise ConfigError("%s needs a lowercase id and a table" % prefix)
        unknown = sorted(set(spec) - _BACKEND_KEYS)
        if unknown:
            raise ConfigError("unknown %s keys: %s" % (prefix, ", ".join(unknown)))
        kind = spec.get("kind")
        if kind not in BACKEND_KINDS:
            raise ConfigError("%s.kind must be one of %s" % (prefix, ", ".join(BACKEND_KINDS)))
        session = _str(spec, "session", prefix=prefix)
        if kind == "pi-web" and session is None:
            raise ConfigError("%s.session must name an existing pi-web session" % prefix)
        backends[backend_id] = Backend(
            kind=kind,
            label=_str(spec, "label", backend_id, prefix=prefix),
            url=_http_base(_str(spec, "url", prefix=prefix), prefix + ".url"),
            key=_secret(spec, prefix, "key_env", "key_file", environ, base_dir),
            session=session or ("anvil-agent" if kind == "hermes-api" else None),
        )
    return backends


def default_config_path() -> str:
    return config_path(CONFIG_NAME)


def load_config(path: Optional[str] = None, *, env: Optional[Mapping[str, str]] = None) -> AgentConfig:
    """Load ``[agent]`` and resolve every credential reference, failing closed. Env references
    read the process environment, then the operator .env (as Scribe does); an explicit ``env``
    mapping is used alone."""
    environ = env
    path = path or default_config_path()
    try:
        with open(path, "rb") as handle:
            payload = handle.read(_MAX_CONFIG_BYTES + 1)
        if len(payload) > _MAX_CONFIG_BYTES:
            raise ConfigError("Anvil Agent config exceeds %d bytes: %s" % (_MAX_CONFIG_BYTES, path))
        data = tomllib.loads(payload.decode("utf-8"))
    except FileNotFoundError:
        raise ConfigError("Anvil Agent config not found: %s" % path) from None
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError("cannot parse Anvil Agent config %s: %s" % (path, exc)) from None
    table = data.get("agent")
    if not isinstance(table, dict):
        raise ConfigError("Anvil Agent config has no [agent] table: %s" % path)
    unknown = sorted(set(table) - _KEYS)
    if unknown:
        raise ConfigError("unknown agent keys: %s" % ", ".join(unknown))

    router_url = _http_base(_str(table, "router_url"), "agent.router_url")
    if router_url.endswith("/v1"):
        router_url = router_url[:-3]
    base_dir = os.path.dirname(os.path.abspath(path))
    router_key = _secret(table, "agent", "router_key_env", "router_key_file", environ, base_dir)
    logins_env = _str(table, "allowed_logins_env")
    if logins_env is None:
        raise ConfigError("agent.allowed_logins_env is required")
    logins_value = resolve_env_value(logins_env, env=environ)[0] or ""
    logins = frozenset(s.strip() for s in logins_value.split(",") if s.strip())
    if not logins:
        raise ConfigError("agent.allowed_logins_env names %s, which resolves to no logins" % logins_env)

    listen_host = _str(table, "listen_host", "127.0.0.1")
    try:
        address = ipaddress.ip_address(listen_host)
        loopback = address.version == 4 and address.is_loopback  # the HTTP server is AF_INET
    except ValueError:
        loopback = False
    if not loopback:
        # The identity header is only trustworthy when tailscale serve is the sole path in.
        raise ConfigError("agent.listen_host must be an IPv4 loopback address")
    try:
        origin_pattern = re.compile(_str(table, "allowed_origin_pattern", DEFAULT_ORIGIN_PATTERN))
    except re.error as exc:
        raise ConfigError("agent.allowed_origin_pattern is not a valid regex") from exc

    return AgentConfig(
        router_url=router_url,
        router_key=router_key,
        allowed_logins=logins,
        backends=_backends(table, environ, base_dir),
        route=_str(table, "route"),
        listen_host=listen_host,
        listen_port=_int(table, "listen_port", DEFAULT_PORT, 1, 65535),
        origin_pattern=origin_pattern,
        segment_seconds=_int(table, "segment_seconds", 110, 1, MAX_SEGMENT_SECONDS),
        max_upload_bytes=_int(table, "max_upload_bytes", 8 * 1024 * 1024, 1024, 32 * 1024 * 1024),
        timeout_seconds=_seconds(table, "timeout_seconds", 60),
        turn_timeout_seconds=_seconds(table, "turn_timeout_seconds", 150),
        max_recording_seconds=_int(table, "max_recording_seconds", 300, 1, 3600),
        max_concurrent_transcriptions=_int(table, "max_concurrent_transcriptions", 2, 1, 16),
    )


# ---- Agent backends: each returns (reply, session_id, finish_status) ----

def _request_json(url, payload=None, headers=None, timeout=30.0):
    """GET (payload None) or POST JSON without proxy discovery or redirects, so a backend
    credential reaches only the configured URL. Returns (decoded body, response headers)."""
    parts = urllib.parse.urlsplit(url)
    connection_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    # ponytail: per-socket-operation timeout, not a wall-clock deadline.
    connection = connection_cls(parts.hostname, parts.port, timeout=timeout)
    path = parts.path + ("?" + parts.query if parts.query else "")
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    try:
        connection.request("GET" if body is None else "POST", path, body=body,
                           headers={"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if response.status != 200:
            raise BackendHTTPError(response.status)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ValueError("backend response too large")
        return json.loads(raw.decode("utf-8")), response.headers
    finally:
        connection.close()


def ask_hermes(backend: Backend, text: str, timeout: float):
    """One turn in the Hermes API server session; Hermes keeps the history in its own state."""
    # No "model" field: the API server then uses the profile's own model instead of a request override.
    data, headers = _request_json(
        backend.url + "/v1/chat/completions",
        {"stream": False, "messages": [{"role": "system", "content": HUD_HINT}, {"role": "user", "content": text}]},
        {"Authorization": "Bearer " + backend.key, "X-Hermes-Session-Id": backend.session}, timeout)
    choice = data["choices"][0]
    session_id = headers.get("X-Hermes-Session-Id") or backend.session
    return (choice["message"].get("content") or "").strip(), session_id, choice.get("finish_reason")


def _assistant_text(message):
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    return "".join(p.get("text", "") for p in content or []
                   if isinstance(p, dict) and p.get("type") == "text").strip()


def ask_pi(backend: Backend, text: str, timeout: float, poll_seconds: float = PI_POLL_SECONDS):
    """One Pi turn through pi-web's mobile bridge: prompt the session, wait until it stops
    streaming, then read its newest message, which must be the assistant's answer."""
    base = "%s/api/mobile-pi/sessions/%s" % (backend.url, urllib.parse.quote(backend.session, safe=""))
    auth = {"Authorization": "Bearer " + backend.key}

    def state():
        return _request_json(base + "/state", headers=auth, timeout=10)[0].get("state") or {}

    before = state().get("messageCount") or 0
    _request_json(base + "/command", {"type": "prompt", "message": "[%s]\n\n%s" % (PI_HINT, text),
                                      "requestId": str(uuid.uuid4())}, auth, timeout=30)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(poll_seconds)
        current = state()
        if current.get("isStreaming") or (current.get("messageCount") or 0) <= before:
            continue
        # No query: the bridge accepts only tail=50 (anything else is HTTP 400) and applies it anyway.
        newest = _request_json(base + "/context", headers=auth, timeout=10)[0]["context"]["messages"][-1]
        if newest.get("role") != "assistant" or newest.get("stopReason") == "toolUse":
            continue  # the prompt, a tool call or its result; the answer is still coming
        if newest.get("stopReason") in ("error", "aborted"):
            raise OSError("Pi turn ended with %s" % newest["stopReason"])
        return _assistant_text(newest), backend.session, newest.get("stopReason") or "stop"
    raise TimeoutError("Pi did not finish in time")


_ASK = {"hermes-api": ask_hermes, "pi-web": ask_pi}


def ask_backend(config: AgentConfig, agent_id: str, text: str):
    backend = config.backends[agent_id]
    return _ASK[backend.kind](backend, text, config.turn_timeout_seconds)


def is_timeout(error) -> bool:
    return isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError)


# ---- Glasses ops (pure validation) ----

def parse_glasses_op(args) -> dict:
    """Validate memomind_glasses tool arguments into an event."""
    if not isinstance(args, dict):
        raise ValueError("arguments must be an object")
    op = str(args.get("op") or "").upper()
    if op not in GLASSES_OPS:
        raise ValueError("op must be one of %s" % ", ".join(GLASSES_OPS))
    title, content, v = args.get("title") or "", args.get("content") or "", args.get("v", 1)
    if not isinstance(title, str) or len(title) > 120:
        raise ValueError("title must be at most 120 characters")
    if not isinstance(content, str) or len(content) > MAX_TEXT_CHARS:
        raise ValueError("content must be at most %d characters" % MAX_TEXT_CHARS)
    if v not in (0, 1):
        raise ValueError("v must be 0 (off/stop) or 1 (on/start)")
    event = {"op": op, "title": title.strip()}
    content = content.strip()
    if op == "NOTIFY":
        style = args.get("style") or "notify"
        if style not in ("notify", "task"):
            raise ValueError("style must be notify or task")
        if not content or len(content) > 2000:
            raise ValueError("NOTIFY needs content (1-2000 characters)")
        event.update(style=style, content=content)
    elif op == "TP":
        if v == 1 and not content:
            raise ValueError("TP with v=1 needs content")
        event.update(v=int(v), content=content)
    elif op in MIC_OPS:
        mode = args.get("mode") or ("minutes" if op == "MR" else "")
        if not isinstance(mode, str) or not re.fullmatch(r"[a-z0-9_-]{0,32}", mode):
            raise ValueError("mode must be a short lowercase id")
        event.update(v=int(v), mode=mode)
    elif op == "ASK":
        if not content or len(content) > 300:
            raise ValueError("ASK needs a question in content (1-300 characters)")
        event["content"] = content
    return event


def parse_notify(body: bytes) -> dict:
    """The v0.1 notify body ``{body, title?, kind: notify|task}`` as a NOTIFY event."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("expected a JSON object") from error
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return parse_glasses_op({"op": "NOTIFY", "content": data.get("body"), "title": data.get("title"),
                             "style": data.get("kind") or "notify"})


# ---- Glasses session state (what the client long-polls) ----

class Glasses:
    """The event ring, client presence, and questions waiting for the wearer."""

    def __init__(self):
        self.events = collections.deque(maxlen=MAX_EVENTS)
        self.changed = threading.Condition()  # guards events, polls and asks
        self.polls = {"active": 0, "last": float("-inf")}
        self.asks = {}  # askId -> {"done": threading.Event, "answer": dict | None}

    def add_event(self, fields, ttl_s=None):
        """Queue an event. NOTIFY cards persist (last 50); control ops expire after ttl_s."""
        with self.changed:
            newest = self.events[-1]["id"] if self.events else 0
            # Millisecond ids stay monotonic across restarts, so a saved client cursor never skips events.
            event = {"id": max(int(time.time() * 1000), newest + 1), **fields,
                     "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "_expires": None if fields["op"] == "NOTIFY" else time.time() + (ttl_s or CONTROL_TTL_S)}
            self.events.append(event)
            self.changed.notify_all()
        return event

    def events_after(self, after, wait_s):
        """Live events newer than ``after``; if none, block up to ``wait_s`` for one (long-poll).
        Each call also marks the glasses app as present."""
        deadline = time.monotonic() + wait_s
        with self.changed:
            self.polls["active"] += 1
            try:
                while True:
                    now = time.time()
                    fresh = [{k: v for k, v in e.items() if not k.startswith("_")} for e in self.events
                             if e["id"] > after and (e["_expires"] is None or e["_expires"] > now)]
                    remaining = deadline - time.monotonic()
                    if fresh or remaining <= 0:
                        return fresh
                    self.changed.wait(remaining)
            finally:
                self.polls["active"] -= 1
                self.polls["last"] = time.monotonic()

    def is_open(self):
        with self.changed:
            return self.polls["active"] > 0 or time.monotonic() - self.polls["last"] < PRESENCE_S

    def ask_wearer(self, event, timeout_s):
        """Queue an ASK card and wait for the wearer's answer (POST /v1/glasses/answer)."""
        ask_id = secrets.token_hex(8)
        slot = {"done": threading.Event(), "answer": None}
        with self.changed:
            if self.asks:  # one question on the HUD at a time
                return {"status": "busy", "detail": "Another question is already waiting for the wearer."}
            self.asks[ask_id] = slot
        queued = None
        try:
            queued = self.add_event({**event, "askId": ask_id, "timeoutMs": int(timeout_s * 1000)}, ttl_s=timeout_s)
            slot["done"].wait(timeout_s)
            with self.changed:  # from here settle() refuses, so no wearer is told a lost answer landed
                self.asks.pop(ask_id, None)
            if not slot["done"].is_set():
                return {"status": "timeout", "detail": "No answer from the wearer in time."}
            return slot["answer"]
        finally:
            with self.changed:  # a settled question must not reappear on another poll
                self.asks.pop(ask_id, None)
                if queued is not None:
                    queued["_expires"] = 0

    def pending(self, ask_id):
        with self.changed:
            return ask_id in self.asks

    def settle(self, ask_id, answer):
        with self.changed:
            slot = self.asks.get(ask_id)
            if slot is None or slot["done"].is_set():
                return False
            slot["answer"] = answer
            slot["done"].set()
            return True

    def run_tool(self, args):
        """Execute one memomind_glasses call. Returns (result text, is_error)."""
        try:
            event = parse_glasses_op(args)
        except ValueError as error:
            return str(error), True
        open_now = self.is_open()
        if event["op"] == "STATUS":
            return json.dumps({"glassesAppOpen": open_now}), False
        if event["op"] == "ASK":
            timeout_ms = args.get("timeoutMs") or 60_000
            if type(timeout_ms) is not int or not 5_000 <= timeout_ms <= 120_000:
                return "timeoutMs must be an integer from 5000 to 120000", True
            if not open_now:
                return json.dumps({"status": "not_open", "detail": "The glasses app is not open on the phone. "
                                   "Ask again later, or send a NOTIFY card instead."}), False
            return json.dumps(self.ask_wearer(event, timeout_ms / 1000)), False
        queued = self.add_event(event)
        result = {"status": "sent" if open_now else "queued", "id": queued["id"], "glassesAppOpen": open_now}
        if event["op"] in MIC_OPS and event["v"] == 1:
            result["detail"] = "The wearer has to press the glasses button to start; nothing records until then."
        if not open_now and event["op"] != "NOTIFY":
            result["detail"] = "The glasses app is not open; this %s expires in %d s." % (event["op"], CONTROL_TTL_S)
        return json.dumps(result), False


# ---- MCP (streamable HTTP, JSON responses only, no server-initiated stream) ----

MCP_TOOL = {
    "name": "memomind_glasses",
    "description": (
        "Drive the user's MemoMind One smart-glasses display through their own Anvil Agent service. Op codes "
        "mirror MemoMind's memomind_control, limited to what the glasses app can do: NOTIFY (card; style "
        "notify|task), TP (teleprompter: v=1 shows content as pages, v=0 closes), LS (live captions on/off), RC "
        "(record a voice memo on/off, optional Scribe profile as mode), MR (meeting minutes: RC with mode "
        "minutes), ASK (show a short question in content and wait up to timeoutMs for the wearer's spoken or "
        "typed answer), STATUS (is the glasses app open). LS, RC, MR and ASK only start after the wearer presses "
        "the glasses button. Commands reach the glasses only while the app is open on the phone; control ops "
        "expire after 60 s. Not available: volume, brightness, display position, system toggles, dashboards, "
        "media, calls."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": list(GLASSES_OPS)},
            "v": {"type": "integer", "enum": [0, 1], "description": "1 = on/start, 0 = off/stop (TP, LS, RC, MR)"},
            "title": {"type": "string", "maxLength": 120},
            "content": {"type": "string", "maxLength": MAX_TEXT_CHARS,
                        "description": "NOTIFY body, TP text, or ASK question"},
            "style": {"type": "string", "enum": ["notify", "task"]},
            "mode": {"type": "string", "description": "RC Scribe profile id, e.g. note, message, minutes, tasks"},
            "timeoutMs": {"type": "integer", "minimum": 5000, "maximum": 120000},
        },
        "required": ["op"],
    },
}


def mcp_response(glasses: Glasses, message):
    """One JSON-RPC message to a response object, or None for a notification."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
    method, msg_id = message["method"], message.get("id")
    if msg_id is None:
        return None  # notifications/initialized and friends need no answer
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": "invalid params"}}
    if method == "initialize":
        version = params.get("protocolVersion")
        result = {"protocolVersion": version if version in MCP_VERSIONS else MCP_VERSIONS[0],
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "memomind-glasses", "version": __version__}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [MCP_TOOL]}
    elif method == "tools/call" and params.get("name") == MCP_TOOL["name"]:
        text, is_error = glasses.run_tool(params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": text}], "isError": is_error}
    elif method == "tools/call":
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": "unknown tool"}}
    else:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


# ---- HTTP service ----

_STT_FAILURES = (TranscriptionError, ValueError, OSError, http.client.HTTPException, subprocess.TimeoutExpired)
_AGENT_FAILURES = (ValueError, OSError, http.client.HTTPException, KeyError, IndexError, TypeError, AttributeError)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _field(fields, name):
    return (fields.get(name) or (None, b""))[1].decode("utf-8", "replace").strip()


def _form_boundary(content_type):
    """The multipart boundary exactly as parse_multipart's email.parser reads it (decoy and RFC 2231
    parameters included), or None. Only an ASCII boundary is valid (RFC 2046)."""
    header = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + (content_type or "").encode("latin-1") + b"\r\n\r\n", headersonly=True)
    boundary = header.get_boundary() if header.get_content_type() == "multipart/form-data" else None
    return boundary if boundary and boundary.isascii() else None


def _length(headers) -> int:
    value = headers.get("Content-Length") or ""
    return int(value) if value.isascii() and value.isdigit() else 0


def make_server(config: AgentConfig, *,
                transcribe: Optional[Callable[[bytes], str]] = None,
                ask: Optional[Callable[[str, str], tuple]] = None) -> _BoundedServer:
    """Bind the listener; ``transcribe`` and ``ask`` are injectable for hermetic tests.
    The returned server carries its session state as ``server.glasses``."""
    glasses = Glasses()
    decode_slots = threading.BoundedSemaphore(config.max_concurrent_transcriptions)
    intake = threading.BoundedSemaphore(MAX_INFLIGHT_POSTS)
    answer_intake = threading.BoundedSemaphore(MAX_INFLIGHT_ANSWERS)
    transcriber = transcribe or (lambda ogg: transcribe_memo(config, ogg))
    asker = ask or (lambda agent_id, text: ask_backend(config, agent_id, text))
    agents_view = {"agents": [{"id": bid, "label": b.label} for bid, b in config.backends.items()]}

    def origin_allowed(origin):
        return bool(origin) and config.origin_pattern.fullmatch(origin) is not None

    class _Sent(Exception):
        """The response is already written; stop handling this request."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "anvil-agent/1"
        timeout = _SOCKET_TIMEOUT_SECONDS  # applied to every connection's socket

        def _read_body(self, length, started):
            """Exactly ``length`` bytes within one wall-clock deadline (as in Scribe), or None after
            replying, so a client that drips its body cannot hold a slot for long."""
            deadline = time.monotonic() + _BODY_DEADLINE_SECONDS
            chunks, received = [], 0
            while received < length:
                if time.monotonic() > deadline:
                    self.close_connection = True
                    self._send(408, {"error": "upload too slow"}, started)
                    return None
                chunk = self.rfile.read1(min(65536, length - received))
                if not chunk:
                    self.close_connection = True
                    self._send(400, {"error": "incomplete upload"}, started)
                    return None
                chunks.append(chunk)
                received += len(chunk)
            return b"".join(chunks)

        def log_message(self, fmt, *args):  # default request lines can carry query strings
            pass

        def _log(self, status, started, note=""):
            print(json.dumps({
                "ts": _now(), "method": self.command, "path": self.path.split("?", 1)[0], "status": status,
                "bytes_in": _length(self.headers),
                "ms": round((time.monotonic() - started) * 1000),
                "login_ok": self.headers.get(IDENTITY_HEADER) in config.allowed_logins,
                "origin_ok": origin_allowed(self.headers.get("Origin")), "note": note,
            }), file=sys.stderr, flush=True)

        def _send(self, status, payload, started, note="", headers=()):
            data = json.dumps(payload).encode()
            self.send_response(status)
            for name, value in headers:
                self.send_header(name, value)
            origin = self.headers.get("Origin")
            if origin_allowed(origin) and self.path.split("?", 1)[0] in CORS_PATHS:  # never on /mcp
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            self._log(status, started, note)

        def _refused(self, started):
            """Allowlisted tailnet login, and an allowed browser Origin when one is sent."""
            if self.headers.get(IDENTITY_HEADER) not in config.allowed_logins:
                self.close_connection = True
                self._send(403, {"error": "tailnet identity not allowed"}, started, "login refused")
                return True
            origin = self.headers.get("Origin")
            if origin and not origin_allowed(origin):
                self.close_connection = True
                self._send(403, {"error": "origin not allowed"}, started, "origin refused")
                return True
            return False

        def do_GET(self):
            started = time.monotonic()
            path, _, query = self.path.partition("?")
            if path == HEALTH_PATH:
                return self._send(200, {"ok": True}, started)
            if path == MCP_PATH:  # no server-initiated SSE stream; JSON responses only
                return self._send(405, {"error": "use POST"}, started, headers=(("Allow", "POST"),))
            if path not in (EVENTS_PATH, AGENTS_PATH):
                return self._send(404, {"error": "not found"}, started)
            if self._refused(started):
                return None
            if path == AGENTS_PATH:
                return self._send(200, agents_view, started)
            params = urllib.parse.parse_qs(query)
            try:
                after = int(params.get("after", ["0"])[0])
                wait_s = min(max(float(params.get("wait", ["0"])[0]), 0.0), MAX_WAIT_S)
            except ValueError:
                return self._send(400, {"error": "after and wait must be numbers"}, started)
            if wait_s != wait_s:  # NaN
                return self._send(400, {"error": "after and wait must be numbers"}, started)
            events = glasses.events_after(after, wait_s)
            return self._send(200, {"events": events}, started, "events=%d" % len(events))

        def do_OPTIONS(self):
            started = time.monotonic()
            if self.path.split("?", 1)[0] not in CORS_PATHS or not origin_allowed(self.headers.get("Origin")):
                return self._send(403, {"error": "origin not allowed"}, started, "preflight refused")
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")
            self.end_headers()
            self._log(204, started)

        def do_POST(self):
            started = time.monotonic()
            path = self.path.split("?", 1)[0]
            if path not in POST_PATHS:
                return self._send(404, {"error": "not found"}, started)
            if self._refused(started):
                return None
            origin = self.headers.get("Origin")
            content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            # A page on an allowed origin may send "simple" cross-origin POSTs that skip preflight.
            # MCP clients are agents, never browsers; notify from a browser must be JSON, which preflights.
            if origin and (path == MCP_PATH or (path == NOTIFY_PATH and content_type != "application/json")):
                self.close_connection = True
                return self._send(403, {"error": "origin not allowed"}, started, "origin refused")
            length = _length(self.headers)
            limit = MAX_JSON_BODY if path in (MCP_PATH, NOTIFY_PATH) else config.max_upload_bytes
            if not 0 < length <= limit:
                self.close_connection = True  # never read an unbounded or unannounced body
                return self._send(413, {"error": "upload missing or too large"}, started)
            slots = answer_intake if path == ANSWER_PATH else intake
            if not slots.acquire(blocking=False):  # bound buffered bodies, not just decodes
                self.close_connection = True  # the unread body stays on this socket
                return self._send(503, {"error": "busy"}, started, "intake busy")
            try:
                body = self._read_body(length, started)
                return None if body is None else self._handle_post(path, body, started)
            finally:
                slots.release()

        def _handle_post(self, path, body, started):
            try:
                if path == NOTIFY_PATH:
                    return self._notify(body, started)
                if path == MCP_PATH:
                    return self._mcp(body, started)
                content_type = self.headers.get("Content-Type")
                boundary = _form_boundary(content_type)
                if boundary is None:
                    raise ValueError("expected multipart/form-data with a boundary")
                # Over-counts (any occurrence, not just delimiter lines), so it can only refuse early.
                if body.count(b"--" + boundary.encode("ascii")) > MAX_FORM_PARTS + 1:
                    raise ValueError("too many form fields")
                if _NESTED_PART.search(body):
                    raise ValueError("nested form parts are not accepted")
                fields = parse_multipart(content_type, body)
                audio = fields.get("audio", (None, b""))[1]
                if path == ANSWER_PATH:
                    return self._answer(fields, audio, started)
                agent = "hermes" if path == HERMES_TURN_PATH else _field(fields, "agent")
                return self._turn(agent, fields, audio, started)
            except ValueError as error:
                return self._send(400, {"error": str(error)}, started)
            except _Sent:
                return None
            except Exception as error:  # noqa: BLE001 - never echo content into logs or replies
                self.close_connection = True
                return self._send(500, {"error": "internal error"}, started, type(error).__name__)

        def _text_or_speech(self, fields, audio, started):
            """Typed ``text``, else transcribed ``audio``; raises _Sent after replying with an error."""
            text = _field(fields, "text")
            if not text and not audio:
                raise ValueError("send a text or audio field")
            if len(text) > MAX_TEXT_CHARS:
                raise ValueError("text is longer than %d characters" % MAX_TEXT_CHARS)
            if text:
                return text
            if not decode_slots.acquire(timeout=_SLOT_WAIT_SECONDS):
                self._send(503, {"error": "busy"}, started, "decode busy")
                raise _Sent()
            try:
                text = transcriber(audio)
            except _STT_FAILURES as error:
                self._send(502, {"error": "transcription failed"}, started, type(error).__name__)
                raise _Sent() from None
            finally:
                decode_slots.release()
            if not text:
                self._send(422, {"error": "no speech recognized", "transcript": ""}, started, "empty")
                raise _Sent()
            return text

        def _turn(self, agent, fields, audio, started):
            """One turn with the chosen agent. Stores nothing; the agent keeps its own session."""
            ready = list(config.backends)
            agent = agent or (ready[0] if ready else "")
            if agent not in ready:
                raise ValueError("agent '%s' is not configured (available: %s)" % (agent, ", ".join(ready) or "none"))
            text = self._text_or_speech(fields, audio, started)
            label = config.backends[agent].label
            try:
                reply, session_id, finish = asker(agent, text)
            except BackendHTTPError as error:  # backend error bodies are never logged or forwarded
                return self._send(502, {"error": "%s answered HTTP %d" % (label, error.code), "transcript": text},
                                  started, "%s %d" % (agent, error.code))
            except _AGENT_FAILURES as error:
                if is_timeout(error):  # the agent may still finish in its own session
                    return self._send(504, {"error": "%s did not answer in time" % label, "unconfirmed": True,
                                            "transcript": text}, started, "%s timeout" % agent)
                return self._send(502, {"error": "%s is unavailable" % label, "transcript": text},
                                  started, "%s %s" % (agent, type(error).__name__))
            return self._send(200, {"agent": agent, "transcript": text, "reply": reply, "sessionId": session_id,
                                    "status": finish}, started, "%s turn finish=%s" % (agent, finish))

        def _answer(self, fields, audio, started):
            """The wearer's answer to a pending ASK: text, audio, or declined=1."""
            ask_id = _field(fields, "askId")
            if not ask_id:
                raise ValueError("missing askId")
            if not glasses.pending(ask_id):
                return self._send(404, {"error": "that question has expired"}, started, "ask expired")
            if _field(fields, "declined") == "1":
                answer, text = {"status": "declined"}, ""
            else:
                text = self._text_or_speech(fields, audio, started)
                answer = {"status": "answered", "answer": text}
            if not glasses.settle(ask_id, answer):
                return self._send(404, {"error": "that question has expired"}, started, "ask expired")
            return self._send(200, {"status": answer["status"], "transcript": text}, started,
                              "ask %s" % answer["status"])

        def _notify(self, body, started):
            try:
                event = parse_notify(body)
            except ValueError as error:
                return self._send(400, {"error": str(error)}, started)
            return self._send(200, {"id": glasses.add_event(event)["id"]}, started, event["style"])

        def _mcp(self, body, started):
            try:
                message = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._send(400, {"jsonrpc": "2.0", "id": None,
                                        "error": {"code": -32700, "message": "parse error"}}, started)
            if message == [] or (isinstance(message, list) and len(message) > MAX_MCP_BATCH):
                # JSON-RPC 2.0: an empty batch is a single Invalid Request; a long one is refused whole
                return self._send(200, {"jsonrpc": "2.0", "id": None,
                                        "error": {"code": -32600, "message": "invalid request"}}, started, "mcp batch refused")
            batch = message if isinstance(message, list) else [message]
            replies = [r for r in (mcp_response(glasses, m) for m in batch) if r is not None]
            if not replies:  # notifications only
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return self._log(202, started, "mcp notification")
            # Method names are client text: log known ones only, once each, plus the batch size.
            methods = dict.fromkeys(
                m["method"] if isinstance(m, dict) and isinstance(m.get("method"), str) and m["method"] in _MCP_METHODS
                else "other" for m in batch)
            return self._send(200, replies if isinstance(message, list) else replies[0], started,
                              "mcp %s n=%d" % (",".join(methods), len(batch)))

    server = _BoundedServer((config.listen_host, config.listen_port), Handler)  # Scribe's connection cap
    server.glasses = glasses
    return server


def config_summary(config: AgentConfig) -> dict:
    """Presence-only view for ``agent validate``: never keys, logins or session ids."""
    return {
        "ok": True, "listen": "%s:%d" % (config.listen_host, config.listen_port),
        "router_url": config.router_url, "stt_route": config.route, "logins": len(config.allowed_logins),
        "backends": [{"id": bid, "kind": b.kind, "label": b.label} for bid, b in config.backends.items()],
    }


__all__ = [
    "AgentConfig", "Backend", "BackendHTTPError", "ConfigError", "Glasses", "MCP_TOOL", "ask_backend",
    "ask_hermes", "ask_pi", "config_summary", "default_config_path", "load_config", "make_server",
    "mcp_response", "parse_glasses_op", "parse_notify",
]
