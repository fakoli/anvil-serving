"""TTS stage: OpenAI ``/v1/audio/speech`` via the out-of-process TTS serve
(anvil task T008, stage half; the serve-lifecycle half is
``anvil_serving/voice/serves/tts.py``).

POSTs each :class:`~anvil_serving.voice.messages.TTSInput` chunk's text to
``{base_url}/audio/speech`` via ``urllib`` with ``response_format="pcm"``
(raw signed-16-bit little-endian samples, no container) and ``stream=True``,
reading the response body in fixed-size chunks as they arrive so audio starts
playing before the whole utterance has finished synthesizing. Each chunk is
resampled from the TTS engine's native rate (``config.source_sample_rate`` --
e.g. Kokoro's 24kHz) to ``config.target_sample_rate`` (16kHz by default) via a
small pure-Python linear-interpolation resampler (:func:`resample_int16`) --
deliberately NOT ``audioop`` (removed in Python 3.13 per PEP 594) and NOT
numpy (the router hot path, and this extra, must gain zero new required
dependency).

Aborts on barge-in: ``cancel_scope.is_stale`` is checked once before the call
and once per streamed chunk, mirroring ``stages/llm.py``'s/``stages/stt.py``'s
barge-in handling -- a barge-in landing mid-synthesis stops emitting further
audio for that now-superseded turn.

HONESTY NOTE: :func:`resample_int16` is a plain linear-interpolation
resampler -- good enough to prove the wire-contract/shape math in hermetic
tests, NOT a mastering-grade resampler (no anti-aliasing filter) and NOT
validated against real synthesized audio or a live TTS serve. See CLAUDE.md's
"never claim a live capability is proven" rule.

KNOWN LIMITATION (quality-only, no wire-contract impact) -- CHUNK-BOUNDARY
DRIFT: :meth:`TTSStage.process` calls :func:`resample_int16` separately per
network-arrival chunk (each call is a fresh, STATELESS interpolation that
starts its output phase back at input-sample 0), not once over the whole
utterance -- deliberately, so audio starts playing before synthesis finishes
(see above). Each call's rounding of ``n_out = round(len(src) * ratio)`` and
its restart of the interpolation phase at every chunk boundary means the
concatenated resampled stream can drift by a fraction of a sample and briefly
re-start its interpolation phase at each boundary, versus resampling the same
bytes in one shot. This is inaudible-to-minor in practice (linear
interpolation already has no anti-aliasing filter, so this is not the
dominant source of quality loss) but is NOT sample-exact. A real fix needs a
STATEFUL resampler carrying the fractional phase (and the trailing
input sample, for interpolating across the boundary) across chunks for one
utterance -- follow-up work, not implemented here.

Stdlib-only: ``array``, ``base64``, ``json``, ``os``, ``socket``, ``ssl``,
``urllib.request``/``urllib.error``.
"""
from __future__ import annotations

import array
import base64
import http.client
import json
import os
import re
import socket
import ssl
import uuid
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, Mapping, Optional

from ..cancel_scope import CancelScope
from ..messages import AudioOut, EndOfResponse, LLMToolCall, TTSInput
from .base import BaseStage

DEFAULT_BASE_URL = "http://127.0.0.1:8091/v1"
DEFAULT_MODEL = "tts"
OPENAI_TTS_PROTOCOL = "openai"
CARTESIA_TTS_PROTOCOL = "cartesia"
_TTS_PROTOCOLS = {OPENAI_TTS_PROTOCOL, CARTESIA_TTS_PROTOCOL}
_PRE_AUDIO_STREAM_RETRY_ATTEMPTS = 1
_TTS_FALLBACK_SEPARATOR_RE = re.compile(r"[-/\\]+")


@dataclass
class TTSStageConfig:
    """Endpoint + request-shaping config for :class:`TTSStage`."""

    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    protocol: str = OPENAI_TTS_PROTOCOL
    api_key_env: Optional[str] = None
    timeout: float = 20.0
    response_format: str = "pcm"       # raw signed16-LE samples, no container
    source_sample_rate: int = 24000    # the TTS engine's native output rate
    target_sample_rate: int = 16000    # normalized rate emitted on AudioOut
    chunk_bytes: int = 4096            # incremental read granularity
    voice_id: Optional[str] = None      # Cartesia/Gepard cloned voice uuid, if used
    language: Optional[str] = None      # Cartesia-compatible optional language code


class TTSClientError(Exception):
    """Raised when the upstream speech call fails (transport error)."""


