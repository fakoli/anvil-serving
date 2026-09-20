"""`anvil-serving voice benchmark` -- TTFA, end-to-end turn latency, an STT
WER-sample, and TTS RTF, as JSON (anvil task T015).

Replays ONE turn end-to-end through the SAME wire calls the real pipeline
stages make -- :func:`~anvil_serving.voice.stages.stt.transcribe_stream`,
:func:`~anvil_serving.voice.stages.llm.stream_chat_completion`,
:func:`~anvil_serving.voice.stages.tts.stream_speech` -- through the SAME
injectable ``transport``/``stream_fn`` seams those stages use, and reports
four numbers:

* ``ttfa_ms`` -- wall-clock time from the start of turn processing to the
  first nonempty TTS chunk yielded by the configured stage. This is an
  observation after any stage-side buffering, not a first-response-byte or
  audible-playback measurement. It is ``null`` when TTS yields no audio.
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
import hashlib
import json
import math
import os
import re
import stat
import struct
import time
from dataclasses import dataclass, fields
from typing import Any, Callable, Dict, Iterator, Mapping, Optional

from .stages.llm import LLMStageConfig, LLMStreamToolCalls, SentenceBatcher, stream_chat_completion
from .stages.stt import STTStageConfig, transcribe_stream
from .stages.tts import TTSStageConfig, stream_speech

DEFAULT_REFERENCE_TEXT = "the quick brown fox jumps over the lazy dog"
EVIDENCE_SCHEMA_VERSION = "voice-benchmark-evidence/v1"
MAX_TTS_TEXT_CHARS = 48
MAX_INPUT_WAV_SECONDS = 30.0
# A 30-second 16-kHz mono PCM16 payload needs 960,000 bytes.  The small
# allowance permits ordinary RIFF metadata chunks while bounding the read.
MAX_INPUT_WAV_BYTES = 1_048_576
MEASUREMENT_SCOPE = {
    "kind": "serialized-stage-replay",
    "ttfa_clock_endpoint": "first-nonempty-yielded-TTS-chunk",
    "realtime": False,
    "acoustic_playback": False,
}
REFERENCE_MODEL_FREE_PROFILES = {"dark-audio", "mini-dark-audio-proxy"}
MINI_LOCAL_AUDIO_PROFILES = {"mini-audio", "mini-validation"}
MINI_LOCAL_AUDIO_PORTS = {30010, 30011}

StreamFn = Callable[..., Iterator[Any]]


class BenchmarkInputError(ValueError):
    """Raised when a benchmark input cannot be safely replayed."""


@dataclass(frozen=True)
class BenchmarkInput:
    """Validated PCM16 mono WAV content ready for one benchmark replay."""

    pcm: bytes
    sample_rate: int
    identity: Dict[str, Any]


def _input_identity(
    pcm: bytes,
    *,
    sample_rate: int,
    reference_text: str,
    input_kind: str,
    qualification: str,
    audio_format: str,
) -> Dict[str, Any]:
    return {
        "input_kind": input_kind,
        "qualification": qualification,
        "sample_sha256": hashlib.sha256(pcm).hexdigest(),
        "sample_bytes": len(pcm),
        "audio_format": audio_format,
        "duration_seconds": round(len(pcm) / (2 * sample_rate), 6),
        "reference_text_sha256": hashlib.sha256(reference_text.encode("utf-8")).hexdigest(),
    }


def load_benchmark_input_wav(path: str) -> BenchmarkInput:
    """Read one bounded PCM16/16-kHz/mono RIFF WAV without following its final link.

    The CLI calls this before contacting any configured endpoint. Parsing the
    RIFF chunks ourselves keeps the accepted contract small and makes the
    exact-on-disk byte count part of validation rather than delegating codec
    support to a platform audio library.
    """
    try:
        path_stat = os.lstat(path)
    except OSError as exc:
        raise BenchmarkInputError("input WAV cannot be inspected: %s" % path) from exc
    if stat.S_ISLNK(path_stat.st_mode):
        raise BenchmarkInputError("input WAV must not be a symbolic link: %s" % path)
    if not stat.S_ISREG(path_stat.st_mode):
        raise BenchmarkInputError("input WAV must be a regular file: %s" % path)
    if path_stat.st_size > MAX_INPUT_WAV_BYTES:
        raise BenchmarkInputError(
            "input WAV exceeds %d-byte limit: %s" % (MAX_INPUT_WAV_BYTES, path)
        )

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BenchmarkInputError("input WAV cannot be opened safely: %s" % path) from exc
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode) or not os.path.samestat(path_stat, opened_stat):
            raise BenchmarkInputError("input WAV changed while opening: %s" % path)
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            raw = handle.read(MAX_INPUT_WAV_BYTES + 1)
    finally:
        if descriptor != -1:
            os.close(descriptor)
    if len(raw) > MAX_INPUT_WAV_BYTES:
        raise BenchmarkInputError(
            "input WAV exceeds %d-byte limit: %s" % (MAX_INPUT_WAV_BYTES, path)
        )

    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise BenchmarkInputError("input WAV must be a RIFF/WAVE file")
    declared_size = struct.unpack_from("<I", raw, 4)[0]
    if declared_size != len(raw) - 8:
        raise BenchmarkInputError("input WAV RIFF size does not match file bytes")

    offset = 12
    format_chunk: Optional[bytes] = None
    data_chunk: Optional[bytes] = None
    while offset < len(raw):
        if len(raw) - offset < 8:
            raise BenchmarkInputError("input WAV has a truncated chunk header")
        chunk_id = raw[offset:offset + 4]
        chunk_size = struct.unpack_from("<I", raw, offset + 4)[0]
        chunk_start = offset + 8
        chunk_end = chunk_start + chunk_size
        padded_end = chunk_end + (chunk_size % 2)
        if chunk_end > len(raw) or padded_end > len(raw):
            raise BenchmarkInputError("input WAV has a truncated chunk")
        if chunk_id == b"fmt ":
            if format_chunk is not None:
                raise BenchmarkInputError("input WAV contains multiple format chunks")
            format_chunk = raw[chunk_start:chunk_end]
        elif chunk_id == b"data":
            if data_chunk is not None:
                raise BenchmarkInputError("input WAV contains multiple data chunks")
            data_chunk = raw[chunk_start:chunk_end]
        offset = padded_end
    if offset != len(raw) or format_chunk is None or data_chunk is None:
        raise BenchmarkInputError("input WAV is missing a complete format or data chunk")
    if len(format_chunk) != 16:
        raise BenchmarkInputError("input WAV must use the canonical 16-byte PCM format chunk")

    audio_format, channels, sample_rate, byte_rate, block_align, bits_per_sample = struct.unpack(
        "<HHIIHH", format_chunk
    )
    if audio_format != 1 or bits_per_sample != 16:
        raise BenchmarkInputError("input WAV must be uncompressed PCM16")
    if channels != 1:
        raise BenchmarkInputError("input WAV must be mono")
    if sample_rate != 16000:
        raise BenchmarkInputError("input WAV must use a 16000-Hz sample rate")
    if byte_rate != 32000 or block_align != 2 or len(data_chunk) % block_align:
        raise BenchmarkInputError("input WAV PCM16 header does not match its audio data")
    duration_seconds = len(data_chunk) / byte_rate
    if duration_seconds <= 0 or duration_seconds > MAX_INPUT_WAV_SECONDS:
        raise BenchmarkInputError(
            "input WAV duration must be greater than zero and at most %d seconds"
            % int(MAX_INPUT_WAV_SECONDS)
        )

    identity = _input_identity(
        data_chunk,
        sample_rate=sample_rate,
        reference_text="",
        input_kind="provided-wav",
        qualification="supplied-content-unverified",
        audio_format="wav-pcm-s16le-mono-16000hz",
    )
    identity["source_wav_sha256"] = hashlib.sha256(raw).hexdigest()
    identity["source_wav_bytes"] = len(raw)
    return BenchmarkInput(
        pcm=data_chunk,
        sample_rate=sample_rate,
        identity=identity,
    )


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Word-level Levenshtein edit distance, normalized by reference length.

    Standard WER definition: ``(substitutions + insertions + deletions) /
    len(reference_words)``. Returns ``0.0`` for two empty strings; ``1.0`` if
    the reference is empty but the hypothesis isn't (every hypothesis word is
    a pure insertion, capped at 1.0 for readability).
    """
    # ASR WER is lexical: capitalization and terminal punctuation do not turn
    # an otherwise identical spoken word into a substitution.
    ref = re.findall(r"\w+(?:['’]\w+)*", reference.casefold())
    hyp = re.findall(r"\w+(?:['’]\w+)*", hypothesis.casefold())
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


