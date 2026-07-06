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

Stdlib-only: ``array``, ``json``, ``os``, ``urllib.request``/``urllib.error``.
"""
from __future__ import annotations

import array
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, Mapping, Optional

from ..cancel_scope import CancelScope
from ..messages import AudioOut, EndOfResponse, TTSInput
from .base import BaseStage

DEFAULT_BASE_URL = "http://127.0.0.1:8091/v1"
DEFAULT_MODEL = "tts"


@dataclass
class TTSStageConfig:
    """Endpoint + request-shaping config for :class:`TTSStage`."""

    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key_env: Optional[str] = None
    timeout: float = 20.0
    response_format: str = "pcm"       # raw signed16-LE samples, no container
    source_sample_rate: int = 24000    # the TTS engine's native output rate
    target_sample_rate: int = 16000    # normalized rate emitted on AudioOut
    chunk_bytes: int = 4096            # incremental read granularity


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


#: Same DI shape as ``stages/llm.py``'s ``Transport``.
Transport = Callable[..., Any]


def _default_transport(url: str, *, data: bytes, headers: Mapping[str, str], timeout: float):
    req = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
    try:
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - configured serve URL only
    except urllib.error.URLError as exc:
        raise TTSClientError("TTS stage: request to %s failed: %s" % (url, exc)) from exc


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
    url = config.base_url.rstrip("/") + "/audio/speech"
    body = json.dumps(build_speech_request_body(text, config)).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/octet-stream"}
    if config.api_key_env:
        token = (os.environ.get(config.api_key_env) or "").strip()
        if token:
            headers["Authorization"] = "Bearer %s" % token

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

    def process(self, item: Any):
        if isinstance(item, EndOfResponse):
            yield item
            return
        if not isinstance(item, TTSInput):
            return  # empty generator: emits nothing
        if self.cancel_scope.is_stale(item.generation):
            return  # superseded by a barge-in before synthesis even started

        leftover = b""
        for chunk in self._stream_fn(item.text, self.config):
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
            resampled = resample_int16(data, self.config.source_sample_rate, self.config.target_sample_rate)
            yield AudioOut(
                turn_id=item.turn_id,
                turn_revision=item.turn_revision,
                generation=item.generation,
                pcm=resampled,
                sample_rate=self.config.target_sample_rate,
            )
