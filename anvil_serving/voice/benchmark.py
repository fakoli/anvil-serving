"""`anvil-serving voice benchmark` -- TTFA, end-to-end turn latency, an STT
WER-sample, and TTS RTF, as JSON (anvil task T015).

Replays ONE turn end-to-end through the SAME wire calls the real pipeline
stages make -- :func:`~anvil_serving.voice.stages.stt.transcribe_stream`,
:func:`~anvil_serving.voice.stages.llm.stream_chat_completion`,
:func:`~anvil_serving.voice.stages.tts.stream_speech` -- through the SAME
injectable ``transport``/``stream_fn`` seams those stages use, and reports
four numbers:

* ``ttfa_ms`` -- wall-clock time from the start of turn processing to the
  FIRST synthesized audio byte coming back from TTS (i.e. through STT text +
  the first LLM output, all the way to the first TTS chunk).
* ``turn_latency_ms`` -- wall-clock time from turn start through the LAST
  synthesized audio chunk.
* ``stt_wer`` -- word-error-rate of the STT hypothesis against a reference
  transcript for the one sample utterance (a WER *sample*, not a corpus
  average -- pass your own ``reference_text``/``pcm`` for a real one).
* ``tts_rtf`` -- real-time factor of the TTS synth: wall-clock synth time
  divided by the SECONDS OF AUDIO produced (< 1.0 is faster than real-time).

HONESTY NOTE: this module measures WHATEVER endpoints its transports point
at. The unit tests inject fake transports/stream_fns with canned (but
deterministic and timed) responses -- proving the MEASUREMENT MATH
(TTFA/latency/WER/RTF arithmetic) and the STT/LLM/TTS wire composition, NOT
real STT/TTS/LLM latency or audio quality. Point ``--config`` at a live voice
manifest and run this against real serves for numbers that mean anything;
nothing here is proven against real audio/GPU hardware. The default sample
audio (:func:`synth_sample_pcm`) is a synthetically generated tone, never
recorded human speech.

Stdlib-only: ``array``, ``json``, ``math``, ``time``.
"""
from __future__ import annotations

import array
import json
import math
import time
from typing import Any, Callable, Dict, Iterator, Mapping, Optional

from .stages.llm import LLMStageConfig, stream_chat_completion
from .stages.stt import STTStageConfig, transcribe_stream
from .stages.tts import TTSStageConfig, stream_speech

DEFAULT_REFERENCE_TEXT = "the quick brown fox jumps over the lazy dog"

