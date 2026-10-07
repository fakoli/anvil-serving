"""Bounded sequential TTS corpus replay; audio remains in private artifacts.

Timing observes HTTP reads, never acoustic playback. Signal screening is
advisory; independent STT and human listening remain separate quality gates.
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
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .stages.base import bearer_headers
from .stages.tts import TTSStageConfig, build_speech_request_body


CORPUS_SCHEMA_VERSION = "tts-corpus/v1"
EVIDENCE_SCHEMA_VERSION = "tts-benchmark-evidence/v1"
MAX_CORPUS_BYTES = 1024 * 1024
MAX_CASES = 128
MAX_TEXT_CHARS = 10000
MAX_WARM_REQUESTS = 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_AUDIO_SECONDS = 120.0
MAX_CHUNK_RECORDS = 64
MAX_RUN_CHUNK_RECORDS = 16384
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_LANGUAGE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


class TTSBenchmarkError(ValueError):
    """Invalid benchmark input, rejected before any endpoint request."""


class _RequestFailure(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class TTSCase:
    schema_version: str
    id: str
    text: str
    category: str
    language: str


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_hash(value: object) -> str:
    return _sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8"))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise TTSBenchmarkError("corpus JSON object contains duplicate fields")
        result[key] = value
    return result


def validate_tts_corpus(
    manifest_path: os.PathLike[str] | str, *, expected_cases: int | None = None,
) -> dict:
    """Freeze versioned JSONL text cases, including their file-order identity."""
    if expected_cases is not None and (
        type(expected_cases) is not int or not 1 <= expected_cases <= MAX_CASES
    ):
        raise TTSBenchmarkError("expected case count must be between 1 and %d" % MAX_CASES)
    manifest = Path(manifest_path).expanduser()
    try:
        before = manifest.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_CORPUS_BYTES:
            raise TTSBenchmarkError("corpus must be a bounded regular file, without a final symlink")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(manifest, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
                raise TTSBenchmarkError("corpus changed while opening")
            raw = handle.read(MAX_CORPUS_BYTES + 1)
        if len(raw) > MAX_CORPUS_BYTES:
            raise TTSBenchmarkError("corpus exceeds byte limit")
        lines = raw.decode("utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise TTSBenchmarkError("corpus cannot be read as a regular UTF-8 file") from exc
    cases = []
    seen = set()
    fields = {"schema_version", "id", "text", "category", "language"}
    for number, line in enumerate(lines, 1):
        if not line.strip():
            raise TTSBenchmarkError("corpus line %d is blank" % number)
        try:
            row = json.loads(line, object_pairs_hook=_unique_object)
        except json.JSONDecodeError as exc:
            raise TTSBenchmarkError("corpus line %d is malformed JSON" % number) from exc
        if not isinstance(row, dict) or set(row) != fields:
            raise TTSBenchmarkError("corpus line %d must contain exactly schema_version, id, text, category, language" % number)
        if row["schema_version"] != CORPUS_SCHEMA_VERSION:
            raise TTSBenchmarkError("corpus line %d must use %s" % (number, CORPUS_SCHEMA_VERSION))
        for field in fields:
            if not isinstance(row[field], str) or not row[field].strip() or "\x00" in row[field]:
                raise TTSBenchmarkError("corpus line %d has an empty or invalid %s" % (number, field))
        if not _SAFE_ID.fullmatch(row["id"]):
            raise TTSBenchmarkError("corpus id must be a safe filename token without path separators")
        if len(row["text"]) > MAX_TEXT_CHARS or len(row["category"]) > 128:
            raise TTSBenchmarkError("corpus text or category exceeds its limit")
        if not _LANGUAGE.fullmatch(row["language"]):
            raise TTSBenchmarkError("corpus language must be a language tag")
        if row["id"] in seen:
            raise TTSBenchmarkError("duplicate corpus id on line %d" % number)
        seen.add(row["id"])
        cases.append(TTSCase(**row))
        if len(cases) > MAX_CASES:
            raise TTSBenchmarkError("corpus contains too many cases")
    if not cases:
        raise TTSBenchmarkError("corpus contains no cases")
    if expected_cases is not None and len(cases) != expected_cases:
        raise TTSBenchmarkError("corpus must contain %d cases; found %d" % (expected_cases, len(cases)))
    return {
        "schema_version": CORPUS_SCHEMA_VERSION,
        "manifest_sha256": _sha256(raw),
        "manifest_bytes": len(raw),
        "manifest_path_sha256": _sha256(str(manifest.absolute()).encode("utf-8")),
        "case_count": len(cases),
        "ordered_case_ids": [case.id for case in cases],
        "category_counts": dict(sorted(Counter(case.category for case in cases).items())),
        "language_counts": dict(sorted(Counter(case.language for case in cases).items())),
        "cases": cases,
    }


def validate_audio_output_directory(path: os.PathLike[str] | str) -> Path:
    """Refuse overwrite, parent traversal, symlinks, and every Git worktree."""
    raw = Path(path).expanduser()
    if ".." in raw.parts:
        raise TTSBenchmarkError("audio output directory must not contain parent traversal")
    target = raw.absolute()
    if target == target.parent:
        raise TTSBenchmarkError("audio output directory must not be a filesystem root")
    for ancestor in (target, *target.parents):
        if ancestor.is_symlink():
            raise TTSBenchmarkError("audio output directory must not traverse symlinks")
        if (ancestor / ".git").exists():
            raise TTSBenchmarkError("audio output directory must be outside Git worktrees")
    if target.exists():
        raise TTSBenchmarkError("audio output directory must be new; existing paths are not overwritten")
    return target


def _validate_config(config: TTSStageConfig) -> None:
    try:
        parsed = urllib.parse.urlparse(config.base_url)
        parsed.port
    except (TypeError, ValueError) as exc:
        raise TTSBenchmarkError("TTS base_url is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username or parsed.password or parsed.params or parsed.query or parsed.fragment
        or not parsed.path.rstrip("/").endswith("/v1")
    ):
        raise TTSBenchmarkError("TTS base_url must be a credential-free HTTP(S) /v1 URL")
    if parsed.hostname == "localhost" or parsed.hostname in {"0.0.0.0", "::1"}:
        raise TTSBenchmarkError("TTS same-host URL must use 127.0.0.1")
    if config.protocol != "openai":
        raise TTSBenchmarkError("TTS corpus benchmark currently supports only the declared openai HTTP protocol")
    if not isinstance(config.stream, bool):
        raise TTSBenchmarkError("TTS stream must be true or false")
    if not isinstance(config.model, str) or not config.model.strip():
        raise TTSBenchmarkError("TTS model must be non-empty")
    if config.response_format not in {"pcm", "wav"}:
        raise TTSBenchmarkError("TTS response_format must be pcm or wav")
    if isinstance(config.timeout, bool) or not isinstance(config.timeout, (int, float)) or not math.isfinite(config.timeout) or not 0 < config.timeout <= 300:
        raise TTSBenchmarkError("TTS timeout must be finite and between zero and 300 seconds")
    for name in ("source_sample_rate", "target_sample_rate"):
        value = getattr(config, name)
        if type(value) is not int or not 8000 <= value <= 192000:
            raise TTSBenchmarkError("TTS %s must be an integer from 8000 through 192000" % name)
    if type(config.chunk_bytes) is not int or not 256 <= config.chunk_bytes <= 1024 * 1024:
        raise TTSBenchmarkError("TTS chunk_bytes must be from 256 through 1048576")
    for name in ("voice_id", "language"):
        value = getattr(config, name)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise TTSBenchmarkError("TTS %s must be a non-empty string when declared" % name)
    if config.api_key_env is not None and not re.fullmatch(r"[A-Z_][A-Z0-9_]*", config.api_key_env):
        raise TTSBenchmarkError("TTS api_key_env must name an environment variable")
    if config.api_key_env and not os.environ.get(config.api_key_env, "").strip():
        raise TTSBenchmarkError("configured TTS credential environment variable is unset")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_transport(url: str, *, data: bytes, headers: Mapping[str, str], timeout: float):
    request = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


def _decode_audio(raw: bytes, config: TTSStageConfig) -> dict:
    if not raw:
        raise _RequestFailure("empty_audio", "response contained no audio")
    if config.response_format == "pcm":
        if raw.startswith(b"RIFF"):
            raise _RequestFailure("format_mismatch", "declared PCM response contained a WAV container")
        if len(raw) % 2:
            raise _RequestFailure("malformed_audio", "PCM response must contain complete signed-16-bit samples")
        return {"pcm": raw, "sample_rate": config.source_sample_rate, "audio_offset": 0,
                "source_frame_bytes": 2, "source_encoding": "pcm-s16le", "nonfinite_samples": 0,
                "source_payload_bytes": len(raw), "source_payload_sha256": _sha256(raw)}
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise _RequestFailure("format_mismatch", "declared WAV response was not a RIFF/WAVE container")
    if struct.unpack_from("<I", raw, 4)[0] != len(raw) - 8:
        raise _RequestFailure("malformed_audio", "WAV RIFF size did not match response bytes")
    offset = 12
    fmt = None
    payload = None
    audio_offset = None
    while offset < len(raw):
        if offset + 8 > len(raw):
            raise _RequestFailure("malformed_audio", "WAV chunk header was truncated")
        tag = raw[offset:offset + 4]
        size = struct.unpack_from("<I", raw, offset + 4)[0]
        start, end = offset + 8, offset + 8 + size
        padded = end + size % 2
        if padded > len(raw):
            raise _RequestFailure("malformed_audio", "WAV chunk payload was truncated")
        if tag == b"fmt ":
            if fmt is not None:
                raise _RequestFailure("malformed_audio", "WAV had duplicate format chunks")
            fmt = raw[start:end]
        elif tag == b"data":
            if payload is not None:
                raise _RequestFailure("malformed_audio", "WAV had duplicate audio chunks")
            payload, audio_offset = raw[start:end], start
        offset = padded
    if fmt is None or payload is None or len(fmt) not in {16, 18}:
        raise _RequestFailure("malformed_audio", "WAV requires a canonical PCM16 or float32 format and data chunk")
    if len(fmt) == 18 and fmt[16:] != b"\0\0":
        raise _RequestFailure("malformed_audio", "WAV format extension is unsupported")
    encoding, channels, rate, byte_rate, align, bits = struct.unpack("<HHIIHH", fmt[:16])
    if channels != 1 or not 8000 <= rate <= 192000 or (encoding, bits) not in {(1, 16), (3, 32)}:
        raise _RequestFailure("malformed_audio", "WAV must be mono PCM16 or IEEE float32 at a supported sample rate")
    width = bits // 8
    if align != width or byte_rate != rate * width or len(payload) % width:
        raise _RequestFailure("malformed_audio", "WAV audio alignment or byte rate was inconsistent")
    nonfinite = 0
    source_payload_bytes, source_payload_sha256 = len(payload), _sha256(payload)
    if encoding == 3:
        samples = array.array("h")
        for (value,) in struct.iter_unpack("<f", payload):
            if not math.isfinite(value):
                nonfinite += 1
                value = 0.0
            samples.append(round(max(-1.0, min(1.0, value)) * 32767))
        if sys.byteorder != "little":
            samples.byteswap()
        payload = samples.tobytes()
    return {"pcm": payload, "sample_rate": rate, "audio_offset": audio_offset,
            "source_frame_bytes": width, "source_encoding": "pcm-s16le" if encoding == 1 else "ieee-float32le",
            "nonfinite_samples": nonfinite, "source_payload_bytes": source_payload_bytes,
            "source_payload_sha256": source_payload_sha256}


def _normalize_pcm(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
    if source_rate == target_rate:
        return pcm
    source = array.array("h")
    source.frombytes(pcm)
    if sys.byteorder != "little":
        source.byteswap()
    count = max(1, round(len(source) * target_rate / source_rate))
    result = array.array("h")
    for index in range(count):
        position = index * source_rate / target_rate
        lower = min(int(position), len(source) - 1)
        upper = min(lower + 1, len(source) - 1)
        fraction = position - lower
        result.append(int(source[lower] + (source[upper] - source[lower]) * fraction))
    if sys.byteorder != "little":
        result.byteswap()
    return result.tobytes()


def _write_artifacts(root: Path, request_id: str, raw: bytes, pcm: bytes, config: TTSStageConfig) -> dict:
    source_name = request_id + ".response." + config.response_format
    normalized_name = request_id + ".wav"
    with open(root / source_name, "xb") as stream:
        os.chmod(root / source_name, 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    with open(root / normalized_name, "xb") as stream:
        os.chmod(root / normalized_name, 0o600)
        with wave.open(stream, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(config.target_sample_rate)
            wav.writeframes(pcm)
        stream.flush()
        os.fsync(stream.fileno())
    normalized = (root / normalized_name).read_bytes()
    return {"response_path": source_name, "normalized_wav_path": normalized_name,
            "normalized_wav_bytes": len(normalized), "normalized_wav_sha256": _sha256(normalized)}


def _one_request(
    case: TTSCase, *, repetition: int, sequence: int, root: Path, config: TTSStageConfig,
    transport: Callable[..., Any], clock: Callable[[], float], remaining_bytes: int,
    chunk_record_budget: int,
) -> tuple[dict, int]:
    request_id = "request-%04d" % sequence
    body = build_speech_request_body(case.text, config)
    payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
    raw = bytearray()
    chunks = []
    count = 0
    first_body = None
    failure = None
    status = None
    response_complete = False
    audio = None
    screening = None
    content_type = None
    read_method = None
    first_audio = None
    response = None
    started = clock()
    end_to_end = None
    artifact_bytes = 0
    # Retain first-frame observations even when the bounded chunk list ends
    # before a large WAV metadata chunk. Scan each RIFF chunk header once.
    wav_scan_offset = 12
    observed_audio_offset = 0 if config.response_format == "pcm" else None
    first_frame_reads = {2: None, 4: None}
    try:
        response = transport(
            config.base_url.rstrip("/") + "/audio/speech", data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/octet-stream, audio/wav",
                     **bearer_headers(config.api_key_env)}, timeout=config.timeout,
        )
        status = getattr(response, "status", None)
        if status is None and callable(getattr(response, "getcode", None)):
            status = response.getcode()
        if type(status) is not int or not 200 <= status < 300:
            raise _RequestFailure("http_status", "endpoint did not return HTTP 2xx")
        content_type = str(getattr(response, "headers", {}).get("Content-Type", "")).split(";", 1)[0].lower()
        if content_type and content_type not in {
            "application/octet-stream", "audio/pcm", "audio/x-pcm", "audio/l16", "audio/wav", "audio/x-wav", "audio/wave",
        }:
            raise _RequestFailure("format_mismatch", "response Content-Type was not an accepted audio type")
        if config.response_format == "pcm" and content_type in {"audio/wav", "audio/x-wav", "audio/wave"}:
            raise _RequestFailure("format_mismatch", "declared PCM response had WAV Content-Type")
        read = getattr(response, "read1", None)
        read_method = "read1" if callable(read) else "read"
        if not callable(read):
            read = response.read
        while True:
            if clock() - started > config.timeout:
                raise _RequestFailure("timeout", "request exceeded its elapsed deadline")
            chunk = read(config.chunk_bytes)
            arrival = (clock() - started) * 1000
            if not chunk:
                response_complete = True
                break
            if not isinstance(chunk, bytes):
                raise _RequestFailure("transport_contract", "HTTP reader returned a non-byte body")
            if first_body is None:
                first_body = arrival
            if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                raise _RequestFailure("response_limit", "response exceeded the bounded audio byte limit")
            raw.extend(chunk)
            count += 1
            if config.response_format == "wav" and len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
                while wav_scan_offset + 8 <= len(raw):
                    size = struct.unpack_from("<I", raw, wav_scan_offset + 4)[0]
                    end = wav_scan_offset + 8 + size + size % 2
                    if raw[wav_scan_offset:wav_scan_offset + 4] == b"data":
                        if observed_audio_offset is None:
                            observed_audio_offset = wav_scan_offset + 8
                        wav_scan_offset = end
                    elif end <= len(raw):
                        wav_scan_offset = end
                    else:
                        break
            if observed_audio_offset is not None:
                for width in first_frame_reads:
                    if first_frame_reads[width] is None and len(raw) >= observed_audio_offset + width:
                        first_frame_reads[width] = arrival
            if len(chunks) < min(MAX_CHUNK_RECORDS, chunk_record_budget):
                chunks.append({"index": count, "elapsed_ms": round(arrival, 6),
                               "bytes": len(chunk), "cumulative_bytes": len(raw)})
        end_to_end = (clock() - started) * 1000
        decoded = _decode_audio(bytes(raw), config)
        pcm = decoded["pcm"]
        rate = decoded["sample_rate"]
        duration = len(pcm) / (2 * rate)
        if not duration:
            raise _RequestFailure("empty_audio", "response contained no audio samples")
        if duration > MAX_AUDIO_SECONDS:
            raise _RequestFailure("audio_duration_limit", "response exceeded the bounded audio duration")
        normalized = _normalize_pcm(pcm, rate, config.target_sample_rate)
        expected_bytes = len(raw) + len(normalized) + 44
        if expected_bytes > remaining_bytes:
            raise _RequestFailure("artifact_limit", "run exceeded its bounded artifact storage budget")
        paths = _write_artifacts(root, request_id, bytes(raw), normalized, config)
        artifact_bytes = len(raw) + paths["normalized_wav_bytes"]
        first_audio = first_frame_reads[decoded["source_frame_bytes"]]
        audio = {
            "source_encoding": decoded["source_encoding"], "source_sample_rate": rate,
            "configured_source_sample_rate": config.source_sample_rate,
            "source_pcm_bytes": len(pcm), "source_pcm_sha256": _sha256(pcm),
            "decoded_pcm_encoding": "pcm-s16le",
            "source_audio_payload_bytes": decoded["source_payload_bytes"],
            "source_audio_payload_sha256": decoded["source_payload_sha256"],
            "duration_seconds": round(duration, 9), "channels": 1,
            "normalized_sample_rate": config.target_sample_rate,
            "normalized_pcm_bytes": len(normalized), "normalized_pcm_sha256": _sha256(normalized),
            "normalized_duration_seconds": round(len(normalized) / (2 * config.target_sample_rate), 9),
            **paths,
        }
        block = max(2, rate // 10 * 2)
        repeated_blocks = any(
            pcm[start:start + block] == pcm[start + block:start + 2 * block] == pcm[start + 2 * block:start + 3 * block]
            for start in range(0, max(0, len(pcm) - 3 * block + 1), block)
        )
        screening = {"advisory_only": True, "blank_audio": not any(pcm),
                     "nonfinite_samples": decoded["nonfinite_samples"],
                     "repeated_100ms_blocks": repeated_blocks,
                     "identical_audio_for_different_text": False}
    except urllib.error.HTTPError as exc:
        status = exc.code
        failure = {"kind": "http_status", "message": "endpoint returned HTTP %d" % exc.code}
        exc.close()
    except _RequestFailure as exc:
        failure = {"kind": exc.kind, "message": str(exc)}
    except Exception as exc:  # external reader/filesystem can fail; never copy body or secret-bearing exception strings
        failure = {"kind": "transport_or_artifact_error", "message": type(exc).__name__}
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        if failure is not None:
            # A disk error can leave an exclusive artifact partially written.
            # Its actual bytes still consume the run's storage budget.
            artifact_bytes = 0
            for suffix in (".response." + config.response_format, ".wav"):
                try:
                    artifact_bytes += (root / (request_id + suffix)).stat().st_size
                except OSError:
                    pass
    if end_to_end is None:
        end_to_end = (clock() - started) * 1000
    generation_seconds = end_to_end / 1000
    audio_seconds = audio["duration_seconds"] if audio else None
    return {
        "id": request_id, "sequence": sequence, "case_id": case.id,
        "repetition": repetition, "lane": "cold" if repetition == 0 else "warm",
        "text": case.text, "text_sha256": _sha256(case.text.encode("utf-8")),
        "category": case.category, "language": case.language,
        "request_sha256": _sha256(payload),
        "response": {"status": status, "format": config.response_format,
                     "content_type": content_type, "complete": response_complete,
                     "bytes": len(raw), "sha256": _sha256(bytes(raw))},
        "timing": {"first_response_body_ms": _rounded(first_body),
                   "first_audio_ms": _rounded(first_audio), "end_to_end_ms": _rounded(end_to_end),
                   "read_method": read_method, "chunk_count": count, "chunks": chunks,
                   "chunk_records_retained": len(chunks),
                   "chunk_records_omitted": count - len(chunks),
                   "chunk_records_truncated": count > len(chunks)},
        "generation_seconds_per_audio_second": round(generation_seconds / audio_seconds, 9) if audio_seconds else None,
        "audio_seconds_per_generation_second": round(audio_seconds / generation_seconds, 9) if audio_seconds and generation_seconds > 0 else None,
        "audio": audio, "screening": screening, "failure": failure,
    }, artifact_bytes


def _rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _percentiles(values: list[float]) -> dict:
    ordered = sorted(values)

    def at(q: float):
        if not ordered:
            return None
        index = (len(ordered) - 1) * q
        low, high = math.floor(index), math.ceil(index)
        return _rounded(ordered[low] + (ordered[high] - ordered[low]) * (index - low))

    return {"p50": at(0.5), "p95": at(0.95), "max": _rounded(ordered[-1]) if ordered else None}


def _aggregate(records: list[dict]) -> dict:
    good = [row for row in records if row["failure"] is None]
    return {
        "requests": len(records), "successful": len(good), "failed": len(records) - len(good),
        "first_audio_ms": _percentiles([row["timing"]["first_audio_ms"] for row in good if row["timing"]["first_audio_ms"] is not None]),
        "end_to_end_ms": _percentiles([row["timing"]["end_to_end_ms"] for row in good]),
        "generation_seconds_per_audio_second": _percentiles([row["generation_seconds_per_audio_second"] for row in good]),
        "audio_seconds_per_generation_second": _percentiles([row["audio_seconds_per_generation_second"] for row in good if row["audio_seconds_per_generation_second"] is not None]),
    }


def run_tts_benchmark(
    corpus_manifest: os.PathLike[str] | str, *, config: TTSStageConfig,
    audio_out: os.PathLike[str] | str, repetitions: int = 3, concurrency: int = 1,
    expected_cases: int | None = None, endpoint_identity: Mapping[str, Any] | None = None,
    config_identity: Mapping[str, Any] | None = None,
    transport: Callable[..., Any] | None = None, clock: Callable[[], float] = time.perf_counter,
) -> dict:
    """Run one observed cold request then corpus-order sequential warm repetitions.

    The cold lane is the first request observed by this process; endpoint/model
    caches are not reset and no model lifecycle or promotion is performed.
    """
    if type(repetitions) is not int or not 1 <= repetitions <= 20:
        raise TTSBenchmarkError("repetitions must be an integer from 1 through 20")
    if type(concurrency) is not int or concurrency != 1:
        raise TTSBenchmarkError("TTS benchmark concurrency must be 1")
    _validate_config(config)
    validated = validate_tts_corpus(corpus_manifest, expected_cases=expected_cases)
    cases = validated.pop("cases")
    if len(cases) * repetitions > MAX_WARM_REQUESTS:
        raise TTSBenchmarkError("TTS schedule exceeds the bounded request count")
    resolved_config = asdict(config)
    endpoint = {
        "base_url": config.base_url, "speech_url": config.base_url.rstrip("/") + "/audio/speech",
        "model": config.model, "protocol": config.protocol, "voice_id": config.voice_id,
        "voice_selection": "declared" if config.voice_id else "server-default-unverified",
        "language_conditioning": config.language, "response_format": config.response_format,
        "stream": config.stream,
        "source_sample_rate": config.source_sample_rate, "target_sample_rate": config.target_sample_rate,
        "identity_authority": "operator-declared; not independently attested by endpoint",
    }
    if set(endpoint_identity or {}) & set(endpoint):
        raise TTSBenchmarkError("endpoint identity metadata must not override request identity")
    endpoint.update(dict(endpoint_identity or {}))
    configuration = {**dict(config_identity or {}), "resolved_tts": resolved_config,
                     "resolved_tts_sha256": _json_hash(resolved_config)}
    # Validate serializability before filesystem creation or requests.
    _json_hash(endpoint)
    _json_hash(configuration)
    root = validate_audio_output_directory(audio_out)
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(mode=0o700)
    transport = transport or _default_transport
    started_at = datetime.now(timezone.utc).isoformat()
    artifact_bytes = 0
    chunk_records = 0
    records = []
    seen_audio: dict[str, set[str]] = {}

    def request(case, repetition, sequence):
        nonlocal artifact_bytes, chunk_records
        row, written = _one_request(case, repetition=repetition, sequence=sequence, root=root,
                                    config=config, transport=transport, clock=clock,
                                    remaining_bytes=MAX_ARTIFACT_BYTES - artifact_bytes,
                                    chunk_record_budget=MAX_RUN_CHUNK_RECORDS - chunk_records)
        artifact_bytes += written
        chunk_records += len(row["timing"]["chunks"])
        if row["audio"] is not None:
            digest = row["audio"]["normalized_pcm_sha256"]
            previous_texts = seen_audio.setdefault(digest, set())
            row["screening"]["identical_audio_for_different_text"] = any(text != case.text for text in previous_texts)
            previous_texts.add(case.text)
        return row

    cold = request(cases[0], 0, 1)
    started = clock()
    for repetition in range(1, repetitions + 1):
        for case in cases:
            records.append(request(case, repetition, len(records) + 2))
    wall_seconds = clock() - started
    failures = [row for row in [cold, *records] if row["failure"] is not None]
    groups = {category: [row for row in records if row["category"] == category]
              for category in validated["category_counts"]}
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION, "started_at": started_at,
        "complete": not failures and len(records) == len(cases) * repetitions,
        "endpoint": endpoint, "configuration": configuration, "corpus": validated,
        "measurement_scope": {
            "kind": "sequential-http-tts-corpus-replay", "acoustic_playback": False,
            "upstream_stream_requested": config.stream,
            "upstream_buffering": "stream-requested; HTTP reads may still be buffered" if config.stream
            else "buffered-request; first audio may follow full synthesis",
            "cancellation": "not_measured", "realtime_session": False,
            "first_audio": "first observed HTTP read containing one complete audio frame; WAV headers excluded",
            "end_to_end": "HTTP request start through response EOF, excluding decoding and artifact writes",
            "warm_wall_includes_artifact_processing": True,
            "warm_wall": "warm replay wall time includes HTTP, decoding, resampling, fsync, artifact hashing and screening; not endpoint throughput",
            "chunk_timing": "client HTTP read observations; not server synthesis chunk boundaries",
            "normalization": "whole-response linear interpolation to configured mono PCM16 rate; no anti-aliasing filter",
            "float_normalization": "nonfinite float samples replaced with zero and reported as advisory",
            "generation_seconds_per_audio_second": "request-through-EOF seconds / source audio seconds; lower is faster",
            "audio_seconds_per_generation_second": "source audio seconds / request-through-EOF seconds; higher is faster",
        },
        "schedule": {"cold_requests": 1, "cold_state": "first-observed-request; endpoint caches not reset",
                     "repetitions": repetitions, "concurrency": concurrency, "order": "repetition then JSONL file order",
                     "expected_warm_requests": len(cases) * repetitions, "observed_warm_requests": len(records)},
        "cold_request": cold, "runs": records, "failures": failures,
        "summary": {"warm": _aggregate(records), "categories": {key: _aggregate(rows) for key, rows in groups.items()},
                    "warm_artifact_inclusive_wall_seconds": round(wall_seconds, 6),
                    "endpoint_throughput_measured": False},
        "chunk_record_budget": {"per_request_limit": MAX_CHUNK_RECORDS,
                                "run_limit": MAX_RUN_CHUNK_RECORDS, "retained": chunk_records,
                                "retention": "first HTTP reads up to per-request and global limits; first-frame timing retained independently"},
        "audio_artifacts": {"location": "external-audio-directory", "directory_path_sha256": _sha256(str(root).encode("utf-8")),
                            "paths": "relative to supplied --audio-out; absolute filesystem paths omitted",
                            "bytes": artifact_bytes, "byte_limit": MAX_ARTIFACT_BYTES,
                            "response_byte_limit": MAX_RESPONSE_BYTES, "audio_duration_limit_seconds": MAX_AUDIO_SECONDS},
        "quality_validation": {"screening": "deterministic advisory signal checks only",
                               "independent_stt": "not_run", "human_listening": "not_run", "listening_preference": "not_measured"},
        "promotion": {"promoted": False, "human_gate_required": True,
                      "evidence_scope": "tts-generation-measurement; independent quality validation required"},
    }