def build_speech_request_body(text: str, config: TTSStageConfig) -> Dict[str, Any]:
    """Build the ``/v1/audio/speech`` request body for one chunk of text."""
    return {
        "model": config.model,
        "input": text,
        "response_format": config.response_format,
        "stream": True,
    }


def build_cartesia_speech_request_body(
    text: str,
    config: TTSStageConfig,
    *,
    context_id: str,
    continue_: bool = True,
) -> Dict[str, Any]:
    """Build one Cartesia-compatible Gepard WebSocket synthesis message."""
    body: Dict[str, Any] = {
        "context_id": context_id,
        "model_id": config.model,
        "transcript": text,
        "continue": continue_,
        "output_format": {
            "container": "raw",
            "encoding": "pcm_s16le",
            "sample_rate": config.source_sample_rate,
        },
    }
    if config.voice_id:
        if config.voice_id == "default":
            body["voice"] = "default"
        else:
            body["voice"] = {"mode": "id", "id": config.voice_id}
    if config.language:
        body["language"] = config.language
    return body


def resample_int16(pcm: bytes, in_rate: int, out_rate: int) -> bytes:
    """Linear-interpolation resample of signed-16-bit little-endian mono PCM.

    Deliberately NOT ``audioop`` (removed in Python 3.13, PEP 594) and NOT
    numpy -- a small pure-Python resampler is plenty for this extra's
    dependency budget. No anti-aliasing filter: adequate for a first cut, not
    mastering-grade (see module honesty note). A no-op when the rates already
    match, or when there's fewer than 2 input samples to interpolate between.

    STATELESS by design: each call restarts the interpolation phase at input
    index 0 -- correct for a single one-shot buffer, but calling it once per
    streamed chunk (as :class:`TTSStage` does) means the concatenated output
    can drift/restart phase at chunk boundaries (see the module honesty
    note's "chunk-boundary drift" entry -- a documented follow-up, not fixed
    here).
    """
    if in_rate == out_rate or not pcm:
        return pcm
    src = array.array("h")
    src.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])  # drop a trailing odd byte, if any
    if len(src) < 2:
        return pcm if len(src) == 0 else src.tobytes()
    ratio = out_rate / in_rate
    n_out = max(1, int(round(len(src) * ratio)))
    out = array.array("h", bytes(2 * n_out))
    last_idx = len(src) - 1
    for i in range(n_out):
        src_pos = i / ratio
        idx = min(int(src_pos), last_idx)
        frac = src_pos - idx
        s0 = src[idx]
        s1 = src[idx + 1] if idx < last_idx else s0
        out[i] = int(s0 + (s1 - s0) * frac)
    return out.tobytes()


def _fallback_tts_text(text: str) -> Optional[str]:
    """Return a more conservative spoken form for a chunk that TTS rejects."""
    fallback = " ".join(_TTS_FALLBACK_SEPARATOR_RE.sub(" ", text).split())
    return fallback if fallback and fallback != text else None


#: Same DI shape as ``stages/llm.py``'s ``Transport``.
Transport = Callable[..., Any]


def _default_transport(url: str, *, data: bytes, headers: Mapping[str, str], timeout: float):
    req = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
    try:
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - configured serve URL only
    except urllib.error.URLError as exc:
        raise TTSClientError("TTS stage: request to %s failed: %s" % (url, exc)) from exc


def _bearer_headers(api_key_env: Optional[str]) -> Dict[str, str]:
    if not api_key_env:
        return {}
    token = (os.environ.get(api_key_env) or "").strip()
    return {"Authorization": "Bearer %s" % token} if token else {}


def _response_status(resp: Any) -> Optional[int]:
    """Best-effort HTTP status extraction across response shapes.

    Real ``urlopen()`` responses expose ``.status`` (3.9+); some also carry a
    ``.getcode()`` alias. Returns ``None`` when the response object exposes
    neither (e.g. a hermetic test fake with no status info at all) -- callers
    treat ``None`` as "unknown, assume success" so old fakes that never set a
    status keep behaving exactly as before this fix (F3).
    """
    status = getattr(resp, "status", None)
    if status is not None:
        return status
    status = getattr(resp, "code", None)
    if status is not None:
        return status
    getcode = getattr(resp, "getcode", None)
    if getcode is not None:
        try:
            return getcode()
        except Exception:  # noqa: BLE001 - fall through to "unknown"
            return None
    return None


