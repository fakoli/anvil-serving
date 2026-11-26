"""Anvil Scribe: record-first dictation over the Capability Gateway (ADR-0048).

Anvil Scribe is its own product family. It consumes Anvil Voice speech-to-text
and the gateway's chat aliases, and never talks to a model serve directly.
Record first, rewrite later. A browser or WebView client posts an Ogg Opus memo.
The service converts it to 16 kHz mono PCM16 WAV with ffmpeg, transcribes it in
gateway-sized segments through ``POST /v1/audio/transcriptions`` with a
route-bound device key, and stores the audio and raw transcript. Live preview
transcribes a trailing window and stores nothing.

Afterwards the client may rewrite a stored memo with a profile (a saved prompt)
on an allowlisted target (a gateway chat alias). Each result is appended to the
memo's ``outputs``; the raw transcript is never replaced. Built-in profiles are
read-only; custom profiles live in the memo store so every device sees them.
History lists, reads and deletes memos.

Trust model: the listener binds loopback only, and the ``Tailscale-User-Login``
header is trusted because ``tailscale serve`` is the only remote path to it.
Logs carry method, path, status, sizes, timing and allow decisions only; never
audio, transcripts, prompts, outputs or identity values. Memo stores are
evaluation-excluded.
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
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Mapping, Optional

from ..control_plane.mcp.auth_file import AuthFileError, read_private_auth_file
from ..paths import config_path
from ..voice.corpus import EVALUATION_EXCLUDED_MARKER

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - requires-python >= 3.11
    tomllib = None

CONFIG_NAME = "scribe.toml"
_MAX_CONFIG_BYTES = 256 * 1024


class ConfigError(ValueError):
    """A fail-closed, secret-free Scribe configuration error."""

MEMOS_PATH = "/v1/memos"
PREVIEW_PATH = "/v1/memos/preview"
PROFILES_PATH = "/v1/memos/profiles"
TARGETS_PATH = "/v1/memos/targets"
CHAT_PATH = "/v1/chat/completions"
HEALTH_PATH = "/healthz"
TRANSCRIPTIONS_PATH = "/v1/audio/transcriptions"
IDENTITY_HEADER = "Tailscale-User-Login"
# Default: a local Studio/browser origin such as http://127.0.0.1:51358.
DEFAULT_ORIGIN_PATTERN = r"http://127\.0\.0\.1:\d{2,5}"
SAMPLE_RATE = 16_000
BYTES_PER_SECOND = SAMPLE_RATE * 2
# 130 s of PCM16 plus a header stays under the gateway's default 4 MiB decoded-input limit.
MAX_SEGMENT_SECONDS = 130
MAX_CUSTOM_PROFILES = 64
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_JSON_BODY = 64 * 1024
_MEMO_ID = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{6}\Z")
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}\Z")
_PROFILES_FILE = "profiles.json"
_KEYS = frozenset({
    "router_url", "router_key_env", "router_key_file", "route", "allowed_logins_env", "allowed_origin_pattern",
    "data_dir", "listen_host", "listen_port", "segment_seconds", "max_upload_bytes",
    "timeout_seconds", "conversion_timeout_seconds", "targets",
})
_TARGET_KEYS = frozenset({"label", "detail", "model", "default"})
_MARKER_TEXT = (
    "Anvil Scribe memos: user content, not evidence.\n"
    "Never use this audio, these transcripts, outputs or saved prompts to train,\n"
    "fine-tune, or evaluate a model.\n"
    "anvil-serving STT corpus validation refuses audio under this directory (ADR-0048).\n"
)

# Verified against a small non-thinking instruct model: it invents names unless told
# not to, and follows an exact line prefix only when the prompt spells it out.
_RULES = ("Output only the rewritten text, with no preamble or commentary. Keep the speaker's meaning, "
          "facts, names and numbers exactly. Do not invent details. The input is a speech-to-text "
          "transcript, so fix obvious recognition errors from context.")
BUILTIN_PROFILES = (
    ("email", "Email", "A polished email in your voice.",
     "Rewrite the transcript as a clear, friendly professional email: a short subject line prefixed "
     "'Subject:', a greeting, a concise body in short paragraphs, and a sign-off. Never invent names: greet "
     "the recipient only if the speaker named one (otherwise use 'Hi,'), and end with 'Best,' and no name. "
     + _RULES),
    ("technical-doc", "Technical doc", "A structured technical write-up.",
     "Rewrite the transcript as a structured technical document in Markdown: a title, a short overview, then "
     "sections with headings, numbered steps where there is a procedure, and code identifiers in backticks "
     "exactly as said. " + _RULES),
    ("note", "Clean note", "Filler removed, punctuation fixed, meaning unchanged.",
     "Clean up the transcript into a readable note: remove filler words and false starts, fix punctuation "
     "and casing, and break it into paragraphs. Do not change wording beyond that. " + _RULES),
    ("message", "Message", "A short chat or Slack message.",
     "Rewrite the transcript as a short, casual chat message suitable for Slack or iMessage: one to three "
     "sentences. " + _RULES),
    ("minutes", "Meeting minutes", "Summary, decisions and action items.",
     "Turn the transcript into meeting minutes in Markdown with sections: Summary, Decisions, Action items "
     "(each as '- [ ] owner: task' when an owner is mentioned). " + _RULES),
    ("tasks", "Task list", "Checkbox tasks, one per line.",
     "Extract every actionable task from the transcript as a Markdown checklist. Start every line with "
     "exactly '- [ ] ' followed by the task, imperative and concise. " + _RULES),
    ("clarify", "Say it better", "A clearer, tighter version of what you meant.",
     "Rewrite the transcript to say the same thing more clearly and concisely, in the first person, keeping "
     "the tone. " + _RULES),
    ("summary", "Summary", "A two- or three-sentence TL;DR.",
     "Summarise the transcript in two or three sentences. " + _RULES),
)


class TranscriptionError(RuntimeError):
    """A content-free gateway transcription or rewrite failure."""


class _Refusal(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


@dataclass(frozen=True)
class Target:
    label: str
    model: str = field(repr=False)
    detail: str = ""
    default: bool = False


@dataclass(frozen=True)
class ScribeConfig:
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
    # The client waits about 90 s; a rewrite past this bound fails with 504.
    conversion_timeout_seconds: float = 45.0
    targets: Mapping = field(default_factory=dict)


def _int(table, key, default, low, high):
    value = table.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise ConfigError("scribe.%s must be an integer from %d through %d" % (key, low, high))
    return value


def _str(table, key, default=None):
    value = table.get(key, default)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ConfigError("scribe.%s must be a non-empty string" % key)
    return value.strip() if value is not None else None


def _seconds(table, key, default):
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 600:
        raise ConfigError("scribe.%s must be from 1 through 600" % key)
    return float(value)


def _targets(table):
    raw = table.get("targets", {})
    if not isinstance(raw, dict) or len(raw) > 16:
        raise ConfigError("scribe.targets must be a table of at most 16 targets")
    targets = {}
    for target_id, spec in raw.items():
        if (_SLUG.fullmatch(target_id) is None or not isinstance(spec, dict)
                or set(spec) - _TARGET_KEYS or not isinstance(spec.get("model"), str)
                or not spec["model"].strip() or not isinstance(spec.get("default", False), bool)):
            raise ConfigError("scribe.targets.%s needs a lowercase id and a model alias" % target_id)
        detail = spec.get("detail", "")
        if not isinstance(detail, str):
            raise ConfigError("scribe.targets.%s.detail must be a string" % target_id)
        targets[target_id] = Target(label=_str(spec, "label", target_id), model=spec["model"].strip(),
                                    detail=detail.strip(), default=spec.get("default", False))
    defaults = [t for t in targets.values() if t.default]
    if len(targets) > 1 and len(defaults) != 1:
        raise ConfigError("scribe.targets needs exactly one default = true")
    if len(targets) == 1 and not defaults:
        (only,) = targets
        targets[only] = Target(targets[only].label, targets[only].model, targets[only].detail, True)
    return targets


def default_config_path() -> str:
    return config_path(CONFIG_NAME)


def load_config(path: Optional[str] = None, *, env: Optional[Mapping[str, str]] = None) -> ScribeConfig:
    """Load ``[scribe]`` and resolve its environment references, failing closed."""
    environ = os.environ if env is None else env
    path = path or default_config_path()
    try:
        with open(path, "rb") as handle:
            payload = handle.read(_MAX_CONFIG_BYTES + 1)
        if len(payload) > _MAX_CONFIG_BYTES:
            raise ConfigError("Scribe config exceeds %d bytes: %s" % (_MAX_CONFIG_BYTES, path))
        data = tomllib.loads(payload.decode("utf-8"))
    except FileNotFoundError:
        raise ConfigError("Scribe config not found: %s" % path) from None
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError("cannot parse Scribe config %s: %s" % (path, exc)) from None
    table = data.get("scribe")
    if not isinstance(table, dict):
        raise ConfigError("Scribe config has no [scribe] table: %s" % path)
    unknown = sorted(set(table) - _KEYS)
    if unknown:
        raise ConfigError("unknown scribe keys: %s" % ", ".join(unknown))

    router_url = _str(table, "router_url")
    parsed = urllib.parse.urlsplit(router_url or "")
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.query or parsed.fragment:
        raise ConfigError("scribe.router_url must be an http(s) gateway base URL")
    router_url = router_url.rstrip("/")
    if router_url.endswith("/v1"):
        router_url = router_url[:-3]

    def secret_reference(key):
        name = _str(table, key)
        if name is None:
            raise ConfigError("scribe.%s is required" % key)
        value = (environ.get(name) or "").strip()
        if not value:
            raise ConfigError("scribe.%s names %s, which is unset or empty" % (key, name))
        return value

    key_file = _str(table, "router_key_file")
    if (key_file is None) == (table.get("router_key_env") is None):
        raise ConfigError("scribe needs exactly one of router_key_env or router_key_file")
    if key_file is None:
        router_key = secret_reference("router_key_env")
    else:
        try:  # the protected file written by `router keys create --out`
            router_key = read_private_auth_file(key_file, max_bytes=4096).decode("utf-8").strip()
        except (AuthFileError, UnicodeDecodeError):
            raise ConfigError("scribe.router_key_file must be a private, non-empty key file") from None
        if not router_key:
            raise ConfigError("scribe.router_key_file must be a private, non-empty key file")
    logins = frozenset(s.strip() for s in secret_reference("allowed_logins_env").split(",") if s.strip())
    if not logins:
        raise ConfigError("scribe.allowed_logins_env resolves to no logins")

    data_dir = _str(table, "data_dir")
    if data_dir is None or not os.path.isabs(os.path.expanduser(data_dir)):
        raise ConfigError("scribe.data_dir must be an absolute path")

    listen_host = _str(table, "listen_host", "127.0.0.1")
    try:
        loopback = ipaddress.ip_address(listen_host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        # The identity header is only trustworthy when tailscale serve is the sole path in.
        raise ConfigError("scribe.listen_host must be a loopback address")

    pattern = _str(table, "allowed_origin_pattern", DEFAULT_ORIGIN_PATTERN)
    try:
        origin_pattern = re.compile(pattern)
    except re.error as exc:
        raise ConfigError("scribe.allowed_origin_pattern is not a valid regex") from exc

    return ScribeConfig(
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
        timeout_seconds=_seconds(table, "timeout_seconds", 60),
        conversion_timeout_seconds=_seconds(table, "conversion_timeout_seconds", 45),
        targets=_targets(table),
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
    """POST without proxy discovery or redirects, so content reaches only the configured gateway."""
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


def _gateway_json(config, path, payload, timeout):
    status, raw = _post_json(config.router_url + path, payload, config.router_key, timeout)
    if status != 200:
        raise TranscriptionError("gateway returned HTTP %d" % status)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise TranscriptionError("gateway response too large")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise TranscriptionError("gateway returned invalid JSON") from None


def transcribe_segment(config: ScribeConfig, wav: bytes) -> str:
    """Transcribe one WAV segment through the gateway's normalized STT endpoint."""
    payload = {"purpose": "stt", "format": "wav", "is_final": True,
               "audio_b64": base64.b64encode(wav).decode("ascii")}
    if config.route:
        payload["route"] = config.route
    text = _gateway_json(config, TRANSCRIPTIONS_PATH, payload, config.timeout_seconds).get("text")
    if not isinstance(text, str):
        raise TranscriptionError("gateway returned an invalid transcript")
    return text.strip()