def _split_for_tts(text: str, *, max_chars: int = MAX_TTS_TEXT_CHARS) -> Iterator[str]:
    """Split one speakable LLM chunk into bounded TTS requests.

    The live pipeline sentence-batches LLM output before TTS. The benchmark is
    intentionally simpler and still waits for the full LLM reply before
    synthesizing, but it should not send an arbitrarily large reply as one TTS
    request. Keeping chunks modest avoids backend-specific long-text failure
    modes while preserving that the audio is synthesized from the LLM reply.
    """
    text = " ".join(text.split())
    if not text:
        return
    if len(text) <= max_chars:
        yield text
        return

    current = ""
    for word in text.split():
        if len(word) > max_chars:
            if current:
                yield current
                current = ""
            for i in range(0, len(word), max_chars):
                yield word[i:i + max_chars]
            continue
        candidate = ("%s %s" % (current, word)).strip()
        if current and len(candidate) > max_chars:
            yield current
            current = word
        else:
            current = candidate
    if current:
        yield current


def _coerce_tool_call(call: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": _json_scalar(call.get("id")),
        "name": _json_scalar(call.get("name")),
        "arguments": _json_scalar(call.get("arguments")),
    }


def _tool_calls_from_delta(delta: Any) -> list[Dict[str, Any]]:
    if isinstance(delta, LLMStreamToolCalls):
        return [_coerce_tool_call(call) for call in delta.tool_calls]
    if isinstance(delta, Mapping):
        calls = delta.get("tool_calls")
        if isinstance(calls, list):
            return [
                _coerce_tool_call(call)
                for call in calls
                if isinstance(call, Mapping)
            ]
    return []


