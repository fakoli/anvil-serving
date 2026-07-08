"""STT stage: OpenAI ``/v1/audio/transcriptions`` via the out-of-process STT
serve (anvil task T006, stage half; the serve-lifecycle half is
``anvil_serving/voice/serves/stt.py``).

POSTs a completed turn's raw PCM (wrapped in a WAV container so the receiving
server can read format/sample-rate off the header) to
``{base_url}/audio/transcriptions`` via ``urllib``, using the SAME
incremental SSE line-parser as the LLM stage
(``anvil_serving/router/backends/sse.py::iter_sse_events``) to assemble
OpenAI's streaming-transcription event vocabulary
(``transcript.text.delta``/``transcript.text.done``) into a running
hypothesis, emitting a non-final :class:`~anvil_serving.voice.messages.Transcription`
per delta and one final ``Transcription`` once the stream ends.

Stale-turn dropping mirrors the LLM stage
(``anvil_serving/voice/stages/llm.py``): ``cancel_scope.is_stale`` is checked
once before the call and once per streamed delta, so a barge-in landing
mid-transcription stops emitting further (now-superseded) partials.

HONESTY NOTE: the streaming-transcription event shapes above follow OpenAI's
published `/v1/audio/transcriptions` `stream=true` wire format but have NOT
been validated against a live STT serve -- see the module docstring in
``docs/findings/2026-07-04-hf-speech-to-speech-review.md`` and CLAUDE.md's
"never claim a live capability is proven" rule. Only the parsing/assembly
logic and the stage's cancel-scope integration are exercised by tests
(hermetic fakes, no network).

Stdlib-only: ``io``, ``json``, ``os``, ``uuid``, ``wave``,
``urllib.request``/``urllib.error``.
"""
from __future__ import annotations

import io
import json
import os
import re
import urllib.error
import urllib.request
import uuid
import wave
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, Mapping, Optional, Tuple

from ...router.backends.sse import iter_sse_events
from ..cancel_scope import CancelScope
from ..messages import Transcription, VADAudio
from .base import BaseStage

DEFAULT_BASE_URL = "http://127.0.0.1:8090/v1"
DEFAULT_MODEL = "stt"


@dataclass
class STTStageConfig:
    """Endpoint + request-shaping config for :class:`STTStage`."""

    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key_env: Optional[str] = None
    timeout: float = 10.0
    # OpenAI-style streaming partials (transcript.text.delta/.done). Disable
    # for a server that only supports the non-streaming JSON response shape.
    stream: bool = True
    # For non-streaming OpenAI-compatible servers that default to another
    # format, request the JSON shape this client consumes.
    response_format: Optional[str] = None
    # Optional provider-specific transcript cleanup. Qwen3-ASR through some
    # serving paths returns "language English<asr_text>..." instead of just the
    # transcript text.
    postprocess: Optional[str] = None
    # Additional OpenAI/vLLM-compatible transcription form fields. Useful for
    # provider-neutral tuning such as language, prompt, temperature, or
    # max_completion_tokens without adding a provider-specific code path.
    request_fields: Mapping[str, str | int | float | bool] = field(default_factory=dict)


class STTClientError(Exception):
    """Raised when the upstream transcription call fails (transport error)."""


def pcm_to_wav(pcm: bytes, *, sample_rate: int, channels: int = 1, sample_width: int = 2) -> bytes:
    """Wrap raw signed-16-bit PCM in a minimal WAV container.

    Lets an OpenAI-compatible server read format/sample-rate/channels off the
    header instead of requiring extra out-of-band params.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sample_width)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _multipart_encode(
    fields: Mapping[str, str], *, file_field: str, filename: str, content_type: str, file_bytes: bytes,
) -> Tuple[bytes, str]:
    """Build a ``multipart/form-data`` body; returns ``(body, boundary)``."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            ("--%s\r\n"
             "Content-Disposition: form-data; name=\"%s\"\r\n\r\n"
             "%s\r\n" % (boundary, name, value)).encode("utf-8")
        )
    parts.append(
        ("--%s\r\n"
         "Content-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
         "Content-Type: %s\r\n\r\n" % (boundary, file_field, filename, content_type)).encode("utf-8")
    )
    parts.append(file_bytes)
    parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
    return b"".join(parts), boundary


#: Same DI shape as ``stages/llm.py``'s ``Transport``: ``(url, *, data,
#: headers, timeout) -> file-like`` supporting ``for line in resp``/``.read()``
#: /``.close()`` -- lets tests inject a canned in-memory response instead of
#: opening a real socket.
Transport = Callable[..., Any]


def _default_transport(url: str, *, data: bytes, headers: Mapping[str, str], timeout: float):
    req = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
    try:
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - configured serve URL only
    except urllib.error.URLError as exc:
        raise STTClientError("STT stage: request to %s failed: %s" % (url, exc)) from exc


def build_transcription_fields(config: STTStageConfig) -> Dict[str, str]:
    fields: Dict[str, str] = {"model": config.model}
    if config.stream:
        fields["stream"] = "true"
    if config.response_format:
        fields["response_format"] = config.response_format
    for key, value in config.request_fields.items():
        if isinstance(value, bool):
            fields[key] = "true" if value else "false"
        else:
            fields[key] = str(value)
    return fields


def postprocess_transcript_text(text: str, postprocess: Optional[str]) -> str:
    if postprocess in (None, "", "none"):
        return text
    if postprocess != "qwen3_asr":
        return text
    cleaned = re.sub(r"^\s*language\s+[^<\s]+", "", text, flags=re.IGNORECASE)
    cleaned = cleaned.replace("<asr_text>", "").replace("</asr_text>", "")
    return cleaned.strip()