def stream_speech(
    text: str, config: TTSStageConfig, *, transport: Optional[Transport] = None,
) -> Iterator[bytes]:
    """Yield raw PCM byte chunks (``config.source_sample_rate``, int16 mono)
    from a streaming ``/v1/audio/speech`` call, as they arrive.

    ``transport`` defaults to the real ``urllib``-backed client; pass a fake
    (returning an object with ``.read(n)``/``.close()``) for hermetic tests.

    F3 fix: checks the response status BEFORE reading/yielding any body
    bytes. A non-2xx status (e.g. a 429/500 with a JSON or HTML error body)
    raises :class:`TTSClientError` instead of silently emitting that error
    body as if it were PCM audio -- the happy (2xx, or status-unknown) path
    streams exactly as before.
    """
    if config.protocol == CARTESIA_TTS_PROTOCOL:
        if transport is not None:
            raise TTSClientError("TTS stage: transport injection is only supported for openai protocol")
        yield from stream_cartesia_speech(text, config)
        return
    if config.protocol != OPENAI_TTS_PROTOCOL:
        raise TTSClientError(
            "TTS stage: unsupported protocol %r (expected one of %s)"
            % (config.protocol, ", ".join(sorted(_TTS_PROTOCOLS)))
        )

    url = config.base_url.rstrip("/") + "/audio/speech"
    body = json.dumps(build_speech_request_body(text, config)).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/octet-stream"}
    headers.update(_bearer_headers(config.api_key_env))

    resp = (transport or _default_transport)(url, data=body, headers=headers, timeout=config.timeout)
    try:
        status = _response_status(resp)
        if status is not None and not (200 <= status < 300):
            detail = b""
            try:
                detail = resp.read()
            except Exception:  # noqa: BLE001 - best-effort; the status alone is enough to fail the turn
                pass
            raise TTSClientError(
                "TTS stage: %s returned HTTP %s (expected 2xx), refusing to treat the "
                "body as audio: %r" % (url, status, detail[:200])
            )
        while True:
            chunk = resp.read(config.chunk_bytes)
            if not chunk:
                break
            yield chunk
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass


def _cartesia_ws_target(base_url: str) -> tuple[str, str, int, str]:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in ("http", "https", "ws", "wss") or not parsed.hostname:
        raise TTSClientError("TTS stage: Cartesia base_url must be an http(s) URL")
    scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme, parsed.scheme)
    default_port = 443 if scheme == "wss" else 80
    port = parsed.port or default_port
    path = parsed.path.rstrip("/")
    if path in ("", "/"):
        path = "/tts/websocket"
    elif not path.endswith("/tts/websocket"):
        path = path + "/tts/websocket"
    return scheme, parsed.hostname, port, path


def _decode_cartesia_chunk(message: Mapping[str, Any]) -> bytes:
    data = message.get("data")
    if data is None or data == "":
        return b""
    if not isinstance(data, str):
        raise TTSClientError("TTS stage: Cartesia chunk data must be base64 text")
    try:
        return base64.b64decode(data, validate=True)
    except ValueError as exc:
        raise TTSClientError("TTS stage: Cartesia chunk data was not valid base64") from exc


def _cartesia_error_detail(message: Mapping[str, Any]) -> str:
    for key in ("error", "message", "detail"):
        value = message.get(key)
        if value:
            return str(value)
    return json.dumps(dict(message), sort_keys=True)