def transcribe_memo(config: ScribeConfig, ogg: bytes) -> str:
    segments = split_wav(ogg_to_wav(ogg), config.segment_seconds)
    return " ".join(t for t in (transcribe_segment(config, s) for s in segments) if t)


def rewrite_transcript(config: ScribeConfig, prompt: str, alias: str, transcript: str):
    """Rewrite a raw transcript through the gateway's chat path; returns ``(text, model)``."""
    reply = _gateway_json(config, CHAT_PATH, {"model": alias, "stream": False, "messages": [
        {"role": "system", "content": prompt}, {"role": "user", "content": transcript}]},
        config.conversion_timeout_seconds)
    try:
        text = reply["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise TranscriptionError("gateway returned an invalid completion") from None
    if not isinstance(text, str):
        raise TranscriptionError("gateway returned an invalid completion")
    model = reply.get("model") if isinstance(reply.get("model"), str) else alias
    # ponytail: drops inline reasoning a model may emit; the gateway normally separates it.
    return text.rsplit("</think>", 1)[-1].strip(), model


_FAILURES = (TranscriptionError, ValueError, OSError, http.client.HTTPException, subprocess.TimeoutExpired)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, value) -> None:
    """Replace one JSON record atomically; callers hold the store lock."""
    staged = path.with_name(path.name + ".tmp")
    staged.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(staged, path)