class STTStreamAssembler:
    """Assembles OpenAI-style streaming-transcription SSE events.

    ``transcript.text.delta`` events carry an INCREMENTAL text fragment
    (accumulated here into the running hypothesis, mirroring how
    ``OpenAIStreamAssembler`` accumulates chat deltas);
    ``transcript.text.done`` carries the full final text. ``feed`` returns
    ``(text_so_far, is_final)`` or ``None`` for an event it doesn't recognize.
    """

    def __init__(self) -> None:
        self.done = False
        self._text = ""

    def feed(self, event: Optional[str], data: str) -> Optional[Tuple[str, bool]]:
        try:
            obj = json.loads(data)
        except (ValueError, TypeError):
            return None
        if not isinstance(obj, Mapping):
            return None
        etype = obj.get("type") or event
        if etype == "transcript.text.delta":
            delta = obj.get("delta")
            if isinstance(delta, str) and delta:
                self._text += delta
                return self._text, False
            return None
        if etype == "transcript.text.done":
            text = obj.get("text")
            if isinstance(text, str):
                self._text = text
            self.done = True
            return self._text, True
        return None


def transcribe_stream(
    pcm: bytes, sample_rate: int, config: STTStageConfig, *, transport: Optional[Transport] = None,
) -> Iterator[Tuple[str, bool]]:
    """Yield ``(text_so_far, is_final)`` from a ``/v1/audio/transcriptions`` call.

    Streams partials when ``config.stream`` is set (the common case); a
    single final tuple otherwise. ``transport`` defaults to the real
    ``urllib``-backed client; pass a fake for hermetic tests.
    """
    url = config.base_url.rstrip("/") + "/audio/transcriptions"
    wav_bytes = pcm_to_wav(pcm, sample_rate=sample_rate)
    body, boundary = _multipart_encode(
        build_transcription_fields(config),
        file_field="file", filename="turn.wav", content_type="audio/wav", file_bytes=wav_bytes,
    )
    headers = {"Content-Type": "multipart/form-data; boundary=%s" % boundary}
    if config.stream:
        headers["Accept"] = "text/event-stream"
    if config.api_key_env:
        token = (os.environ.get(config.api_key_env) or "").strip()
        if token:
            headers["Authorization"] = "Bearer %s" % token

    resp = (transport or _default_transport)(url, data=body, headers=headers, timeout=config.timeout)
    try:
        if config.stream:
            assembler = STTStreamAssembler()
            for event, data in iter_sse_events(resp):
                result = assembler.feed(event, data)
                if result:
                    text, is_final = result
                    yield postprocess_transcript_text(text, config.postprocess), is_final
                if assembler.done:
                    break
        else:
            raw = resp.read()
            # F6 fix: a decode failure or an unexpected response shape used to
            # fall back to `obj = {}` -> `text = ""` -> `yield ("", True)`,
            # i.e. a MALFORMED response was indistinguishable from a real,
            # successful, legitimately-empty transcription -- the pipeline
            # would silently treat a broken STT serve as "the user said
            # nothing" instead of surfacing the failure. Raise instead; a
            # valid response with an empty (but present, string) "text" field
            # still succeeds as `("", True)` exactly as before.
            try:
                obj = json.loads(raw)
            except (ValueError, TypeError) as exc:
                raise STTClientError(
                    "STT stage: non-streaming response was not valid JSON: %s" % exc
                ) from exc
            if not isinstance(obj, Mapping) or not isinstance(obj.get("text"), str):
                raise STTClientError(
                    "STT stage: non-streaming response missing a string 'text' field: %r" % (obj,)
                )
            yield (postprocess_transcript_text(obj["text"], config.postprocess), True)
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass


StreamFn = Callable[[bytes, int, STTStageConfig], Iterator[Tuple[str, bool]]]


class STTStage(BaseStage):
    """Transcribes each final :class:`VADAudio` segment, emitting zero or more
    partial :class:`Transcription` items followed by one final one.

    Checks ``cancel_scope.is_stale`` on the incoming segment AND on every
    streamed partial: a barge-in landing mid-transcription stops emitting
    further (now-superseded) partials for that turn -- mirrors
    ``stages/llm.py``'s barge-in handling.
    """

    name = "stt"

    def __init__(
        self,
        in_queue,
        out_queues=None,
        *,
        cancel_scope: Optional[CancelScope] = None,
        config: Optional[STTStageConfig] = None,
        stream_fn: Optional[StreamFn] = None,
    ) -> None:
        super().__init__(in_queue, out_queues)
        self.cancel_scope = cancel_scope or CancelScope()
        self.config = config or STTStageConfig()
        self._stream_fn: StreamFn = stream_fn or transcribe_stream

    def process(self, item: Any):
        if not isinstance(item, VADAudio) or not item.is_final:
            return None
        if self.cancel_scope.is_stale(item.generation):
            return None  # superseded by a barge-in before transcription even started

        out = []
        for text, is_final in self._stream_fn(item.pcm, item.sample_rate, self.config):
            if self.cancel_scope.is_stale(item.generation):
                return out or None
            out.append(
                Transcription(
                    turn_id=item.turn_id,
                    turn_revision=item.turn_revision,
                    generation=item.generation,
                    text=text,
                    is_final=is_final,
                )
            )
        return out or None
