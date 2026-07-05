#!/usr/bin/env python
"""RUN ON fakoli-dark — NOT YET EXECUTED (requires sm_120 GPU / real audio / running router)

TTS A/B preflight (anvil task T009): Kokoro-82M vs Orpheus-3B vs Qwen3-TTS
1.7B (see ``docs/findings/2026-07-04-hf-speech-to-speech-review.md`` s8's
sm_120 component table). Synthesizes one sample line of text against each
configured TTS serve over the SAME real wire call the TTS stage makes
(:func:`anvil_serving.voice.stages.tts.stream_speech`), measures TTFA
(time-to-first-audio-byte) + RTF (real-time factor: synth wall-clock /
seconds of audio produced), and prints a side-by-side comparison table (plus
an optional ``--report`` JSON dump).

Feeds ``docs/findings/2026-07-voice-tts-ab.md``.

WHAT THIS DOES **NOT** MEASURE: perceptual audio QUALITY (naturalness,
prosody, artifact rate). That needs a human listening test (or an automated
MOS-predictor model) -- deliberately out of scope for an automated preflight
script; the findings doc has a manual "quality" column for a human to fill in
after listening to the ``--capture``-saved WAV files.

HONESTY NOTE: this script has NEVER been run. It requires a real sm_120 box
with the candidate TTS serves reachable at the configured ``base_url``s.
Every number is real measurement math (the same TTFA/RTF arithmetic
``anvil_serving/voice/benchmark.py`` already unit-tests) applied to WHATEVER
the configured endpoints answer with.

Guarded import: ``torch`` is imported ONLY for informational local-GPU
context (see ``preflight_stt.py``'s identical guard) -- absence never blocks
the TTS A/B run itself (pure HTTP via ``urllib``, see ``stages/tts.py``).

Usage::

    python scripts/voice/preflight_tts.py --report docs/findings/tts-ab-run1.json --capture-dir /tmp/tts-ab
    python scripts/voice/preflight_tts.py \\
        --candidate name=orpheus,base_url=http://127.0.0.1:8093/v1,model=orpheus-3b,source_sample_rate=24000 \\
        --text "The quick brown fox jumps over the lazy dog."
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# Bootstrap the repo root onto sys.path so this file works BOTH as a directly
# executed script (`python scripts/voice/preflight_tts.py`, where Python only
# puts the script's OWN directory -- scripts/voice -- on sys.path) and as an
# imported module (pytest's own `pythonpath = ["."]` already covers that
# case, so this insert is then a harmless no-op). Mirrors the same bootstrap
# in this directory's other scripts (``mini_validation.py``,
# ``realtime_sdk_client_demo.py``, ``local_loop_demo.py``, ``preflight_stt.py``)
# -- `scripts/` is deliberately NOT part of the installed wheel (see
# pyproject.toml), so it can't be relied on to already be importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from anvil_serving.voice.stages.tts import TTSClientError, TTSStageConfig, stream_speech  # noqa: E402

try:
    import torch  # type: ignore
except Exception:  # noqa: BLE001 - any import-time failure just means "no GPU info available"
    torch = None

DEFAULT_TEXT = "The quick brown fox jumps over the lazy dog."

# The review doc's s8 sm_120 table. None of these are declared in this repo's
# example serves.toml yet -- override with --candidate for whatever you've
# actually deployed.
DEFAULT_CANDIDATES: List[Dict[str, Any]] = [
    {"name": "kokoro-82m", "base_url": "http://127.0.0.1:8091/v1", "model": "kokoro-82m", "source_sample_rate": 24000},
    {"name": "orpheus-3b", "base_url": "http://127.0.0.1:8093/v1", "model": "orpheus-3b", "source_sample_rate": 24000},
    {"name": "qwen3-tts-1.7b", "base_url": "http://127.0.0.1:8094/v1", "model": "qwen3-tts-1.7b", "source_sample_rate": 24000},
]


@dataclass
class Candidate:
    name: str
    base_url: str
    model: str
    source_sample_rate: int = 24000
    api_key_env: Optional[str] = None


def parse_candidate_arg(raw: str) -> Candidate:
    """Parse ``--candidate name=...,base_url=...,model=...[,source_sample_rate=...][,api_key_env=...]``."""
    fields: Dict[str, str] = {}
    for part in raw.split(","):
        if "=" not in part:
            raise argparse.ArgumentTypeError("--candidate field %r must be key=value" % part)
        key, _, value = part.partition("=")
        fields[key.strip()] = value.strip()
    for required in ("name", "base_url", "model"):
        if required not in fields:
            raise argparse.ArgumentTypeError("--candidate is missing required field %r" % required)
    if "source_sample_rate" in fields:
        fields["source_sample_rate"] = int(fields["source_sample_rate"])
    return Candidate(**fields)


def gpu_info() -> Dict[str, Any]:
    if torch is None:
        return {"available": False, "detail": "torch not importable in this environment (informational only)"}
    try:
        if not torch.cuda.is_available():
            return {"available": False, "detail": "torch imported but no CUDA device visible"}
        idx = torch.cuda.current_device()
        major, minor = torch.cuda.get_device_capability(idx)
        return {"available": True, "name": torch.cuda.get_device_name(idx), "capability": "sm_%d%d" % (major, minor)}
    except Exception as exc:  # noqa: BLE001 - informational probe must never crash the run
        return {"available": False, "detail": "torch/CUDA probe raised: %s" % exc}


def save_wav(path: str, pcm: bytes, sample_rate: int) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)


def run_one(
    candidate: Candidate, text: str, timeout: float, *, capture_dir: Optional[str],
) -> Dict[str, Any]:
    config = TTSStageConfig(
        base_url=candidate.base_url, model=candidate.model, api_key_env=candidate.api_key_env,
        timeout=timeout, source_sample_rate=candidate.source_sample_rate,
    )
    t0 = time.perf_counter()
    first_audio_time: Optional[float] = None
    total_bytes = 0
    chunks: List[bytes] = []
    error: Optional[str] = None
    try:
        for chunk in stream_speech(text, config):
            if first_audio_time is None:
                first_audio_time = time.perf_counter()
            total_bytes += len(chunk)
            if capture_dir:
                chunks.append(chunk)
    except TTSClientError as exc:
        error = str(exc)
    t_end = time.perf_counter()

    ttfa_ms = None if error else ((first_audio_time or t_end) - t0) * 1000.0
    synth_s = max(0.0, t_end - t0)
    audio_s = (total_bytes / 2) / config.source_sample_rate if total_bytes else 0.0
    rtf = (synth_s / audio_s) if audio_s > 0 else None

    capture_path = None
    if capture_dir and chunks:
        capture_path = str(Path(capture_dir) / ("%s.wav" % candidate.name))
        Path(capture_dir).mkdir(parents=True, exist_ok=True)
        save_wav(capture_path, b"".join(chunks), config.source_sample_rate)

    return {
        "name": candidate.name,
        "base_url": candidate.base_url,
        "model": candidate.model,
        "ttfa_ms": round(ttfa_ms, 2) if ttfa_ms is not None else None,
        "rtf": round(rtf, 4) if rtf is not None else None,
        "audio_bytes": total_bytes,
        "capture_path": capture_path,
        "quality": None,  # TODO(human listening pass): fill in after reviewing capture_path
        "error": error,
    }


def format_table(results: List[Dict[str, Any]]) -> str:
    header = "%-16s %-10s %-8s %-8s  capture" % ("candidate", "ttfa", "rtf", "status")
    lines = [header, "-" * len(header)]
    for r in results:
        status = "ERROR" if r["error"] else "ok"
        ttfa_str = "n/a" if r["ttfa_ms"] is None else ("%.1fms" % r["ttfa_ms"])
        rtf_str = "n/a" if r["rtf"] is None else ("%.3f" % r["rtf"])
        lines.append(
            "%-16s %-10s %-8s %-8s  %s" % (
                r["name"], ttfa_str, rtf_str, status, r["capture_path"] or (r["error"] or "-"),
            )
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--candidate", action="append", type=parse_candidate_arg, dest="candidates",
        help="name=...,base_url=...,model=...[,source_sample_rate=...][,api_key_env=...] "
             "(repeatable; defaults to kokoro/orpheus/qwen3-tts placeholders)",
    )
    p.add_argument("--text", default=DEFAULT_TEXT, help="sample line of text to synthesize")
    p.add_argument("--timeout", type=float, default=20.0, help="per-request HTTP timeout (seconds)")
    p.add_argument("--capture-dir", help="save each candidate's synthesized audio as a WAV here (for a human quality pass)")
    p.add_argument("--report", help="write the full JSON report to this path")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    candidates = args.candidates or [Candidate(**c) for c in DEFAULT_CANDIDATES]

    if not args.text.strip():
        print("preflight_tts: --text must be non-empty", file=sys.stderr)
        return 2

    results = [run_one(c, args.text, args.timeout, capture_dir=args.capture_dir) for c in candidates]
    print(format_table(results))

    report = {"gpu": gpu_info(), "text": args.text, "candidates": results}
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print("preflight_tts: wrote report to %s" % args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
