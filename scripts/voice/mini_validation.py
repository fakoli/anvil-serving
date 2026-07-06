#!/usr/bin/env python
"""RUN ON A 16GB MINI (or another host for an honest negative control).

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

The task acceptance command is intentionally terse:

    python scripts/voice/mini_validation.py --report

With no explicit report path, ``--report`` writes
``docs/findings/2026-07-voice-16gb-mini.json`` and appends a row to the
matching findings doc.

Usage::

    python scripts/voice/mini_validation.py --report
    python scripts/voice/mini_validation.py --config examples/voice/fakoli-dark.toml \\
        --serves-manifest ./serves.toml --report /tmp/mini-run1.json
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.parse
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
from scripts.voice.local_loop_demo import route_decision_probe  # noqa: E402

_FAKOLI_DARK_CONFIG = _REPO_ROOT / "examples" / "voice" / "fakoli-dark.toml"
DEFAULT_CONFIG = str(_FAKOLI_DARK_CONFIG if _FAKOLI_DARK_CONFIG.is_file() else Path(voice_config.DEFAULT_CONFIG))
DEFAULT_REPORT_PATH_REL = "docs/findings/2026-07-voice-16gb-mini.json"
DEFAULT_REPORT_PATH = str(_REPO_ROOT / DEFAULT_REPORT_PATH_REL)
FINDINGS_DOC = _REPO_ROOT / "docs" / "findings" / "2026-07-voice-16gb-mini.md"
DEFAULT_TARGET_HOST_PATTERN = r"(?i)(^|[^a-z0-9])(fakoli[-_. ]?mini|mini)([^a-z0-9]|$)"
DEFAULT_FAKOLI_DARK_HOSTS = ("100.87.34.66", "fakoli-dark")

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


def _gb(num_bytes: float) -> float:
    return round(num_bytes / (1024.0 ** 3), 2)


def _run_probe(argv: List[str], *, timeout: float = 5.0, _run: Callable[..., Any] = subprocess.run) -> Dict[str, Any]:
    try:
        proc = _run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        return {"ok": False, "detail": "%s not found" % argv[0], "error": str(exc)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "%s timed out" % argv[0]}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def _windows_memory_snapshot_ctypes() -> Dict[str, Any]:
    import ctypes

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("GlobalMemoryStatusEx failed")
    return {
        "total_gb": _gb(float(status.ullTotalPhys)),
        "available_gb": _gb(float(status.ullAvailPhys)),
        "used_gb": _gb(float(status.ullTotalPhys - status.ullAvailPhys)),
        "source": "GlobalMemoryStatusEx",
    }


def host_memory_snapshot(*, _run: Callable[..., Any] = subprocess.run) -> Dict[str, Any]:
    """Best-effort host memory snapshot in GB.

    Uses only stdlib and platform tools. Returns a structured failure instead
    of raising because a missing memory probe is itself useful in a hardware
    validation report.
    """
    snapshot: Dict[str, Any] = {
        "hostname": platform.node(),
        "platform": sys.platform,
        "total_gb": None,
        "available_gb": None,
        "used_gb": None,
        "source": None,
        "error": None,
    }
    try:
        if sys.platform.startswith("linux"):
            meminfo: Dict[str, int] = {}
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition(":")
                parts = value.strip().split()
                if parts and parts[0].isdigit():
                    meminfo[key] = int(parts[0]) * 1024
            total = meminfo.get("MemTotal")
            available = meminfo.get("MemAvailable")
            if total is None:
                raise ValueError("/proc/meminfo missing MemTotal")
            if available is None:
                available = sum(meminfo.get(k, 0) for k in ("MemFree", "Buffers", "Cached"))
            snapshot.update({
                "total_gb": _gb(total),
                "available_gb": _gb(float(available)),
                "used_gb": _gb(float(total - available)),
                "source": "/proc/meminfo",
            })
            return snapshot

        if sys.platform == "darwin":
            total_probe = _run_probe(["sysctl", "-n", "hw.memsize"], _run=_run)
            if not total_probe.get("ok"):
                raise ValueError(total_probe.get("stderr") or total_probe.get("detail") or "sysctl failed")
            total = int(str(total_probe.get("stdout", "0")).strip())
            vm_probe = _run_probe(["vm_stat"], _run=_run)
            available = None
            if vm_probe.get("ok"):
                text = str(vm_probe.get("stdout", ""))
                page_size_match = re.search(r"page size of (\d+) bytes", text)
                page_size = int(page_size_match.group(1)) if page_size_match else 4096
                page_counts: Dict[str, int] = {}
                for line in text.splitlines():
                    match = re.match(r"Pages ([^:]+):\s+(\d+)\.", line.strip())
                    if match:
                        page_counts[match.group(1).lower()] = int(match.group(2))
                available_pages = (
                    page_counts.get("free", 0)
                    + page_counts.get("inactive", 0)
                    + page_counts.get("speculative", 0)
                )
                available = available_pages * page_size
            snapshot.update({
                "total_gb": _gb(total),
                "available_gb": _gb(float(available)) if available is not None else None,
                "used_gb": _gb(float(total - available)) if available is not None else None,
                "source": "sysctl/vm_stat",
            })
            return snapshot

        if sys.platform.startswith("win"):
            probe = _run_probe(
                ["wmic", "OS", "get", "FreePhysicalMemory,TotalVisibleMemorySize", "/Value"],
                _run=_run,
            )
            if not probe.get("ok"):
                snapshot.update(_windows_memory_snapshot_ctypes())
                return snapshot
            values: Dict[str, int] = {}
            for line in str(probe.get("stdout", "")).splitlines():
                key, _, value = line.partition("=")
                if key and value.strip().isdigit():
                    values[key] = int(value.strip()) * 1024
            total = values.get("TotalVisibleMemorySize")
            free = values.get("FreePhysicalMemory")
            if total is None:
                snapshot.update(_windows_memory_snapshot_ctypes())
                return snapshot
            snapshot.update({
                "total_gb": _gb(total),
                "available_gb": _gb(float(free)) if free is not None else None,
                "used_gb": _gb(float(total - free)) if free is not None else None,
                "source": "wmic",
            })
            return snapshot

        snapshot["error"] = "unsupported platform for host memory probe"
        return snapshot
    except Exception as exc:  # noqa: BLE001 - report probe failures, never crash
        snapshot["error"] = str(exc)
        return snapshot


def is_16gb_class(memory: Dict[str, Any]) -> Optional[bool]:
    total = memory.get("total_gb")
    if not isinstance(total, (int, float)):
        return None
    return 14.0 <= float(total) <= 18.5


def _url_host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def is_loopback_url(url: str) -> bool:
    host = _url_host(url)
    return host == "127.0.0.1" or host == "::1"


def host_matches_target(hostname: str, pattern: str) -> bool:
    try:
        return re.search(pattern, hostname or "") is not None
    except re.error:
        return False


def llm_endpoint_is_fakoli_dark(url: str, llm: Dict[str, Any]) -> bool:
    host = _url_host(url)
    expected = llm.get("expected_endpoint_host")
    expected_hosts = [str(expected).lower()] if expected else list(DEFAULT_FAKOLI_DARK_HOSTS)
    return host in expected_hosts


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


def container_mem_for_serve(serve_name: str, serves_manifest_path: Optional[str]) -> Optional[str]:
    try:
        serves = generic_serves.load_manifest(serves_manifest_path or generic_serves.DEFAULT_MANIFEST)
        entry = next((s for s in serves if s["name"] == serve_name or s["container"] == serve_name), None)
        if entry:
            return container_mem_usage(entry["container"])
    except (FileNotFoundError, ServeNotConfigured):
        return None
    return None


@dataclass
class ServeValidation:
    kind: str
    serve_name: str
    bring_up_ok: bool
    bring_up_returncode: Optional[int]
    startup_s: Optional[float]
    ready: Optional[bool]
    docker_state: Optional[str]
    readiness_detail: Optional[str]
    container_mem: Optional[str]
    container_mem_after_benchmark: Optional[str]
    error: Optional[str]


def validate_one_serve(
    kind: str, serve_config, serve_obj, *, ready_timeout: float, serves_manifest_path: Optional[str],
) -> ServeValidation:
    serve_name = serve_config.serve_name
    t0 = time.perf_counter()
    error = None
    ready = None
    docker_state = None
    readiness_detail = None
    bring_up_returncode = None
    try:
        bring_up_result = serve_obj.bring_up()
        if isinstance(bring_up_result, int):
            bring_up_returncode = bring_up_result
            if bring_up_result != 0:
                error = "bring_up returned nonzero exit code %s" % bring_up_result
    except ServeNotConfigured as exc:
        error = "not configured: %s" % exc
    except Exception as exc:  # noqa: BLE001 - a single serve's failure must not abort the whole validation run
        error = "bring-up/wait_ready raised: %s" % exc
    startup_s = round(time.perf_counter() - t0, 2)

    try:
        readiness = serve_obj.wait_ready(timeout=ready_timeout)
        ready = readiness.ready
        docker_state = readiness.docker_state
        readiness_detail = readiness.detail
    except Exception as exc:  # noqa: BLE001 - readiness failure is report data
        ready = False
        readiness_detail = "wait_ready raised: %s" % exc
        if error is None:
            error = readiness_detail

    container_mem = container_mem_for_serve(serve_name, serves_manifest_path)

    return ServeValidation(
        kind=kind, serve_name=serve_name,
        bring_up_ok=error is None and (bring_up_returncode in (None, 0)),
        bring_up_returncode=bring_up_returncode,
        startup_s=startup_s, ready=ready, docker_state=docker_state,
        readiness_detail=readiness_detail, container_mem=container_mem,
        container_mem_after_benchmark=None, error=error,
    )


def build_verdict(result: Dict[str, Any]) -> Dict[str, Any]:
    reasons: List[str] = []
    failure_modes: List[str] = []

    host_memory = result.get("host_memory_after_load") or {}
    is_target = is_16gb_class(host_memory)
    if is_target is False:
        failure_modes.append("host_not_16gb_class")
        reasons.append("host is not a 16GB-class Mini target (total_gb=%s)" % host_memory.get("total_gb"))
    elif is_target is None:
        failure_modes.append("host_memory_unmeasured")
        reasons.append("host total memory could not be measured")
    if not result.get("host_matches_expected_mini"):
        failure_modes.append("host_not_expected_mini")

    if not result.get("stt_local_endpoint"):
        failure_modes.append("stt_not_local_loopback")
    if not result.get("tts_local_endpoint"):
        failure_modes.append("tts_not_local_loopback")
    if result.get("stt", {}).get("bring_up_ok") is False:
        failure_modes.append("stt_bring_up_failed")
    if result.get("tts", {}).get("bring_up_ok") is False:
        failure_modes.append("tts_bring_up_failed")
    if not result.get("stt", {}).get("ready"):
        failure_modes.append("stt_not_ready")
    if not result.get("tts", {}).get("ready"):
        failure_modes.append("tts_not_ready")
    route_proof = result.get("route_proof") or {}
    if (
        not result.get("llm_routed_remote")
        or not result.get("llm_endpoint_is_fakoli_dark")
        or not route_proof.get("ok")
    ):
        failure_modes.append("llm_not_routed_to_remote_fakoli_dark")
    if not result.get("llm_auth_env"):
        failure_modes.append("llm_auth_env_missing")
    elif not result.get("llm_auth_env_present"):
        failure_modes.append("llm_auth_token_unset")
    if result.get("benchmark_error"):
        failure_modes.append("benchmark_error")
    elif not result.get("benchmark"):
        failure_modes.append("benchmark_missing")
    else:
        benchmark = result.get("benchmark") or {}
        if not benchmark.get("tts_first_audio_observed") or not benchmark.get("tts_output_bytes"):
            failure_modes.append("first_audio_missing")
        if not isinstance(benchmark.get("ttfa_ms"), (int, float)):
            failure_modes.append("ttfa_missing")
        if not isinstance(benchmark.get("turn_latency_ms"), (int, float)):
            failure_modes.append("turn_latency_missing")
        if not isinstance(benchmark.get("tts_rtf"), (int, float)):
            failure_modes.append("tts_rtf_missing")

    if result.get("stt", {}).get("ready") and not result.get("stt", {}).get("container_mem_after_benchmark"):
        failure_modes.append("stt_container_memory_missing")
    if result.get("tts", {}).get("ready") and not result.get("tts", {}).get("container_mem_after_benchmark"):
        failure_modes.append("tts_container_memory_missing")

    available = host_memory.get("available_gb")
    if isinstance(available, (int, float)) and available < 2.0:
        reasons.append("host available memory after load below 2GB")

    if failure_modes:
        status = "unsupported"
    elif is_target is True and not reasons:
        status = "supported"
    else:
        status = "experimental"

    return {
        "status": status,
        "failure_modes": failure_modes,
        "reasons": reasons or (
            ["one or more required Mini validation proof gates failed"]
            if failure_modes else ["all required Mini validation checks passed"]
        ),
    }


def run_validation(
    data: Dict[str, Any], *, ready_timeout: float, serves_manifest_path: Optional[str],
    target_host_pattern: str,
) -> Dict[str, Any]:
    voice = data["voice"]
    rss_before = peak_rss_mb()
    host_before = host_memory_snapshot()

    stt_cfg = STTServeConfig(
        base_url=voice["stt"]["base_url"],
        model=voice["stt"]["model"],
        manifest_path=serves_manifest_path,
    )
    tts_cfg = TTSServeConfig(
        base_url=voice["tts"]["base_url"],
        model=voice["tts"]["model"],
        manifest_path=serves_manifest_path,
    )

    stt_validation = validate_one_serve(
        "stt", stt_cfg, STTServe(stt_cfg), ready_timeout=ready_timeout, serves_manifest_path=serves_manifest_path,
    )
    tts_validation = validate_one_serve(
        "tts", tts_cfg, TTSServe(tts_cfg), ready_timeout=ready_timeout, serves_manifest_path=serves_manifest_path,
    )
    stt_result = asdict(stt_validation)
    tts_result = asdict(tts_validation)
    host_after_serves_ready = host_memory_snapshot()
    route_proof = route_decision_probe(data, prompt="voice Mini validation route proof")

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

    stt_result["container_mem_after_benchmark"] = container_mem_for_serve(
        stt_result["serve_name"], serves_manifest_path,
    )
    tts_result["container_mem_after_benchmark"] = container_mem_for_serve(
        tts_result["serve_name"], serves_manifest_path,
    )
    host_after_load = host_memory_snapshot()
    rss_after = peak_rss_mb()
    hostname = platform.node()
    llm = voice["llm"]
    llm_auth_env = llm.get("api_key_env")

    result = {
        "platform": sys.platform,
        "hostname": hostname,
        "target_host_pattern": target_host_pattern,
        "host_matches_expected_mini": host_matches_target(hostname, target_host_pattern),
        "host_memory_before": host_before,
        "host_memory_after_serves_ready": host_after_serves_ready,
        "host_memory_after_load": host_after_load,
        "host_is_16gb_class": is_16gb_class(host_after_load),
        "driver_process_rss_before_mb": rss_before,
        "driver_process_peak_rss_mb": rss_after,
        "driver_process_rss_note": (
            "resource module unavailable on this platform (expected on Windows; "
            "target platform is macOS/Linux)" if rss_after is None else
            "this is the DRIVER SCRIPT's own footprint, not the STT/TTS containers' -- see container_mem per serve"
        ),
        "stt": stt_result,
        "tts": tts_result,
        "llm_endpoint": llm["base_url"],
        "llm_endpoint_host": _url_host(llm["base_url"]),
        "llm_endpoint_is_fakoli_dark": llm_endpoint_is_fakoli_dark(llm["base_url"], llm),
        "llm_routed_remote": not is_loopback_url(llm["base_url"]),
        "llm_auth_env": llm_auth_env,
        "llm_auth_env_present": bool(llm_auth_env and os.environ.get(llm_auth_env)),
        "route_proof": route_proof,
        "stt_local_endpoint": is_loopback_url(voice["stt"]["base_url"]),
        "tts_local_endpoint": is_loopback_url(voice["tts"]["base_url"]),
        "benchmark": benchmark_result,
        "benchmark_error": benchmark_error,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    result["verdict"] = build_verdict(result)
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=DEFAULT_CONFIG, help="voice manifest TOML (STT/LLM/TTS endpoints)")
    p.add_argument("--serves-manifest", default=None, help="serves.toml path (container names for docker stats); default ./serves.toml")
    p.add_argument("--ready-timeout", type=float, default=60.0, help="per-serve readiness-probe timeout, seconds")
    p.add_argument(
        "--target-host-pattern",
        default=os.environ.get("ANVIL_VOICE_MINI_HOST_PATTERN", DEFAULT_TARGET_HOST_PATTERN),
        help="case-insensitive regex the host name must match for target Mini proof",
    )
    p.add_argument(
        "--report",
        nargs="?",
        const=DEFAULT_REPORT_PATH,
        default=None,
        help="write the full JSON report to this path; defaults to %s when no path is given" % DEFAULT_REPORT_PATH_REL,
    )
    p.add_argument(
        "--allow-unsupported",
        action="store_true",
        help="return exit code 0 even when the verdict is unsupported (for exploratory negative-control runs)",
    )
    return p


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    return str(value)


def _serve_cell(result: Dict[str, Any]) -> str:
    ready = "ready" if result.get("ready") else "not-ready"
    mem = result.get("container_mem_after_benchmark") or result.get("container_mem") or "mem=n/a"
    err = result.get("error")
    return "%s; %s%s" % (ready, mem, ("; %s" % err) if err else "")


def _failure_cell(result: Dict[str, Any]) -> str:
    verdict = result.get("verdict") or {}
    modes = verdict.get("failure_modes") or []
    reasons = verdict.get("reasons") or []
    return "; ".join(modes + reasons)


def append_finding_row(row: str) -> bool:
    """Insert one markdown table row into the T016 findings doc."""
    try:
        row = row.rstrip("\n")
        if not FINDINGS_DOC.exists():
            print("mini_validation: findings doc does not exist: %s" % FINDINGS_DOC, file=sys.stderr)
            return False

        lines = FINDINGS_DOC.read_text(encoding="utf-8").splitlines()
        try:
            session_idx = lines.index("## Session log")
        except ValueError:
            print("mini_validation: findings doc has no Session log heading", file=sys.stderr)
            return False
        header_idx = next(
            (
                i for i in range(session_idx + 1, len(lines))
                if lines[i].startswith("| timestamp (UTC) |")
            ),
            None,
        )
        if header_idx is None or header_idx + 1 >= len(lines) or not lines[header_idx + 1].startswith("|---"):
            print("mini_validation: findings doc has no session-log markdown table", file=sys.stderr)
            return False

        table_end = header_idx + 2
        while table_end < len(lines) and lines[table_end].startswith("|"):
            if lines[table_end].startswith("| _TBD_ |"):
                lines.insert(table_end, row)
                FINDINGS_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return True
            table_end += 1
        lines.insert(table_end, row)
        FINDINGS_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    except OSError as exc:
        print("mini_validation: could not append to %s: %s" % (FINDINGS_DOC, exc), file=sys.stderr)
        return False


def resolve_report_path(report: str) -> Path:
    path = Path(report)
    if path.is_absolute():
        return path
    return (_REPO_ROOT / path).resolve()


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        data = voice_config.load_manifest(args.config)
    except voice_config.ConfigError as exc:
        print("mini_validation: %s" % exc, file=sys.stderr)
        return 2

    result = run_validation(
        data,
        ready_timeout=args.ready_timeout,
        serves_manifest_path=args.serves_manifest,
        target_host_pattern=args.target_host_pattern,
    )
    print(json.dumps(result, indent=2))

    report_path = None
    append_ok = True
    if args.report:
        report_path = resolve_report_path(args.report)
        if report_path.parent:
            report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        print("mini_validation: wrote report to %s" % report_path)

        ttfa = (result.get("benchmark") or {}).get("ttfa_ms") if result.get("benchmark") else None
        latency = (result.get("benchmark") or {}).get("turn_latency_ms") if result.get("benchmark") else None
        host_mem = result.get("host_memory_after_load") or {}
        append_ok = append_finding_row(
            "| %s | %s | %s | %s | %s | %s | %s / %s | %s / %s | %s | %s |" % (
                result["measured_at"],
                result.get("hostname") or "-",
                "%s GB; 16gb_class=%s" % (_fmt(host_mem.get("total_gb")), _fmt(result.get("host_is_16gb_class"))),
                result.get("verdict", {}).get("status", "-"),
                _serve_cell(result["stt"]),
                _serve_cell(result["tts"]),
                _fmt(ttfa),
                _fmt(latency),
                _fmt(host_mem.get("used_gb")),
                _fmt(host_mem.get("available_gb")),
                _failure_cell(result),
                display_path(report_path),
            )
        )
    verdict = result.get("verdict", {}).get("status")
    if verdict == "unsupported" and not args.allow_unsupported:
        print("mini_validation: verdict unsupported; pass --allow-unsupported for exploratory negative-control runs", file=sys.stderr)
        return 1
    if not append_ok:
        print("mini_validation: could not append required findings row", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