def _tool_call_outcome(tool_calls: list[Dict[str, Any]]) -> Dict[str, Any]:
    if not tool_calls:
        return {
            "status": "not_run",
            "successful": None,
            "tool_call_count": 0,
            "calls": [],
        }
    return {
        "status": "observed",
        "successful": True,
        "tool_call_count": len(tool_calls),
        "calls": tool_calls,
    }


def _llm_reply_and_tts_chunks(
    llm_deltas: Iterator[Any], *, speech_chunk_max_chars: int = MAX_TTS_TEXT_CHARS,
) -> tuple[str, list[str], list[Dict[str, Any]]]:
    reply_text = ""
    tts_texts: list[str] = []
    tool_calls: list[Dict[str, Any]] = []
    batcher = SentenceBatcher(max_chars=speech_chunk_max_chars)
    for delta in llm_deltas:
        extracted_tool_calls = _tool_calls_from_delta(delta)
        if extracted_tool_calls:
            tool_calls.extend(extracted_tool_calls)
            continue
        if not isinstance(delta, str):
            continue
        reply_text += delta
        for sentence in batcher.feed(delta):
            tts_texts.extend(_split_for_tts(sentence, max_chars=speech_chunk_max_chars))
    tts_texts.extend(
        chunk
        for trailing in batcher.flush_chunks()
        for chunk in _split_for_tts(trailing, max_chars=speech_chunk_max_chars)
    )
    return reply_text, tts_texts, tool_calls


def _json_scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _url_port(base_url: str) -> Optional[int]:
    try:
        from urllib.parse import urlparse
    except ImportError:  # pragma: no cover - stdlib always has urllib.parse
        return None
    parsed = urlparse(base_url or "")
    return parsed.port


