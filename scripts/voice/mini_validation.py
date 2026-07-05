#!/usr/bin/env python
"""RUN ON A 16GB MINI — NOT YET EXECUTED (requires a real low-memory box with
local STT/TTS serves + a network path to the fakoli-dark anvil router)

Validates the "small edge box, brain on the big rig" split (anvil task T016):
local STT+TTS serves run ON the 16GB Mini (per
``docs/findings/2026-07-04-hf-speech-to-speech-review.md``'s VRAM/RAM
guidance -- STT ~1-4GB, TTS ~0.5-7GB, both comfortably small), while the LLM
stage is routed over the tailnet to the anvil router on fakoli-dark (see the
saved memory note on Mini<->router tailnet binding -- the router publishes
its tailnet IP, not loopback). Records, per configured serve: container
startup wall-clock, peak memory (this driver process's own RSS -- see the
HONESTY NOTE below for what that does and doesn't cover), one end-to-end
TTFA/turn-latency sample (reusing ``anvil_serving.voice.benchmark``), and any
failure encountered at each step -- a partial/failed run still produces a
complete report rather than crashing, since "does it fail gracefully on a
memory-constrained box" is itself one of the things this script is measuring.

Feeds ``docs/findings/2026-07-voice-16gb-mini.md``.

HONESTY NOTE -- what "memory" means here: this script's own peak RSS (via
the stdlib ``resource`` module, POSIX-only) reflects the DRIVER PROCESS's
footprint, which is tiny and NOT the number that matters on a 16GB box -- the
STT/TTS engines run OUT-OF-PROCESS in their own containers. The metric that
actually matters is each container's OWN memory, which this script ALSO
attempts via ``docker stats --no-stream`` (best-effort, guarded: absent
Docker/permission errors degrade to ``None``, never a crash) against the
container names declared in the serves manifest (see
``anvil_serving.serves.load_manifest`` / ``anvil_serving/voice/serves/_common.py``).
On Windows (this dev sandbox), ``resource`` isn't importable at all -- that
branch is guarded and reports ``None`` with a clear reason rather than
raising, since the actual target platform for this script is the Mini
(macOS/Linux), not this box.

This script has NEVER been run.

Usage::

    python scripts/voice/mini_validation.py --config examples/voice/voice.example.toml \\
        --report docs/findings/mini-run1.json
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from anvil_serving import serves as generic_serves  # noqa: E402
from anvil_serving.voice import benchmark as voice_benchmark  # noqa: E402
from anvil_serving.voice import config as voice_config  # noqa: E402
from anvil_serving.voice.serves._common import ServeNotConfigured  # noqa: E402
from anvil_serving.voice.serves.stt import STTServe, STTServeConfig  # noqa: E402
from anvil_serving.voice.serves.tts import TTSServe, TTSServeConfig  # noqa: E402

# POSIX-only (macOS/Linux -- the Mini's actual platform); guarded so this
# module stays importable on Windows dev sandboxes (no `resource` module
# there at all -- not merely "not installed").
try:
    import resource  # type: ignore
except ImportError:
    resource = None


def peak_rss_mb() -> Optional[float]:
    """This driver PROCESS's own peak RSS in MB -- see the module HONESTY
    NOTE for why this is informational only, not the number that matters."""
    if resource is None:
        return None
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:  # noqa: BLE001 - informational only; never fail the run over this
        return None
    # ru_maxrss is bytes on macOS, KB everywhere else (Linux) -- normalize to MB.
    return round(usage / (1024.0 * 1024.0), 2) if sys.platform == "darwin" else round(usage / 1024.0, 2)


def container_mem_usage(container: str, *, _run: Callable[..., Any] = subprocess.run) -> Optional[str]:
    """``docker stats --no-stream`` MemUsage string for one container (e.g.
    ``"512MiB / 16GiB"``), or ``None`` if docker/the container isn't
    reachable -- never raises (mirrors ``anvil_serving/serves.py``'s
    ``docker_state``'s own guarded-subprocess convention)."""
    try:
        r = _run(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip() or None


@dataclass
class ServeValidation:
    kind: str
    serve_name: str
    bring_up_ok: bool
    startup_s: Optional[float]
    ready: Optional[bool]
    container_mem: Optional[str]
    error: Optional[str]


def validate_one_serve(
    kind: str, serve_config, serve_obj, *, ready_timeout: float, serves_manifest_path: Optional[str],
) -> ServeValidation:
    serve_name = serve_config.serve_name
    t0 = time.perf_counter()
    error = None
    ready = None
    try:
        serve_obj.bring_up()
        readiness = serve_obj.wait_ready(timeout=ready_timeout)
        ready = readiness.ready
    except ServeNotConfigured as exc:
        error = "not configured: %s" % exc
    except Exception as exc:  # noqa: BLE001 - a single serve's failure must not abort the whole validation run
        error = "bring-up/wait_ready raised: %s" % exc
    startup_s = round(time.perf_counter() - t0, 2)

    container_mem = None
    try:
        serves = generic_serves.load_manifest(serves_manifest_path or generic_serves.DEFAULT_MANIFEST)
        entry = next((s for s in serves if s["name"] == serve_name or s["container"] == serve_name), None)
        if entry:
            container_mem = container_mem_usage(entry["container"])
    except (FileNotFoundError, ServeNotConfigured):
        pass

    return ServeValidation(
        kind=kind, serve_name=serve_name, bring_up_ok=error is None,
        startup_s=startup_s, ready=ready, container_mem=container_mem, error=error,
    )


def run_validation(
    data: Dict[str, Any], *, ready_timeout: float, serves_manifest_path: Optional[str],
) -> Dict[str, Any]:
    voice = data["voice"]
    rss_before = peak_rss_mb()

    stt_cfg = STTServeConfig(base_url=voice["stt"]["base_url"], model=voice["stt"]["model"])
    tts_cfg = TTSServeConfig(base_url=voice["tts"]["base_url"], model=voice["tts"]["model"])

    stt_validation = validate_one_serve(
        "stt", stt_cfg, STTServe(stt_cfg), ready_timeout=ready_timeout, serves_manifest_path=serves_manifest_path,
    )
    tts_validation = validate_one_serve(
        "tts", tts_cfg, TTSServe(tts_cfg), ready_timeout=ready_timeout, serves_manifest_path=serves_manifest_path,
    )

    benchmark_result: Optional[Dict[str, Any]] = None
    benchmark_error: Optional[str] = None
    try:
        # LLM stage is routed to fakoli-dark's router (whatever [voice.llm]
        # in the manifest declares -- e.g. the tailnet address, per the saved
        # Mini<->router tailnet-binding note); STT/TTS calls stay local. This
        # makes LIVE network calls -- see run_benchmark_from_manifest's own
        # honesty note in anvil_serving/voice/benchmark.py.
        benchmark_result = voice_benchmark.run_benchmark_from_manifest(data)
    except Exception as exc:  # noqa: BLE001 - a benchmark failure is itself a reportable failure mode, not a crash
        benchmark_error = str(exc)

    rss_after = peak_rss_mb()

    return {
        "platform": sys.platform,
        "driver_process_rss_before_mb": rss_before,
        "driver_process_peak_rss_mb": rss_after,
        "driver_process_rss_note": (
            "resource module unavailable on this platform (expected on Windows; "
            "target platform is macOS/Linux)" if rss_after is None else
            "this is the DRIVER SCRIPT's own footprint, not the STT/TTS containers' -- see container_mem per serve"
        ),
        "stt": asdict(stt_validation),
        "tts": asdict(tts_validation),
        "llm_endpoint": voice["llm"]["base_url"],
        "benchmark": benchmark_result,
        "benchmark_error": benchmark_error,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def build_parser():
    import argparse

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=voice_config.DEFAULT_CONFIG, help="voice manifest TOML (STT/LLM/TTS endpoints)")
    p.add_argument("--serves-manifest", default=None, help="serves.toml path (container names for docker stats); default ./serves.toml")
    p.add_argument("--ready-timeout", type=float, default=60.0, help="per-serve readiness-probe timeout, seconds")
    p.add_argument("--report", default=None, help="write the full JSON report to this path")
    return p


def append_finding_row(row: str) -> None:
    findings_doc = _REPO_ROOT / "docs" / "findings" / "2026-07-voice-16gb-mini.md"
    try:
        with open(findings_doc, "a", encoding="utf-8") as f:
            f.write(row.rstrip("\n") + "\n")
    except OSError as exc:
        print("mini_validation: could not append to %s: %s" % (findings_doc, exc), file=sys.stderr)


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        data = voice_config.load_manifest(args.config)
    except voice_config.ConfigError as exc:
        print("mini_validation: %s" % exc, file=sys.stderr)
        return 2

    result = run_validation(data, ready_timeout=args.ready_timeout, serves_manifest_path=args.serves_manifest)
    print(json.dumps(result, indent=2))

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        print("mini_validation: wrote report to %s" % args.report)

    ttfa = (result.get("benchmark") or {}).get("ttfa_ms") if result.get("benchmark") else None
    append_finding_row(
        "| %s | %s | %s | %s | %s | %s |" % (
            result["measured_at"],
            "ok" if result["stt"]["bring_up_ok"] else ("FAIL: %s" % result["stt"]["error"]),
            "ok" if result["tts"]["bring_up_ok"] else ("FAIL: %s" % result["tts"]["error"]),
            ttfa if ttfa is not None else (result.get("benchmark_error") or "n/a"),
            result["driver_process_peak_rss_mb"],
            args.report or "-",
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
