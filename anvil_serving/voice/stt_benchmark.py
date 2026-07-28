"""Reusable multi-sample STT benchmark and evidence contract."""
from __future__ import annotations

import concurrent.futures
import math
import os
import re
import time
import unicodedata
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .corpus import CorpusCase, audio_metadata, validate_corpus
from .stages.stt import STTClientError, STTStageConfig, transcribe_file


EVIDENCE_SCHEMA_VERSION = "stt-benchmark-evidence/v1"
TranscribeFn = Callable[[os.PathLike[str] | str, STTStageConfig], Mapping[str, Any]]


class STTBenchmarkError(ValueError):
    """Raised when a benchmark request is invalid before any measurement."""


def _edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row, expected in enumerate(reference, start=1):
        current = [row]
        for column, actual in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (expected != actual),
                )
            )
        previous = current
    return previous[-1]


def normalized_words(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = "".join(
        " " if unicodedata.category(char).startswith(("P", "S")) else char
        for char in normalized
    )
    return normalized.split()


def error_counts(reference: str, hypothesis: str) -> dict[str, int]:
    ref_words = normalized_words(reference)
    hyp_words = normalized_words(hypothesis)
    return {
        "word_edits": _edit_distance(ref_words, hyp_words),
        "reference_words": len(ref_words),
        "char_edits": _edit_distance(list(reference), list(hypothesis)),
        "reference_chars": len(reference),
    }


def word_error_rate(reference: str, hypothesis: str) -> float:
    counts = error_counts(reference, hypothesis)
    denominator = counts["reference_words"]
    return counts["word_edits"] / denominator if denominator else float(bool(hypothesis))


def character_error_rate(reference: str, hypothesis: str) -> float:
    counts = error_counts(reference, hypothesis)
    denominator = counts["reference_chars"]
    return counts["char_edits"] / denominator if denominator else float(bool(hypothesis))


def repetition_detected(text: str) -> bool:
    words = normalized_words(text)
    if not words:
        return False
    if any(count >= 5 for count in Counter(words).values()) and len(words) <= 12:
        return True
    for width in range(2, min(12, len(words) // 2) + 1):
        for start in range(0, len(words) - (2 * width) + 1):
            if words[start:start + width] == words[start + width:start + (2 * width)]:
                return True
    return bool(re.search(r"\b(\w+)(?:\s+\1){3,}\b", " ".join(words)))


def _percentile(values: Iterable[float], percentile: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * percentile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _validate_endpoint(config: STTStageConfig) -> None:
    parsed = urllib.parse.urlparse(config.base_url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise STTBenchmarkError("STT base_url must be a credential-free HTTP(S) URL")
    if parsed.hostname == "localhost":
        raise STTBenchmarkError("STT base_url must use 127.0.0.1, not localhost")
    if not parsed.path.rstrip("/").endswith("/v1"):
        raise STTBenchmarkError("STT base_url path must end in /v1")
    if not config.model.strip():
        raise STTBenchmarkError("STT model must be non-empty")
    if config.timeout <= 0:
        raise STTBenchmarkError("STT timeout must be positive")


def _case_path(manifest: Path, case: CorpusCase) -> Path:
    return (manifest.parent / case.audio_path).resolve()


def _one_request(
    *,
    manifest: Path,
    case: CorpusCase,
    repetition: int,
    config: STTStageConfig,
    transcribe: TranscribeFn,
) -> dict:
    audio_path = _case_path(manifest, case)
    duration = audio_metadata(audio_path).duration_seconds
    started = time.perf_counter()
    response: Mapping[str, Any] | None = None
    error: str | None = None
    try:
        response = transcribe(audio_path, config)
        transcript = response.get("text")
        if not isinstance(transcript, str):
            raise STTClientError("response missing a string 'text' field")
        if not transcript.strip():
            raise STTClientError("response transcript was empty")
    except Exception as exc:  # noqa: BLE001 - failure is recorded per case
        transcript = ""
        error = "%s: %s" % (type(exc).__name__, exc)
    latency_ms = (time.perf_counter() - started) * 1000.0
    counts = error_counts(case.reference_text, transcript)
    result = {
        "case_id": case.id,
        "repetition": repetition,
        "category": case.category,
        "language": case.language,
        "audio_sha256": case.sha256,
        "audio_seconds": round(duration, 6),
        "reference_text": case.reference_text,
        "transcript": transcript,
        "reported_language": response.get("language") if response else None,
        "latency_ms": round(latency_ms, 6),
        "real_time_factor": round((latency_ms / 1000.0) / duration, 9),
        "normalized_wer": round(
            counts["word_edits"] / counts["reference_words"]
            if counts["reference_words"] else float(bool(transcript)),
            9,
        ),
        "raw_cer": round(
            counts["char_edits"] / counts["reference_chars"]
            if counts["reference_chars"] else float(bool(transcript)),
            9,
        ),
        **counts,
        "repetition_detected": repetition_detected(transcript),
        "failure": error,
    }
    return result


def _aggregate(records: list[dict]) -> dict:
    successful = [record for record in records if record["failure"] is None]
    total_word_edits = sum(record["word_edits"] for record in successful)
    total_reference_words = sum(record["reference_words"] for record in successful)
    total_char_edits = sum(record["char_edits"] for record in successful)
    total_reference_chars = sum(record["reference_chars"] for record in successful)
    return {
        "requests": len(records),
        "successful": len(successful),
        "failed": len(records) - len(successful),
        "repetition_flags": sum(bool(record["repetition_detected"]) for record in successful),
        "micro_wer": round(
            total_word_edits / total_reference_words if total_reference_words else 0.0,
            9,
        ),
        "micro_raw_cer": round(
            total_char_edits / total_reference_chars if total_reference_chars else 0.0,
            9,
        ),
        "latency_ms": {
            "p50": _rounded(_percentile((record["latency_ms"] for record in successful), 0.50)),
            "p95": _rounded(_percentile((record["latency_ms"] for record in successful), 0.95)),
            "max": _rounded(max((record["latency_ms"] for record in successful), default=None)),
        },
        "real_time_factor": {
            "p50": _rounded(
                _percentile((record["real_time_factor"] for record in successful), 0.50)
            ),
            "p95": _rounded(
                _percentile((record["real_time_factor"] for record in successful), 0.95)
            ),
        },
    }


def _rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _group_aggregates(records: list[dict]) -> dict:
    groups: dict[str, list[dict]] = {}
    for record in records:
        groups.setdefault(record["category"], []).append(record)
    return {key: _aggregate(value) for key, value in sorted(groups.items())}


def _language_probes(
    *,
    manifest: Path,
    cases: list[CorpusCase],
    config: STTStageConfig,
    transcribe: TranscribeFn,
    count: int,
) -> list[dict]:
    if count <= 0:
        return []
    human = [case for case in cases if case.category.startswith("librispeech-")]
    selected = human[:count]
    probe_config = STTStageConfig(
        base_url=config.base_url,
        model=config.model,
        api_key_env=config.api_key_env,
        timeout=config.timeout,
        stream=False,
        response_format=config.response_format or "json",
        language="auto",
        prompt=config.prompt,
    )
    probes = []
    for case in selected:
        record = _one_request(
            manifest=manifest,
            case=case,
            repetition=0,
            config=probe_config,
            transcribe=transcribe,
        )
        tag = record["reported_language"]
        record["expected_language"] = "en"
        record["language_tag_matches"] = (
            isinstance(tag, str) and tag.lower().replace("_", "-").split("-", 1)[0] == "en"
        )
        probes.append(record)
    return probes


def run_stt_benchmark(
    corpus_manifest: os.PathLike[str] | str,
    *,
    config: STTStageConfig,
    repetitions: int = 3,
    concurrency: int = 1,
    endpoint_identity: Mapping[str, Any] | None = None,
    auto_language_probe_count: int = 0,
    transcribe: TranscribeFn = transcribe_file,
) -> dict:
    """Run one cold request plus repeated warm requests over a validated corpus."""
    if repetitions < 1 or repetitions > 20:
        raise STTBenchmarkError("repetitions must be between 1 and 20")
    if concurrency < 1 or concurrency > 32:
        raise STTBenchmarkError("concurrency must be between 1 and 32")
    if auto_language_probe_count < 0 or auto_language_probe_count > 30:
        raise STTBenchmarkError("auto-language probe count must be between 0 and 30")
    _validate_endpoint(config)
    validated = validate_corpus(corpus_manifest)
    manifest = Path(validated["manifest"])
    cases: list[CorpusCase] = validated["cases"]
    cold = _one_request(
        manifest=manifest,
        case=cases[0],
        repetition=0,
        config=config,
        transcribe=transcribe,
    )
    work = [
        (case, repetition)
        for repetition in range(1, repetitions + 1)
        for case in cases
    ]
    started = time.perf_counter()
    if concurrency == 1:
        records = [
            _one_request(
                manifest=manifest,
                case=case,
                repetition=repetition,
                config=config,
                transcribe=transcribe,
            )
            for case, repetition in work
        ]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [
                executor.submit(
                    _one_request,
                    manifest=manifest,
                    case=case,
                    repetition=repetition,
                    config=config,
                    transcribe=transcribe,
                )
                for case, repetition in work
            ]
            records = [future.result() for future in futures]
    wall_seconds = time.perf_counter() - started
    records.sort(key=lambda record: (record["repetition"], record["case_id"]))
    aggregate = _aggregate(records)
    primary = [record for record in records if record["category"].startswith("librispeech-")]
    synthetic = [record for record in records if record["category"].startswith("synthetic-")]
    probes = _language_probes(
        manifest=manifest,
        cases=cases,
        config=config,
        transcribe=transcribe,
        count=auto_language_probe_count,
    )
    failed_records = [record for record in records if record["failure"] is not None]
    if cold["failure"] is not None:
        failed_records.insert(0, cold)
    failed_records.extend(probe for probe in probes if probe["failure"] is not None)
    failures = len(failed_records)
    repetition_flags = (
        int(aggregate["repetition_flags"])
        + int(bool(cold["repetition_detected"]))
        + sum(bool(probe["repetition_detected"]) for probe in probes)
    )
    complete = (
        failures == 0
        and len(records) == len(cases) * repetitions
        and len(probes) == auto_language_probe_count
    )
    evidence = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "complete": complete,
        "promotion": {
            "promoted": False,
            "human_gate_required": True,
            "evidence_scope": "stt-candidate-qualification",
        },
        "corpus": {
            "schema_version": validated["schema_version"],
            "manifest": str(manifest),
            "manifest_sha256": validated["manifest_sha256"],
            "case_count": validated["case_count"],
            "category_counts": validated["category_counts"],
            "duration_seconds": validated["duration_seconds"],
        },
        "endpoint": {
            "base_url": config.base_url,
            "model": config.model,
            "language_conditioning": config.language,
            "prompt": config.prompt,
            **dict(endpoint_identity or {}),
        },
        "schedule": {
            "cold_requests": 1,
            "repetitions": repetitions,
            "concurrency": concurrency,
            "expected_warm_requests": len(cases) * repetitions,
            "observed_warm_requests": len(records),
        },
        "cold_request": cold,
        "runs": records,
        "failures": failed_records,
        "summary": {
            "all": aggregate,
            "primary_human": _aggregate(primary),
            "synthetic_agent": _aggregate(synthetic),
            "categories": _group_aggregates(records),
            "wall_seconds": round(wall_seconds, 6),
            "requests_per_second": round(len(records) / wall_seconds, 6),
            "audio_seconds_per_wall_second": round(
                sum(record["audio_seconds"] for record in records) / wall_seconds,
                6,
            ),
        },
        "language_detection_probes": probes,
        "gate_observations": {
            "malformed_or_failed_runs": failures,
            "repetition_flags": repetition_flags,
            "language_probe_matches": sum(
                bool(probe["language_tag_matches"]) for probe in probes
            ),
            "language_probe_count": len(probes),
        },
    }
    return evidence