def _uses_mini_local_audio_endpoint(base_url: str) -> bool:
    return (base_url or "").startswith("http://127.0.0.1:") and _url_port(base_url) in MINI_LOCAL_AUDIO_PORTS


def _topology_evidence(
    *,
    profile: Optional[str],
    stt_config: STTStageConfig,
    llm_config: LLMStageConfig,
    tts_config: TTSStageConfig,
) -> Dict[str, Any]:
    mini_endpoint_stages = [
        stage for stage, base_url in (
            ("stt", stt_config.base_url),
            ("tts", tts_config.base_url),
        )
        if _uses_mini_local_audio_endpoint(base_url)
    ]
    reference_test = profile in REFERENCE_MODEL_FREE_PROFILES
    mini_local_profile = profile in MINI_LOCAL_AUDIO_PROFILES
    mini_hosts_models = mini_local_profile or bool(mini_endpoint_stages)
    if reference_test:
        assertion_passed: Optional[bool] = not mini_hosts_models
    else:
        assertion_passed = None
    return {
        "profile": profile,
        "mode": "reference-model-free" if reference_test else "non-reference-or-unspecified",
        "endpoints": {
            "stt_base_url": stt_config.base_url,
            "llm_base_url": llm_config.base_url,
            "tts_base_url": tts_config.base_url,
        },
        "mini_model_free_assertion": {
            "checked": True,
            "method": "profile_and_endpoint_config",
            "reference_test": reference_test,
            "passed": assertion_passed,
            "mini_hosts_models": mini_hosts_models,
            "mini_local_audio_profile": mini_local_profile,
            "mini_local_model_endpoint_stages": mini_endpoint_stages,
            "reference_model_free_profiles": sorted(REFERENCE_MODEL_FREE_PROFILES),
        },
    }


def _route_identity_from_manifest(data: Mapping[str, Any]) -> Dict[str, Any]:
    voice = data.get("voice", {}) if isinstance(data, Mapping) else {}
    llm = voice.get("llm", {}) if isinstance(voice, Mapping) else {}
    return {
        "endpoint_host": _json_scalar(llm.get("expected_endpoint_host")),
        "alias": _json_scalar(llm.get("model")),
    }