def prepare_store(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = data_dir / EVALUATION_EXCLUDED_MARKER
    if not marker.exists():
        marker.write_text(_MARKER_TEXT, encoding="utf-8")


class MemoStore:
    """Memo records, outputs and custom profiles in one evaluation-excluded directory."""

    # ponytail: one process-wide lock; single-owner stores never contend enough to need more.
    def __init__(self, data_dir: Path):
        self.root = data_dir
        self.lock = threading.Lock()
        prepare_store(data_dir)

    def _memo_path(self, memo_id):
        if not isinstance(memo_id, str) or _MEMO_ID.fullmatch(memo_id) is None:
            raise _Refusal(404, "memo not found")
        return self.root / (memo_id + ".json")

    @staticmethod
    def _complete(memo):
        memo.setdefault("outputs", [])
        return memo

    def get(self, memo_id):
        try:
            return self._complete(json.loads(self._memo_path(memo_id).read_text(encoding="utf-8")))
        except FileNotFoundError:
            raise _Refusal(404, "memo not found") from None
        except (UnicodeDecodeError, ValueError):
            raise _Refusal(500, "memo record is unreadable") from None

    def create(self, audio, duration_ms):
        memo_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
        (self.root / (memo_id + ".ogg")).write_bytes(audio)  # keep audio even if STT fails
        memo = {"id": memo_id, "createdAt": _now(), "durationMs": duration_ms, "transcript": None, "outputs": []}
        with self.lock:
            _write_json(self.root / (memo_id + ".json"), memo)
        return memo

    def update(self, memo_id, change):
        with self.lock:
            memo = self.get(memo_id)
            change(memo)
            _write_json(self._memo_path(memo_id), memo)
            return memo

    def delete(self, memo_id):
        path = self._memo_path(memo_id)
        with self.lock:
            if not path.exists():
                raise _Refusal(404, "memo not found")
            path.unlink()
            (self.root / (memo_id + ".ogg")).unlink(missing_ok=True)

    def page(self, limit, before):
        ids = sorted((p.stem for p in self.root.glob("*.json") if _MEMO_ID.fullmatch(p.stem)), reverse=True)
        if before:
            ids = [memo_id for memo_id in ids if memo_id < before]
        memos = []
        for memo_id in ids[:limit]:
            try:
                memos.append(self.get(memo_id))
            except _Refusal:  # deleted between listing and reading
                continue
        return memos, (ids[limit - 1] if len(ids) > limit else None)

    def custom_profiles(self):
        try:
            value = json.loads((self.root / _PROFILES_FILE).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        return value.get("profiles", []) if isinstance(value, dict) else []

    def save_custom_profiles(self, profiles):
        _write_json(self.root / _PROFILES_FILE, {"profiles": profiles})


def _builtin_profiles():
    return [{"id": pid, "label": label, "description": description, "builtin": True,
             "prompt": prompt, "target": None} for pid, label, description, prompt in BUILTIN_PROFILES]


def _profile_fields(body, targets, *, partial):
    """Validate the editable custom-profile fields from a JSON body."""
    if not isinstance(body, dict) or set(body) - {"label", "prompt", "description", "target"}:
        raise _Refusal(400, "profile fields are label, prompt, description and target")
    limits = {"label": 80, "prompt": 8000, "description": 280}
    fields = {}
    for name, limit in limits.items():
        if name in body:
            value = body[name]
            if not isinstance(value, str) or len(value) > limit or (name != "description" and not value.strip()):
                raise _Refusal(400, "%s must be text of at most %d characters" % (name, limit))
            fields[name] = value.strip()
        elif not partial and name != "description":
            raise _Refusal(400, "%s is required" % name)
    if "target" in body:
        if body["target"] is not None and body["target"] not in targets:
            raise _Refusal(400, "unknown target")
        fields["target"] = body["target"]
    return fields


def make_server(config: ScribeConfig, *,
                     transcribe: Optional[Callable[[bytes], str]] = None,
                     rewrite: Optional[Callable[[str, str, str], tuple]] = None) -> ThreadingHTTPServer:
    """Bind the memo listener; ``transcribe`` and ``rewrite`` are injectable for hermetic tests."""
    store = MemoStore(config.data_dir)
    transcriber = transcribe or (lambda ogg: transcribe_memo(config, ogg))
    rewriter = rewrite or (lambda prompt, alias, text: rewrite_transcript(config, prompt, alias, text))
    targets_view = {"targets": [{"id": tid, "label": t.label, "detail": t.detail, "default": t.default}
                                for tid, t in config.targets.items()]}

    def origin_allowed(origin):
        return bool(origin) and config.origin_pattern.fullmatch(origin) is not None

    def all_profiles():
        return _builtin_profiles() + [{**p, "builtin": False} for p in store.custom_profiles()]

    def find_profile(profile_id):
        for profile in all_profiles():
            if profile["id"] == profile_id:
                return profile
        return None

    def process(memo_id, profile_id, target_id=None):
        """Rewrite one stored memo; appends one output or raises without appending."""
        memo = store.get(memo_id)
        if memo.get("transcript") is None:
            raise _Refusal(409, "memo has no transcript")
        profile = find_profile(profile_id) if isinstance(profile_id, str) else None
        if profile is None:
            raise _Refusal(400, "unknown profile")
        if not config.targets:
            raise _Refusal(400, "no rewrite targets are configured")
        target_id = target_id or profile.get("target") or next(t for t, v in config.targets.items() if v.default)
        if target_id not in config.targets:
            raise _Refusal(400, "unknown target")
        target = config.targets[target_id]
        started = time.monotonic()
        if memo["transcript"]:
            try:
                text, model = rewriter(profile["prompt"], target.model, memo["transcript"])
            except TimeoutError:
                raise _Refusal(504, "rewrite timed out") from None
            except _FAILURES:
                raise _Refusal(502, "rewrite failed") from None
        else:
            text, model = "", target.model  # silence: nothing to rewrite
        output = {"id": "out-" + secrets.token_hex(4), "profile": profile["id"], "profileLabel": profile["label"],
                  "target": target_id, "model": model, "text": text, "createdAt": _now(),
                  "ms": round((time.monotonic() - started) * 1000)}
        store.update(memo_id, lambda m: m["outputs"].append(output))  # 404 if deleted meanwhile
        return output

    class Handler(BaseHTTPRequestHandler):
        server_version = "anvil-scribe/2"

        def log_message(self, fmt, *args):  # default request lines can carry query strings
            pass

        def _log(self, status, started, note=""):
            print(json.dumps({
                "ts": _now(), "method": self.command, "path": self.path.split("?", 1)[0], "status": status,
                "bytes_in": int(self.headers.get("Content-Length") or 0),
                "ms": round((time.monotonic() - started) * 1000),
                "login_ok": self.headers.get(IDENTITY_HEADER) in config.allowed_logins,
                "origin_ok": origin_allowed(self.headers.get("Origin")), "note": note,
            }), file=sys.stderr, flush=True)

        def _send(self, status, payload, started, note=""):
            data = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            origin = self.headers.get("Origin")
            if origin_allowed(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            if payload is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            self._log(status, started, note)

        def _route(self):
            path, _, query = self.path.partition("?")
            parts = path.rstrip("/").split("/")
            return path.rstrip("/") or "/", parts, urllib.parse.parse_qs(query)

        def _allowed(self, started):
            if self.headers.get(IDENTITY_HEADER) not in config.allowed_logins:
                self.close_connection = True
                self._send(403, {"error": "tailnet identity not allowed"}, started, "login refused")
                return False
            origin = self.headers.get("Origin")
            if origin and not origin_allowed(origin):
                self.close_connection = True
                self._send(403, {"error": "origin not allowed"}, started, "origin refused")
                return False
            return True

        def _json_body(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 < length <= _MAX_JSON_BODY:
                self.close_connection = True
                raise _Refusal(413 if length > _MAX_JSON_BODY else 400, "a JSON body is required")
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise _Refusal(400, "body must be JSON") from None

        def _dispatch(self, handler):
            started = time.monotonic()
            path, parts, query = self._route()
            if self.command == "GET" and path == HEALTH_PATH:
                return self._send(200, {"ok": True}, started)
            if path != MEMOS_PATH and not path.startswith(MEMOS_PATH + "/"):
                return self._send(404, {"error": "not found"}, started)
            if not self._allowed(started):
                return
            try:
                status, payload, note = handler(path, parts, query)
            except _Refusal as refusal:
                self.close_connection = True  # a refused request may leave an unread body
                status, payload, note = refusal.status, {"error": refusal.message}, ""
            except Exception as error:  # noqa: BLE001 - never echo content into logs or replies
                self.close_connection = True
                status, payload, note = 500, {"error": "internal error"}, type(error).__name__
            self._send(status, payload, started, note)

        def do_GET(self):
            def handle(path, parts, query):
                if path == MEMOS_PATH:
                    try:
                        limit = max(1, min(100, int((query.get("limit") or ["50"])[0])))
                    except ValueError:
                        raise _Refusal(400, "limit must be an integer") from None
                    before = (query.get("before") or [None])[0]
                    memos, cursor = store.page(limit, before)
                    return 200, {"memos": memos, "next": cursor}, ""
                if path == PROFILES_PATH:
                    return 200, {"profiles": all_profiles()}, ""
                if path == TARGETS_PATH:
                    return 200, targets_view, ""
                if len(parts) == 4:
                    return 200, store.get(parts[3]), ""
                raise _Refusal(404, "not found")
            self._dispatch(handle)

        def do_DELETE(self):
            def handle(path, parts, query):
                if len(parts) == 5 and path.startswith(PROFILES_PATH + "/"):
                    if find_profile(parts[4]) and find_profile(parts[4])["builtin"]:
                        raise _Refusal(403, "built-in profiles are read-only")
                    with store.lock:
                        custom = store.custom_profiles()
                        kept = [p for p in custom if p.get("id") != parts[4]]
                        if len(kept) == len(custom):
                            raise _Refusal(404, "profile not found")
                        store.save_custom_profiles(kept)
                    return 204, None, ""
                if len(parts) == 4 and path not in (PROFILES_PATH, TARGETS_PATH, PREVIEW_PATH):
                    store.delete(parts[3])
                    return 204, None, ""
                raise _Refusal(404, "not found")
            self._dispatch(handle)

        def do_PUT(self):
            def handle(path, parts, query):
                if not (len(parts) == 5 and path.startswith(PROFILES_PATH + "/")):
                    raise _Refusal(404, "not found")
                existing = find_profile(parts[4])
                if existing is None:
                    raise _Refusal(404, "profile not found")
                if existing["builtin"]:
                    raise _Refusal(403, "built-in profiles are read-only")
                fields = _profile_fields(self._json_body(), config.targets, partial=True)
                with store.lock:
                    custom = store.custom_profiles()
                    for profile in custom:
                        if profile.get("id") == parts[4]:
                            profile.update(fields)
                            store.save_custom_profiles(custom)
                            return 200, {"profile": {**profile, "builtin": False}}, ""
                raise _Refusal(404, "profile not found")
            self._dispatch(handle)

        def do_OPTIONS(self):
            started = time.monotonic()
            path = self.path.split("?", 1)[0].rstrip("/")
            if not (path == MEMOS_PATH or path.startswith(MEMOS_PATH + "/")) or not origin_allowed(
                    self.headers.get("Origin")):
                return self._send(403, {"error": "origin not allowed"}, started, "preflight refused")
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")
            self.end_headers()
            self._log(204, started)

        def do_POST(self):
            def handle(path, parts, query):
                if path == PROFILES_PATH:
                    fields = _profile_fields(self._json_body(), config.targets, partial=False)
                    profile = {"id": "custom-" + secrets.token_hex(3), "description": "", "target": None, **fields}
                    with store.lock:
                        custom = store.custom_profiles()
                        if len(custom) >= MAX_CUSTOM_PROFILES:
                            raise _Refusal(409, "custom profile limit reached")
                        store.save_custom_profiles(custom + [profile])
                    return 200, {"profile": {**profile, "builtin": False}}, ""
                if len(parts) == 5 and parts[4] == "process":
                    body = self._json_body()
                    if not isinstance(body, dict) or set(body) - {"profile", "target"}:
                        raise _Refusal(400, "process takes profile and an optional target")
                    return 200, {"output": process(parts[3], body.get("profile"), body.get("target"))}, ""
                if path not in (MEMOS_PATH, PREVIEW_PATH):
                    raise _Refusal(404, "not found")
                return self._capture(path)
            self._dispatch(handle)

        def _capture(self, path):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if not 0 < length <= config.max_upload_bytes:
                self.close_connection = True  # never read an unbounded or unannounced body
                raise _Refusal(413, "upload missing or too large")
            try:
                fields = parse_multipart(self.headers.get("Content-Type"), self.rfile.read(length))
                audio = fields.get("audio", (None, b""))[1]
                if not audio:
                    raise ValueError("missing audio field")
                duration_ms = int((fields.get("durationMs") or (None, b"0"))[1] or 0)
                mode = (fields.get("mode") or (None, b""))[1].decode("utf-8").strip() or None
            except ValueError as error:
                raise _Refusal(400, str(error)) from None
            if path == PREVIEW_PATH:  # live text while recording: transcribe, store nothing
                try:
                    return 200, {"transcript": transcriber(audio)}, "preview"
                except _FAILURES as error:
                    return 502, {"error": "transcription failed"}, type(error).__name__
            if mode is not None and find_profile(mode) is None:  # legacy upload-time profile
                raise _Refusal(400, "unknown mode")
            memo = store.create(audio, duration_ms)
            try:
                transcript = transcriber(audio)
            except _FAILURES as error:
                return 502, {"error": "transcription failed", "id": memo["id"]}, type(error).__name__
            memo = store.update(memo["id"], lambda m: m.update(transcript=transcript))
            if mode is not None:
                try:
                    process(memo["id"], mode)
                except _Refusal as refusal:  # the raw memo is saved either way
                    return 200, store.get(memo["id"]), "legacy mode %d" % refusal.status
                memo = store.get(memo["id"])
            return 200, memo, ""

    return ThreadingHTTPServer((config.listen_host, config.listen_port), Handler)