StreamFn = Callable[..., Iterator[Any]]


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Word-level Levenshtein edit distance, normalized by reference length.

    Standard WER definition: ``(substitutions + insertions + deletions) /
    len(reference_words)``. Returns ``0.0`` for two empty strings; ``1.0`` if
    the reference is empty but the hypothesis isn't (every hypothesis word is
    a pure insertion, capped at 1.0 for readability).
    """
    ref = reference.split()
    hyp = hypothesis.split()
    if not ref:
        return 0.0 if not hyp else 1.0
    # Classic edit-distance DP table (rows=reference, cols=hypothesis).
    n, m = len(ref), len(hyp)
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        curr = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,        # deletion
                curr[j - 1] + 1,    # insertion
                prev[j - 1] + cost,  # substitution / match
            )
        prev = curr
    return min(1.0, prev[m] / n)


def synth_sample_pcm(*, duration_s: float = 1.0, sample_rate: int = 16000, freq_hz: float = 220.0) -> bytes:
    """A short, deterministic synthetic tone (NOT recorded speech) good
    enough to exercise the STT/TTS wire round-trip in a benchmark run when no
    real sample audio is supplied."""
    n = max(1, int(duration_s * sample_rate))
    amplitude = 8000
    samples = array.array("h", (
        int(amplitude * math.sin(2 * math.pi * freq_hz * (i / sample_rate)))
        for i in range(n)
    ))
    return samples.tobytes()


def run_benchmark(
    *,
    stt_config: STTStageConfig,
    llm_config: LLMStageConfig,
    tts_config: TTSStageConfig,
    pcm: bytes,
    sample_rate: int,
    reference_text: Optional[str] = None,
    stt_transport: Optional[Callable[..., Any]] = None,
    llm_transport: Optional[Callable[..., Any]] = None,
    tts_transport: Optional[Callable[..., Any]] = None,
    stt_stream_fn: Optional[StreamFn] = None,
    llm_stream_fn: Optional[StreamFn] = None,
    tts_stream_fn: Optional[StreamFn] = None,
    clock: Callable[[], float] = time.perf_counter,
) -> Dict[str, Any]:
    """Replay one turn through STT -> LLM -> TTS, returning the four metrics
    (plus the intermediate hypothesis/reply text, useful for debugging a run).

    Every stage call is injectable (``*_transport``/``*_stream_fn``) so tests
    never open a real socket; production callers (e.g. :func:`main`) leave
    them ``None`` to use the real ``urllib``-backed clients.
    """
    stt_fn = stt_stream_fn or (
        lambda p, sr, cfg: transcribe_stream(p, sr, cfg, transport=stt_transport)
    )
    llm_fn = llm_stream_fn or (
        lambda t, cfg: stream_chat_completion(t, cfg, transport=llm_transport)
    )
    tts_fn = tts_stream_fn or (
        lambda t, cfg: stream_speech(t, cfg, transport=tts_transport)
    )

    t0 = clock()

    hypothesis = ""
    for text, is_final in stt_fn(pcm, sample_rate, stt_config):
        hypothesis = text
        if is_final:
            break

    reply_text = ""
    for delta in llm_fn(hypothesis, llm_config):
        reply_text += delta

    t_tts_start = clock()
    first_audio_time: Optional[float] = None
    total_audio_bytes = 0
    for chunk in tts_fn(reply_text, tts_config):
        if first_audio_time is None:
            first_audio_time = clock()
        total_audio_bytes += len(chunk)
    t_end = clock()

    ttfa_ms = ((first_audio_time if first_audio_time is not None else t_end) - t0) * 1000.0
    turn_latency_ms = (t_end - t0) * 1000.0

    synth_seconds = max(0.0, t_end - t_tts_start)
    audio_seconds = (total_audio_bytes / 2) / tts_config.source_sample_rate if total_audio_bytes else 0.0
    tts_rtf = (synth_seconds / audio_seconds) if audio_seconds > 0 else None

    reference = DEFAULT_REFERENCE_TEXT if reference_text is None else reference_text
    stt_wer = word_error_rate(reference, hypothesis) if reference else None

    return {
        "ttfa_ms": round(ttfa_ms, 2),
        "turn_latency_ms": round(turn_latency_ms, 2),
        "stt_wer": round(stt_wer, 4) if stt_wer is not None else None,
        "tts_rtf": round(tts_rtf, 4) if tts_rtf is not None else None,
        "stt_hypothesis": hypothesis,
        "llm_reply": reply_text,
        "reference_text": reference,
    }


def _stage_config_from_table(table: Mapping[str, Any], cls) -> Any:
    kwargs: Dict[str, Any] = {
        "base_url": table.get("base_url", ""),
        "model": table.get("model", ""),
    }
    if table.get("api_key_env"):
        kwargs["api_key_env"] = table["api_key_env"]
    return cls(**kwargs)


def run_benchmark_from_manifest(
    data: Mapping[str, Any],
    *,
    pcm: Optional[bytes] = None,
    sample_rate: int = 16000,
    reference_text: Optional[str] = None,
    stt_transport: Optional[Callable[..., Any]] = None,
    llm_transport: Optional[Callable[..., Any]] = None,
    tts_transport: Optional[Callable[..., Any]] = None,
    stt_stream_fn: Optional[StreamFn] = None,
    llm_stream_fn: Optional[StreamFn] = None,
    tts_stream_fn: Optional[StreamFn] = None,
) -> Dict[str, Any]:
    """Build stage configs from a validated voice manifest (see
    ``anvil_serving/voice/config.py``) and run :func:`run_benchmark`.

    With every ``*_transport``/``*_stream_fn`` left ``None`` this makes LIVE
    network calls to whatever ``[voice.stt]``/``[voice.llm]``/``[voice.tts]``
    declare -- callers (e.g. the CLI) are expected to catch transport errors
    themselves when the serves aren't up yet. Tests pass fakes through these
    same params to stay hermetic.
    """
    voice = data.get("voice", {})
    stt_config = _stage_config_from_table(voice.get("stt", {}), STTStageConfig)
    llm_config = _stage_config_from_table(voice.get("llm", {}), LLMStageConfig)
    tts_config = _stage_config_from_table(voice.get("tts", {}), TTSStageConfig)
    sample = pcm if pcm is not None else synth_sample_pcm(sample_rate=sample_rate)
    return run_benchmark(
        stt_config=stt_config, llm_config=llm_config, tts_config=tts_config,
        pcm=sample, sample_rate=sample_rate, reference_text=reference_text,
        stt_transport=stt_transport, llm_transport=llm_transport, tts_transport=tts_transport,
        stt_stream_fn=stt_stream_fn, llm_stream_fn=llm_stream_fn, tts_stream_fn=tts_stream_fn,
    )


def to_json(result: Mapping[str, Any]) -> str:
    """Render a benchmark result as pretty-printed JSON (what the CLI prints)."""
    return json.dumps(dict(result), indent=2)