def _evidence_identity(
    *,
    stt_config: STTStageConfig,
    llm_config: LLMStageConfig,
    tts_config: TTSStageConfig,
    profile: Optional[str],
    candidate: Optional[str],
    route_identity: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    route = {
        "endpoint_host": None,
        "alias": None,
    }
    if route_identity:
        for key in route:
            route[key] = _json_scalar(route_identity.get(key))
    return {
        "profile": profile,
        "candidate": candidate,
        "llm": {
            "base_url": llm_config.base_url,
            "model": llm_config.model,
        },
        "stt": {
            "base_url": stt_config.base_url,
            "model": stt_config.model,
        },
        "tts": {
            "base_url": tts_config.base_url,
            "model": tts_config.model,
        },
        "route": route,
    }


def _evidence_run(result: Mapping[str, Any], *, run_id: str) -> Dict[str, Any]:
    total_turn_latency_ms = result.get("total_turn_latency_ms", result.get("turn_latency_ms"))
    llm_stage_latency_ms = result.get("llm_stage_latency_ms", result.get("llm_ms"))
    evidence_run = {
        "id": run_id,
        "latency": {
            "ttfa_ms": result.get("ttfa_ms"),
            "turn_latency_ms": result.get("turn_latency_ms"),
            "total_turn_latency_ms": total_turn_latency_ms,
            "stt_ms": result.get("stt_ms"),
            "llm_ms": result.get("llm_ms"),
            "llm_stage_latency_ms": llm_stage_latency_ms,
            "tts_ms": result.get("tts_ms"),
        },
        "comparison": {
            "stt_wer": result.get("stt_wer"),
            "tts_rtf": result.get("tts_rtf"),
            "tts_first_audio_observed": result.get("tts_first_audio_observed"),
        },
        "tts": {
            "output_bytes": result.get("tts_output_bytes"),
            "audio_seconds": result.get("tts_audio_seconds"),
            "source_sample_rate": result.get("tts_source_sample_rate"),
            "request_count": result.get("tts_request_count"),
        },
        "transcript": {
            "stt_hypothesis": result.get("stt_hypothesis"),
            "llm_reply": result.get("llm_reply"),
            "reference_text": result.get("reference_text"),
        },
        "tool": result.get("tool_call_outcome", _tool_call_outcome([])),
    }
    if result.get("input") is not None:
        evidence_run["input"] = result["input"]
    return evidence_run


def build_evidence_record(
    result: Mapping[str, Any],
    *,
    stt_config: STTStageConfig,
    llm_config: LLMStageConfig,
    tts_config: TTSStageConfig,
    profile: Optional[str] = None,
    candidate: Optional[str] = None,
    route_identity: Optional[Mapping[str, Any]] = None,
    run_id: str = "run-001",
) -> Dict[str, Any]:
    """Return the stable JSON evidence envelope for one voice benchmark run."""
    topology = result.get("topology")
    if not isinstance(topology, Mapping):
        topology = _topology_evidence(
            profile=profile,
            stt_config=stt_config,
            llm_config=llm_config,
            tts_config=tts_config,
        )
    evidence = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "identity": _evidence_identity(
            stt_config=stt_config,
            llm_config=llm_config,
            tts_config=tts_config,
            profile=profile,
            candidate=candidate,
            route_identity=route_identity,
        ),
        "topology": dict(topology),
        "runs": [_evidence_run(result, run_id=run_id)],
    }
    if result.get("measurement_scope") is not None:
        evidence["measurement_scope"] = dict(result["measurement_scope"])
    return evidence


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
    profile: Optional[str] = None,
    candidate: Optional[str] = None,
    route_identity: Optional[Mapping[str, Any]] = None,
    input_identity: Optional[Mapping[str, Any]] = None,
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
    t_stt_end = clock()

    t_llm_start = t_stt_end
    reply_text, tts_texts, tool_calls = _llm_reply_and_tts_chunks(
        llm_fn(hypothesis, llm_config),
        speech_chunk_max_chars=llm_config.speech_chunk_max_chars,
    )
    t_llm_end = clock()

    t_tts_start = t_llm_end
    first_audio_time: Optional[float] = None
    total_audio_bytes = 0
    for tts_text in tts_texts:
        for chunk in tts_fn(tts_text, tts_config):
            if not chunk:
                continue
            if first_audio_time is None:
                first_audio_time = clock()
            total_audio_bytes += len(chunk)
    t_end = clock()

    ttfa_ms = (first_audio_time - t0) * 1000.0 if first_audio_time is not None else None
    turn_latency_ms = (t_end - t0) * 1000.0
    stt_ms = (t_stt_end - t0) * 1000.0
    llm_ms = (t_llm_end - t_llm_start) * 1000.0
    tts_ms = (t_end - t_tts_start) * 1000.0

    synth_seconds = max(0.0, t_end - t_tts_start)
    audio_seconds = (total_audio_bytes / 2) / tts_config.source_sample_rate if total_audio_bytes else 0.0
    tts_rtf = (synth_seconds / audio_seconds) if audio_seconds > 0 else None

    reference = DEFAULT_REFERENCE_TEXT if reference_text is None else reference_text
    stt_wer = word_error_rate(reference, hypothesis) if reference else None
    if input_identity is None:
        input = _input_identity(
            pcm,
            sample_rate=sample_rate,
            reference_text=reference,
            input_kind="provided-pcm",
            qualification="caller-supplied-pcm",
            audio_format="raw-pcm-s16le-mono-%dhz" % sample_rate,
        )
    else:
        input = dict(input_identity)
        input["sample_sha256"] = hashlib.sha256(pcm).hexdigest()
        input["sample_bytes"] = len(pcm)
        input["duration_seconds"] = round(len(pcm) / (2 * sample_rate), 6)
        input["reference_text_sha256"] = hashlib.sha256(reference.encode("utf-8")).hexdigest()

    result: Dict[str, Any] = {
        "ttfa_ms": round(ttfa_ms, 2) if ttfa_ms is not None else None,
        "turn_latency_ms": round(turn_latency_ms, 2),
        "total_turn_latency_ms": round(turn_latency_ms, 2),
        "stt_ms": round(stt_ms, 2),
        "llm_ms": round(llm_ms, 2),
        "llm_stage_latency_ms": round(llm_ms, 2),
        "tts_ms": round(tts_ms, 2),
        "stt_wer": round(stt_wer, 4) if stt_wer is not None else None,
        "tts_rtf": round(tts_rtf, 4) if tts_rtf is not None else None,
        "tts_first_audio_observed": first_audio_time is not None,
        "tts_output_bytes": total_audio_bytes,
        "tts_audio_seconds": round(audio_seconds, 4),
        "tts_source_sample_rate": tts_config.source_sample_rate,
        "tts_request_count": len(tts_texts),
        "stt_hypothesis": hypothesis,
        "llm_reply": reply_text,
        "reference_text": reference,
        "input": input,
        "measurement_scope": dict(MEASUREMENT_SCOPE),
        "tool_call_outcome": _tool_call_outcome(tool_calls),
        "topology": _topology_evidence(
            profile=profile,
            stt_config=stt_config,
            llm_config=llm_config,
            tts_config=tts_config,
        ),
    }
    result["evidence"] = build_evidence_record(
        result,
        stt_config=stt_config,
        llm_config=llm_config,
        tts_config=tts_config,
        profile=profile,
        candidate=candidate,
        route_identity=route_identity,
    )
    return result


