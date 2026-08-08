#!/usr/bin/env python
"""RUN ON fakoli-dark (requires sm_120 GPU / real audio / running STT serves)

STT A/B preflight (anvil task T007): ``parakeet.cpp`` vs a vLLM-served
Whisper/Qwen3-ASR (see ``docs/findings/2026-07-04-hf-speech-to-speech-review.md``
s8's sm_120 component table). Starts each configured STT serve (or assumes
it's already up with ``--no-bring-up``), transcribes one sample utterance
against each over the SAME real wire call the STT stage makes
(:func:`anvil_serving.voice.stages.stt.transcribe_stream`), measures
wall-clock latency + a WER-sample against a reference transcript, and prints
a side-by-side comparison table (plus an optional ``--report`` JSON dump).

Feeds ``docs/findings/2026-07-voice-stt-ab.md``. First executed on
fakoli-dark on 2026-07-05 with parakeet.cpp and a disposable vLLM
Whisper-tiny transcription serve.

HONESTY NOTE: meaningful numbers require a real sm_120 box with parakeet.cpp
and/or a vLLM Whisper/Qwen3-ASR deployment reachable at the configured
``base_url``s (see ``examples/fakoli-dark/`` for the serving pattern this
repo already uses for the LLM tiers) -- there is no serves.toml entry for
either shipped in this repo yet; ``--candidate`` lets you point at whatever
you've actually deployed. Every number this script prints is real measurement
math (the same WER/latency computation
``anvil_serving/voice/benchmark.py`` uses, already unit-tested there) applied
to WHATEVER the configured endpoints answer with -- nothing here is a
canned/simulated result.

Guarded import: ``torch`` is imported ONLY to print informational local-GPU
context in the report (device name/compute capability) -- entirely optional,
and its absence (or absence of a CUDA device) never blocks the STT A/B run
itself, which is pure HTTP via ``urllib`` (see ``stages/stt.py``).

Usage::

    python scripts/voice/preflight_stt.py --report
    python scripts/voice/preflight_stt.py \\
        --candidate name=parakeet,base_url=http://127.0.0.1:30010/v1,model=tdt-0.6b-v3,stream=false \\
        --candidate name=vllm-whisper-tiny,base_url=http://127.0.0.1:30015/v1,model=whisper-tiny,stream=false \\
        --sample /path/to/utterance.wav --reference-text "the quick brown fox jumps over the lazy dog"
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# Bootstrap the repo root onto sys.path so this file works BOTH as a directly
# executed script (`python scripts/voice/preflight_stt.py`, where Python only
# puts the script's OWN directory -- scripts/voice -- on sys.path) and as an
# imported module (pytest's own `pythonpath = ["."]` already covers that
# case, so this insert is then a harmless no-op). Mirrors the same bootstrap
# in this directory's other scripts (``mini_validation.py``,
# ``realtime_sdk_client_demo.py``, ``local_loop_demo.py``) -- `scripts/` is
# deliberately NOT part of the installed wheel (see pyproject.toml), so it
# can't be relied on to already be importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from anvil_serving.voice.benchmark import DEFAULT_REFERENCE_TEXT, synth_sample_pcm, word_error_rate  # noqa: E402
from anvil_serving.voice.serves._common import ServeNotConfigured  # noqa: E402
from anvil_serving.voice.serves.stt import STTServe, STTServeConfig  # noqa: E402
from anvil_serving.voice.stages.stt import STTClientError, STTStageConfig, transcribe_stream  # noqa: E402
from scripts.voice._preflight_common import (  # noqa: E402
    container_info,
    endpoint_health,
    gpu_info,
)


# The review doc's s8 sm_120 table: parakeet.cpp is the "clean zero-drama
# path" (no torch); vLLM Whisper/Qwen3-ASR reuses the same cu128 image
# already running the LLM tiers. Neither serve is declared in this repo's
# example serves.toml yet (no --bring-up default target). Defaults match the
# measured fakoli-dark endpoints from docs/findings/2026-07-voice-stt-ab.md;
# override with --candidate for a different host or managed serve topology.
DEFAULT_REPORT_PATH = "docs/findings/2026-07-voice-stt-ab.json"

DEFAULT_CANDIDATES: List[Dict[str, Any]] = [
    {
        "name": "parakeet.cpp",
        "base_url": "http://127.0.0.1:30010/v1",
        "model": "tdt-0.6b-v3",
        "container_name": "parakeet-stt",
        "stream": False,
    },
    {
        "name": "vllm-whisper-tiny",
        "base_url": "http://127.0.0.1:30015/v1",
        "model": "whisper-tiny",
        "container_name": "anvil-stt-vllm-whisper-tiny-eager-test",
        "stream": False,
    },
]


@dataclass
class Candidate:
    name: str
    base_url: str
    model: str
    serve_name: Optional[str] = None
    api_key_env: Optional[str] = None
    container_name: Optional[str] = None
    stream: bool = True


def parse_bool(raw: str) -> bool:
    normalized = raw.strip().lower()
    if normalized in ("1", "true", "yes", "y", "on"):
        return True
    if normalized in ("0", "false", "no", "n", "off"):
        return False
    raise argparse.ArgumentTypeError("expected boolean value for stream=..., got %r" % raw)


def parse_candidate_arg(raw: str) -> Candidate:
    """Parse ``--candidate name=...,base_url=...,model=...[,stream=true|false]``."""
    fields: Dict[str, str] = {}
    for part in raw.split(","):
        if "=" not in part:
            raise argparse.ArgumentTypeError("--candidate field %r must be key=value" % part)
        key, _, value = part.partition("=")
        fields[key.strip()] = value.strip()
    for required in ("name", "base_url", "model"):
        if required not in fields:
            raise argparse.ArgumentTypeError("--candidate is missing required field %r" % required)
    allowed = {"name", "base_url", "model", "serve_name", "api_key_env", "container", "container_name", "stream"}
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise argparse.ArgumentTypeError("--candidate has unknown field(s): %s" % ", ".join(unknown))
    return Candidate(
        name=fields["name"],
        base_url=fields["base_url"],
        model=fields["model"],
        serve_name=fields.get("serve_name"),
        api_key_env=fields.get("api_key_env"),
        container_name=fields.get("container_name") or fields.get("container"),
        stream=parse_bool(fields.get("stream", "true")),
    )


def load_sample_pcm(path: str) -> "tuple[bytes, int]":
    """Read a mono 16-bit PCM WAV file; returns ``(pcm_bytes, sample_rate)``."""
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("--sample must be 16-bit PCM (got sampwidth=%d)" % w.getsampwidth())
        pcm = w.readframes(w.getnframes())
        if w.getnchannels() != 1:
            # Downmix by taking every Nth frame's first channel would need audioop
            # (removed in 3.13) or numpy; out of scope for a preflight script --
            # fail loudly instead of silently mangling the sample.
            raise ValueError(
                "--sample must be mono (got %d channels); provide a mono WAV" % w.getnchannels()
            )
        return pcm, w.getframerate()


def bring_up_and_wait(candidate: Candidate, *, ready_timeout: float, do_bring_up: bool) -> Dict[str, Any]:
    """Optionally bring up the candidate's serve, then wait for readiness.

    Returns a plain dict (never raises) so one mis-configured candidate never
    aborts the whole A/B run -- its transcription attempt below will simply
    fail with a clear transport error instead.
    """
    if not candidate.serve_name:
        health = endpoint_health(candidate.base_url, timeout=ready_timeout)
        container = container_info(candidate.container_name)
        ready = bool(health.get("ready")) and (container is None or container.get("status") == "running")
        result: Dict[str, Any] = {
            "docker_state": "raw-endpoint",
            "ready": ready,
            "detail": "raw endpoint health/container probe; no managed serve_name given",
            "health": health,
        }
        if container is not None:
            result["container"] = container
        return result
    serve = STTServe(STTServeConfig(base_url=candidate.base_url, model=candidate.model, serve_name=candidate.serve_name))
    try:
        if do_bring_up:
            serve.bring_up()
        readiness = serve.wait_ready(timeout=ready_timeout)
        return asdict(readiness)
    except ServeNotConfigured as exc:
        return {"docker_state": "unconfigured", "ready": False, "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 - bring_up() shells out to docker (FileNotFoundError,
        # a nonzero-exit surfaced as some other error, etc.) -- one candidate's bring-up
        # failure must not abort the whole A/B run, honoring this function's own "never
        # raises" docstring contract (matches mini_validation.validate_one_serve()).
        return {"docker_state": "error", "ready": False, "detail": "bring-up/wait_ready raised: %s" % exc}


def run_one(candidate: Candidate, pcm: bytes, sample_rate: int, reference_text: str, timeout: float) -> Dict[str, Any]:
    config = STTStageConfig(
        base_url=candidate.base_url, model=candidate.model, api_key_env=candidate.api_key_env,
        timeout=timeout, stream=candidate.stream,
    )
    t0 = time.perf_counter()
    hypothesis = ""
    error: Optional[str] = None
    try:
        for text, is_final in transcribe_stream(pcm, sample_rate, config):
            hypothesis = text
            if is_final:
                break
    except STTClientError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - preflight must record one candidate failure, not abort the A/B run
        error = str(exc)
    latency_ms = (time.perf_counter() - t0) * 1000.0
    wer = word_error_rate(reference_text, hypothesis) if error is None else None
    return {
        "name": candidate.name,
        "base_url": candidate.base_url,
        "model": candidate.model,
        "stream": candidate.stream,
        "latency_ms": round(latency_ms, 2),
        "hypothesis": hypothesis,
        "wer": wer,
        "error": error,
    }


def format_table(results: List[Dict[str, Any]]) -> str:
    header = "%-16s %-11s %-9s %-8s %-8s  hypothesis" % ("candidate", "wire-mode", "latency", "wer", "status")
    lines = [header, "-" * len(header)]
    for r in results:
        status = "ERROR" if r["error"] else "ok"
        wer_str = "n/a" if r["wer"] is None else ("%.3f" % r["wer"])
        mode = "stream" if r["stream"] else "json"
        lines.append(
            "%-16s %-11s %7.1fms %-8s %-8s  %s" % (
                r["name"], mode, r["latency_ms"], wer_str, status,
                (r["error"] or r["hypothesis"])[:60],
            )
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--candidate", action="append", type=parse_candidate_arg, dest="candidates",
        help="name=...,base_url=...,model=...[,serve_name=...][,api_key_env=...][,container_name=...]"
             "[,stream=true|false] "
             "(repeatable; defaults to the measured fakoli-dark parakeet.cpp + vLLM Whisper endpoints)",
    )
    p.add_argument("--sample", help="path to a mono 16-bit PCM WAV sample; default is a synthetic tone (NOT speech)")
    p.add_argument("--reference-text", default=None, help="reference transcript for the WER sample")
    p.add_argument("--timeout", type=float, default=120.0, help="per-request HTTP timeout (seconds)")
    p.add_argument("--ready-timeout", type=float, default=30.0, help="readiness-probe timeout per candidate (seconds)")
    p.add_argument("--no-bring-up", action="store_true", help="skip serve bring-up; assume candidates are already running")
    p.add_argument(
        "--report",
        nargs="?",
        const=DEFAULT_REPORT_PATH,
        help="write the full JSON report to this path; defaults to %s when no path is given" % DEFAULT_REPORT_PATH,
    )
    p.add_argument(
        "--allow-errors",
        action="store_true",
        help="return exit code 0 even when one or more candidates fail (for exploratory runs)",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    candidates = args.candidates or [Candidate(**c) for c in DEFAULT_CANDIDATES]

    if args.sample:
        pcm, sample_rate = load_sample_pcm(args.sample)
    else:
        print("preflight_stt: --sample not given; using a synthetic tone (NOT real speech) "
              "-- pass --sample for a meaningful WER number.", file=sys.stderr)
        pcm, sample_rate = synth_sample_pcm(), 16000

    reference_text = args.reference_text or DEFAULT_REFERENCE_TEXT

    results = []
    for candidate in candidates:
        readiness = bring_up_and_wait(candidate, ready_timeout=args.ready_timeout, do_bring_up=not args.no_bring_up)
        result = run_one(candidate, pcm, sample_rate, reference_text, args.timeout)
        result["readiness"] = readiness
        results.append(result)

    print(format_table(results))

    report = {"gpu": gpu_info(), "reference_text": reference_text, "sample": args.sample, "candidates": results}
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print("preflight_stt: wrote report to %s" % args.report)
    if not args.allow_errors and any(r["error"] for r in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