def stream_cartesia_speech(text: str, config: TTSStageConfig) -> Iterator[bytes]:
    """Yield raw PCM chunks from Gepard's Cartesia-compatible WebSocket API."""
    from ..realtime.ws import WebSocketError, client_handshake

    scheme, host, port, path = _cartesia_ws_target(config.base_url)
    context_id = "anvil-%s" % uuid.uuid4().hex
    raw_sock: Optional[socket.socket] = None
    conn = None
    try:
        raw_sock = socket.create_connection((host, port), timeout=config.timeout)
        raw_sock.settimeout(config.timeout)
        if scheme == "wss":
            raw_sock = ssl.create_default_context().wrap_socket(raw_sock, server_hostname=host)
            raw_sock.settimeout(config.timeout)
        conn = client_handshake(
            raw_sock,
            host=host,
            port=port,
            path=path,
            headers=_bearer_headers(config.api_key_env),
        )
        conn.send_json(build_cartesia_speech_request_body(text, config, context_id=context_id))
        conn.send_json({"context_id": context_id, "continue": False})
        while True:
            payload = conn.recv_text()
            if payload is None:
                raise TTSClientError("TTS stage: Cartesia websocket closed before done")
            try:
                message = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise TTSClientError("TTS stage: Cartesia websocket returned non-JSON text") from exc
            if not isinstance(message, dict):
                raise TTSClientError("TTS stage: Cartesia websocket message must be a JSON object")
            msg_type = message.get("type")
            if msg_type == "chunk":
                chunk = _decode_cartesia_chunk(message)
                if chunk:
                    yield chunk
                continue
            if msg_type == "done":
                return
            if msg_type == "error":
                raise TTSClientError("TTS stage: Cartesia websocket error: %s" % _cartesia_error_detail(message))
            raise TTSClientError("TTS stage: unexpected Cartesia websocket message type %r" % msg_type)
    except (OSError, WebSocketError) as exc:
        raise TTSClientError("TTS stage: Cartesia websocket request failed: %s" % exc) from exc
    finally:
        if conn is not None:
            conn.close()
        if raw_sock is not None:
            try:
                raw_sock.close()
            except OSError:
                pass


StreamFn = Callable[[str, TTSStageConfig], Iterator[bytes]]


class TTSStage(BaseStage):
    """Synthesizes each :class:`TTSInput` into one or more resampled
    :class:`AudioOut` chunks, streamed incrementally; forwards
    :class:`EndOfResponse` unchanged.

    ``process`` is a GENERATOR: each resampled :class:`AudioOut` chunk is
    yielded the instant it is ready, while :func:`stream_speech` is still
    blocked reading the REST of the utterance's audio off the wire.
    :class:`~anvil_serving.voice.stages.base.BaseStage` pulls one yielded item
    at a time and puts it on the downstream queue immediately -- so audio
    starts flowing to playback as soon as the FIRST chunk is synthesized, not
    after the whole utterance finishes. Do NOT collect chunks into a list and
    return it at the end; that would silently defeat the incremental
    contract this stage exists to provide.

    A stray odd trailing byte at a chunk boundary (PCM samples are 2 bytes
    each) is buffered and prepended to the next chunk rather than dropped, so
    16-bit sample alignment survives arbitrary read-chunk boundaries.
    """

    name = "tts"

    def __init__(
        self,
        in_queue,
        out_queues=None,
        *,
        cancel_scope: Optional[CancelScope] = None,
        config: Optional[TTSStageConfig] = None,
        stream_fn: Optional[StreamFn] = None,
    ) -> None:
        super().__init__(in_queue, out_queues)
        self.cancel_scope = cancel_scope or CancelScope()
        self.config = config or TTSStageConfig()
        self._stream_fn: StreamFn = stream_fn or stream_speech
        self._pre_audio_stream_retry_attempts = _PRE_AUDIO_STREAM_RETRY_ATTEMPTS

    def process(self, item: Any):
        if isinstance(item, (EndOfResponse, LLMToolCall)):
            yield item
            return
        if not isinstance(item, TTSInput):
            return  # empty generator: emits nothing
        if self.cancel_scope.is_stale(item.generation):
            return  # superseded by a barge-in before synthesis even started

        candidate_texts = [item.text]
        fallback = _fallback_tts_text(item.text)
        if fallback is not None:
            candidate_texts.append(fallback)

        last_error: Optional[BaseException] = None
        for text in candidate_texts:
            attempts = 0
            while True:
                emitted_audio = False
                leftover = b""
                try:
                    for chunk in self._stream_fn(text, self.config):
                        if self.cancel_scope.is_stale(item.generation):
                            return  # barge-in mid-synthesis: abort, drop further audio
                        data = leftover + chunk
                        if len(data) % 2:
                            leftover = data[-1:]
                            data = data[:-1]
                        else:
                            leftover = b""
                        if not data:
                            continue
                        resampled = resample_int16(
                            data, self.config.source_sample_rate, self.config.target_sample_rate
                        )
                        emitted_audio = True
                        yield AudioOut(
                            turn_id=item.turn_id,
                            turn_revision=item.turn_revision,
                            generation=item.generation,
                            pcm=resampled,
                            sample_rate=self.config.target_sample_rate,
                        )
                    return
                except (http.client.HTTPException, OSError) as exc:
                    if emitted_audio:
                        raise
                    last_error = exc
                    if attempts >= self._pre_audio_stream_retry_attempts:
                        break
                    attempts += 1
        if last_error is not None:
            raise last_error