def _stage_config_from_table(table: Mapping[str, Any], cls) -> Any:
    allowed = {field.name for field in fields(cls)}
    kwargs: Dict[str, Any] = {
        key: value for key, value in table.items() if key in allowed
    }
    kwargs.setdefault("base_url", "")
    kwargs.setdefault("model", "")
    return cls(**kwargs)


def run_benchmark_from_manifest(
    data: Mapping[str, Any],
    *,
    profile: Optional[str] = None,
    candidate: Optional[str] = None,
    pcm: Optional[bytes] = None,
    sample_rate: int = 16000,
    reference_text: Optional[str] = None,
    stt_transport: Optional[Callable[..., Any]] = None,
    llm_transport: Optional[Callable[..., Any]] = None,
    tts_transport: Optional[Callable[..., Any]] = None,
    stt_stream_fn: Optional[StreamFn] = None,
    llm_stream_fn: Optional[StreamFn] = None,
    tts_stream_fn: Optional[StreamFn] = None,
    input_identity: Optional[Mapping[str, Any]] = None,
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
    reference = DEFAULT_REFERENCE_TEXT if reference_text is None else reference_text
    if input_identity is None:
        if pcm is None:
            input_identity = _input_identity(
                sample,
                sample_rate=sample_rate,
                reference_text=reference,
                input_kind="synthetic-tone-not-speech",
                qualification="not-qualifying",
                audio_format="raw-pcm-s16le-mono-%dhz" % sample_rate,
            )
            input_identity["note"] = (
                "Synthetic tone is not speech and is not qualifying voice evidence."
            )
        else:
            input_identity = _input_identity(
                sample,
                sample_rate=sample_rate,
                reference_text=reference,
                input_kind="provided-pcm",
                qualification="caller-supplied-pcm",
                audio_format="raw-pcm-s16le-mono-%dhz" % sample_rate,
            )
    route_identity = _route_identity_from_manifest(data)
    return run_benchmark(
        stt_config=stt_config, llm_config=llm_config, tts_config=tts_config,
        pcm=sample, sample_rate=sample_rate, reference_text=reference_text,
        stt_transport=stt_transport, llm_transport=llm_transport, tts_transport=tts_transport,
        stt_stream_fn=stt_stream_fn, llm_stream_fn=llm_stream_fn, tts_stream_fn=tts_stream_fn,
        profile=profile, candidate=candidate, route_identity=route_identity,
        input_identity=input_identity,
    )


def run_audio_benchmark(
    *,
    stt_config: STTStageConfig,
    tts_config: TTSStageConfig,
    reference_text: str = DEFAULT_REFERENCE_TEXT,
    stt_transport: Optional[Callable[..., Any]] = None,
    tts_transport: Optional[Callable[..., Any]] = None,
    stt_stream_fn: Optional[StreamFn] = None,
    tts_stream_fn: Optional[StreamFn] = None,
    clock: Callable[[], float] = time.perf_counter,
    profile: Optional[str] = None,
) -> Dict[str, Any]:
    """Measure a TTS -> STT loop without requiring an LLM or Realtime proxy."""
    stt_fn = stt_stream_fn or (
        lambda p, sr, cfg: transcribe_stream(p, sr, cfg, transport=stt_transport)
    )
    tts_fn = tts_stream_fn or (
        lambda text, cfg: stream_speech(text, cfg, transport=tts_transport)
    )
    started = clock()
    chunks = [chunk for chunk in tts_fn(reference_text, tts_config) if chunk]
    tts_finished = clock()
    pcm = b"".join(chunks)
    hypothesis = ""
    for text, is_final in stt_fn(pcm, tts_config.source_sample_rate, stt_config):
        hypothesis = text
        if is_final:
            break
    finished = clock()
    tts_seconds = max(0.0, tts_finished - started)
    audio_seconds = (
        (len(pcm) / 2) / tts_config.source_sample_rate if pcm else 0.0
    )
    stt_wer = word_error_rate(reference_text, hypothesis) if reference_text else None
    result = {
        "scope": "audio",
        "audio_roundtrip_ms": round((finished - started) * 1000.0, 2),
        "tts_ms": round(tts_seconds * 1000.0, 2),
        "stt_ms": round((finished - tts_finished) * 1000.0, 2),
        "tts_output_bytes": len(pcm),
        "tts_audio_seconds": round(audio_seconds, 4),
        "tts_rtf": round(tts_seconds / audio_seconds, 4) if audio_seconds else None,
        "stt_hypothesis": hypothesis,
        "reference_text": reference_text,
        "stt_wer": round(stt_wer, 4) if stt_wer is not None else None,
        "topology": {
            "profile": profile,
            "stt": {"base_url": stt_config.base_url, "model": stt_config.model},
            "tts": {"base_url": tts_config.base_url, "model": tts_config.model},
        },
        "promotion_quality_evidence": False,
        "promoted": False,
    }
    result["evidence"] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "evidence_scope": "voice-pipeline",
        "promotion_quality_evidence": False,
        "identity": {
            "scope": "audio",
            "profile": profile,
            "stt_model": stt_config.model,
            "stt_base_url": stt_config.base_url,
            "tts_model": tts_config.model,
            "tts_base_url": tts_config.base_url,
        },
        "topology": dict(result["topology"]),
        "runs": [dict(result, evidence=None, id="run-001")],
    }
    result["evidence"]["runs"][0].pop("evidence", None)
    return result


def run_audio_benchmark_from_manifest(
    data: Mapping[str, Any],
    *,
    profile: Optional[str] = None,
    reference_text: str = DEFAULT_REFERENCE_TEXT,
    stt_transport: Optional[Callable[..., Any]] = None,
    tts_transport: Optional[Callable[..., Any]] = None,
    stt_stream_fn: Optional[StreamFn] = None,
    tts_stream_fn: Optional[StreamFn] = None,
) -> Dict[str, Any]:
    """Build the two audio stage configs and run the bounded audio-only loop."""
    voice = data.get("voice", {})
    return run_audio_benchmark(
        stt_config=_stage_config_from_table(voice.get("stt", {}), STTStageConfig),
        tts_config=_stage_config_from_table(voice.get("tts", {}), TTSStageConfig),
        reference_text=reference_text,
        stt_transport=stt_transport,
        tts_transport=tts_transport,
        stt_stream_fn=stt_stream_fn,
        tts_stream_fn=tts_stream_fn,
        profile=profile,
    )


def to_json(result: Mapping[str, Any]) -> str:
    """Render a benchmark result as pretty-printed JSON (what the CLI prints)."""
    return json.dumps(dict(result), indent=2, sort_keys=True)
