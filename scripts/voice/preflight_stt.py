#!/usr/bin/env python
"""RUN ON fakoli-dark — NOT YET EXECUTED (requires sm_120 GPU / real audio / running router)

STT A/B preflight (anvil task T007): ``parakeet.cpp`` vs a vLLM-served
Whisper/Qwen3-ASR (see ``docs/findings/2026-07-04-hf-speech-to-speech-review.md``
s8's sm_120 component table). Starts each configured STT serve (or assumes
it's already up with ``--no-bring-up``), transcribes one sample utterance
against each over the SAME real wire call the STT stage makes
(:func:`anvil_serving.voice.stages.stt.transcribe_stream`), measures
wall-clock latency + a WER-sample against a reference transcript, and prints
a side-by-side comparison table (plus an optional ``--report`` JSON dump).

Feeds ``docs/findings/2026-07-voice-stt-ab.md``.

HONESTY NOTE: this script has NEVER been run. It requires a real sm_120 box
with parakeet.cpp and/or a vLLM Whisper/Qwen3-ASR deployment reachable at the
configured ``base_url``s (see ``examples/fakoli-dark/`` for the serving
pattern this repo already uses for the LLM tiers) -- there is no serves.toml
entry for either shipped in this repo yet; ``--candidate`` lets you point at
whatever you've actually deployed. Every number this script would print is
real measurement math (the same WER/latency computation
``anvil_serving/voice/benchmark.py`` uses, already unit-tested there) applied
to WHATEVER the configured endpoints answer with -- nothing here is a
canned/simulated result.

Guarded import: ``torch`` is imported ONLY to print informational local-GPU
context in the report (device name/compute capability) -- entirely optional,
and its absence (or absence of a CUDA device) never blocks the STT A/B run
itself, which is pure HTTP via ``urllib`` (see ``stages/stt.py``).

Usage::

    python scripts/voice/preflight_stt.py --report docs/findings/stt-ab-run1.json
    python scripts/voice/preflight_stt.py \\
        --candidate name=parakeet,base_url=http://127.0.0.1:8090/v1,model=parakeet-tdt-0.6b-v3 \\
        --candidate name=qwen3-asr,base_url=http://127.0.0.1:8092/v1,model=Qwen3-ASR-1.7B \\
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

# Purely informational (local GPU context in the printed/--report output).
# Guarded so importing this module -- or running it with --help -- never
# requires a GPU or a CUDA-enabled torch build to even be installed.
try:
    import torch  # type: ignore
except Exception:  # noqa: BLE001 - any import-time failure just means "no GPU info available"
    torch = None


# The review doc's s8 sm_120 table: parakeet.cpp is the "clean zero-drama
# path" (no torch); vLLM Whisper/Qwen3-ASR reuses the same cu128 image
# already running the LLM tiers. Neither serve is declared in this repo's
# example serves.toml yet (no --bring-up default target) -- these are
# starting points; override with --candidate, or point --serves-manifest at
# a real serves.toml that declares them.
DEFAULT_CANDIDATES: List[Dict[str, str]] = [
    {"name": "parakeet.cpp", "base_url": "http://127.0.0.1:8090/v1", "model": "parakeet-tdt-0.6b-v3", "serve_name": "stt-parakeet"},
    {"name": "vllm-whisper", "base_url": "http://127.0.0.1:8092/v1", "model": "whisper-large-v3", "serve_name": "stt-vllm-whisper"},
]


@dataclass
class Candidate:
    name: str
    base_url: str
    model: str
    serve_name: Optional[str] = None
    api_key_env: Optional[str] = None


def parse_candidate_arg(raw: str) -> Candidate:
    """Parse ``--candidate name=...,base_url=...,model=...[,serve_name=...][,api_key_env=...]``."""
    fields: Dict[str, str] = {}
    for part in raw.split(","):
        if "=" not in part:
            raise argparse.ArgumentTypeError("--candidate field %r must be key=value" % part)
        key, _, value = part.partition("=")
        fields[key.strip()] = value.strip()
    for required in ("name", "base_url", "model"):
        if required not in fields:
            raise argparse.ArgumentTypeError("--candidate is missing required field %r" % required)
    return Candidate(**fields)


def gpu_info() -> Dict[str, Any]:
    """Best-effort local-GPU context for the report; never raises."""
    if torch is None:
        return {"available": False, "detail": "torch not importable in this environment (informational only)"}
    try:
        if not torch.cuda.is_available():
            return {"available": False, "detail": "torch imported but no CUDA device visible"}
        idx = torch.cuda.current_device()
        major, minor = torch.cuda.get_device_capability(idx)
        return {
            "available": True,
            "name": torch.cuda.get_device_name(idx),
            "capability": "sm_%d%d" % (major, minor),
        }
    except Exception as exc:  # noqa: BLE001 - informational probe must never crash the run
        return {"available": False, "detail": "torch/CUDA probe raised: %s" % exc}


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
        return {"docker_state": "n/a", "ready": None, "detail": "no serve_name given; assuming already reachable"}
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
        timeout=timeout, stream=True,
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
    latency_ms = (time.perf_counter() - t0) * 1000.0
    wer = word_error_rate(reference_text, hypothesis) if error is None else None
    return {
        "name": candidate.name,
        "base_url": candidate.base_url,
        "model": candidate.model,
        "latency_ms": round(latency_ms, 2),
        "hypothesis": hypothesis,
        "wer": wer,
        "error": error,
    }


def format_table(results: List[Dict[str, Any]]) -> str:
    header = "%-16s %-9s %-8s %-8s  hypothesis" % ("candidate", "latency", "wer", "status")
    lines = [header, "-" * len(header)]
    for r in results:
        status = "ERROR" if r["error"] else "ok"
        wer_str = "n/a" if r["wer"] is None else ("%.3f" % r["wer"])
        lines.append(
            "%-16s %7.1fms %-8s %-8s  %s" % (
                r["name"], r["latency_ms"], wer_str, status,
                (r["error"] or r["hypothesis"])[:60],
            )
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--candidate", action="append", type=parse_candidate_arg, dest="candidates",
        help="name=...,base_url=...,model=...[,serve_name=...][,api_key_env=...] "
             "(repeatable; defaults to parakeet.cpp + vllm-whisper placeholders)",
    )
    p.add_argument("--sample", help="path to a mono 16-bit PCM WAV sample; default is a synthetic tone (NOT speech)")
    p.add_argument("--reference-text", default=None, help="reference transcript for the WER sample")
    p.add_argument("--timeout", type=float, default=15.0, help="per-request HTTP timeout (seconds)")
    p.add_argument("--ready-timeout", type=float, default=30.0, help="readiness-probe timeout per candidate (seconds)")
    p.add_argument("--no-bring-up", action="store_true", help="skip serve bring-up; assume candidates are already running")
    p.add_argument("--report", help="write the full JSON report to this path")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
