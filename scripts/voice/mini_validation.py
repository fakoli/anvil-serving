#!/usr/bin/env python
"""RUN ON A 16GB MINI (or another host for an honest negative control).

Validates the "small edge box, brain on the big rig" split (anvil task T016):
local STT+TTS serves run ON the 16GB Mini (per
``docs/findings/2026-07-04-hf-speech-to-speech-review.md``'s VRAM/RAM
guidance -- STT ~1-4GB, TTS ~0.5-7GB, both comfortably small), while the LLM
stage is routed over the tailnet to the anvil router on fakoli-dark (see the
saved memory note on Mini<->router tailnet binding -- the router publishes
its tailnet IP, not loopback). Records, per configured serve: startup
wall-clock, post-benchmark serve memory (Docker stats or native Mini
listener-process RSS -- see the HONESTY NOTE below for what that does and
doesn't cover), one end-to-end
TTFA/turn-latency sample (reusing ``anvil_serving.voice.benchmark``), and any
failure encountered at each step -- a partial/failed run still produces a
complete report rather than crashing, since "does it fail gracefully on a
memory-constrained box" is itself one of the things this script is measuring.

Feeds ``docs/findings/2026-07-voice-16gb-mini.md``.

HONESTY NOTE -- what "memory" means here: this script's own peak RSS (via
the stdlib ``resource`` module, POSIX-only) reflects the DRIVER PROCESS's
footprint, which is tiny and NOT the number that matters on a 16GB box -- the
STT/TTS engines run OUT-OF-PROCESS. The metric that actually matters is each
serve's OWN memory, which this script also records after the live benchmark.
Managed container serves use ``docker stats --no-stream`` against the
container names declared in the serves manifest (see
``anvil_serving.serves.load_manifest`` / ``anvil_serving/voice/serves/_common.py``).
Explicit external native Mini serves use macOS ``lsof`` + ``ps`` to
attribute RSS to the process listening on the configured loopback port.
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
    python scripts/voice/mini_validation.py --config examples/voice/fakoli-mini.toml \\
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
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from anvil_serving import serves as generic_serves  # noqa: E402
from anvil_serving.voice import benchmark as voice_benchmark  # noqa: E402
from anvil_serving.voice import config as voice_config  # noqa: E402
from anvil_serving.voice.stages.tts import TTSStageConfig, stream_speech  # noqa: E402
from anvil_serving.voice.serves._common import ServeNotConfigured  # noqa: E402
from anvil_serving.voice.serves.stt import STTServe, STTServeConfig  # noqa: E402
from anvil_serving.voice.serves.tts import TTSServe, TTSServeConfig  # noqa: E402
from scripts.voice.local_loop_demo import route_decision_probe  # noqa: E402

_FAKOLI_MINI_CONFIG = _REPO_ROOT / "examples" / "voice" / "fakoli-mini.toml"
_FAKOLI_DARK_CONFIG = _REPO_ROOT / "examples" / "voice" / "fakoli-dark.toml"
DEFAULT_CONFIG = str(
    _FAKOLI_MINI_CONFIG if _FAKOLI_MINI_CONFIG.is_file()
    else _FAKOLI_DARK_CONFIG if _FAKOLI_DARK_CONFIG.is_file()
    else Path(voice_config.DEFAULT_CONFIG)
)
DEFAULT_REPORT_PATH_REL = "docs/findings/2026-07-voice-16gb-mini.json"
DEFAULT_REPORT_PATH = str(_REPO_ROOT / DEFAULT_REPORT_PATH_REL)
FINDINGS_DOC = _REPO_ROOT / "docs" / "findings" / "2026-07-voice-16gb-mini.md"
DEFAULT_TARGET_HOST_PATTERN = r"(?i)^fakoli[-_. ]?mini(?:[-_. ]?2)?$"
DEFAULT_TARGET_HW_MODEL_PATTERN = r"^Mac16,10$"
DEFAULT_FAKOLI_DARK_HOSTS = ("100.87.34.66", "fakoli-dark")
MIN_TTS_OUTPUT_SECONDS = 0.25
BENCHMARK_SAMPLE_TEXT = "testing the local voice proof"

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


def _probe_stdout(argv: List[str], *, _run: Callable[..., Any] = subprocess.run) -> Optional[str]:
    probe = _run_probe(argv, timeout=5.0, _run=_run)
    if not probe.get("ok"):
        return None
    text = str(probe.get("stdout") or "").strip()
    return text or None


def host_identity_snapshot(*, _run: Callable[..., Any] = subprocess.run) -> Dict[str, Any]:
    """Best-effort target host identity and hardware proof."""
    identity: Dict[str, Any] = {
        "platform_node": platform.node(),
        "hostname": None,
        "computer_name": None,
        "local_host_name": None,
        "hardware_model": None,
    }
    identity["hostname"] = _probe_stdout(["hostname"], _run=_run)
    if sys.platform == "darwin":
        identity["computer_name"] = _probe_stdout(["scutil", "--get", "ComputerName"], _run=_run)
        identity["local_host_name"] = _probe_stdout(["scutil", "--get", "LocalHostName"], _run=_run)
        identity["hardware_model"] = _probe_stdout(["sysctl", "-n", "hw.model"], _run=_run)
    return identity


def _identity_values(identity: Dict[str, Any]) -> List[str]:
    return [
        str(value)
        for key in ("platform_node", "hostname", "computer_name", "local_host_name")
        for value in [identity.get(key)]
        if isinstance(value, str) and value.strip()
    ]


def host_identity_matches_target(identity: Dict[str, Any], pattern: str) -> bool:
    return any(host_matches_target(value, pattern) for value in _identity_values(identity))


def hardware_model_matches_target(identity: Dict[str, Any], pattern: str) -> bool:
    model = identity.get("hardware_model")
    if not isinstance(model, str) or not model.strip():
        return False
    return host_matches_target(model, pattern)


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


def model_ids_from_payload(payload: Any) -> List[str]:
    records: List[Any] = []
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            records.extend(data)
        models = payload.get("models")
        if isinstance(models, list):
            records.extend(models)
    ids: List[str] = []
    for record in records:
        if isinstance(record, dict) and isinstance(record.get("id"), str):
            ids.append(record["id"])
        elif isinstance(record, str):
            ids.append(record)
    return ids


def endpoint_models_probe(base_url: str, expected_model: str, *, timeout: float = 10.0) -> Dict[str, Any]:
    url = base_url.rstrip("/") + "/models"
    result: Dict[str, Any] = {
        "ok": False,
        "url": url,
        "expected_model": expected_model,
        "model_ids": [],
        "model_present": False,
    }
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - configured serve URL only
            raw = resp.read(256 * 1024).decode("utf-8", "replace")
            try:
                payload: Any = json.loads(raw or "{}")
            except ValueError:
                payload = {"raw": raw[:1000]}
            model_ids = model_ids_from_payload(payload)
            result.update({
                "ok": True,
                "status": getattr(resp, "status", resp.getcode()),
                "model_ids": model_ids,
                "model_present": expected_model in model_ids,
            })
            if not model_ids:
                result["error"] = "models response did not contain model ids"
            elif expected_model not in model_ids:
                result["error"] = "expected model %s not advertised" % expected_model
            return result
    except urllib.error.HTTPError as exc:
        result.update({"status": exc.code, "error": "HTTP %s" % exc.code})
        return result
    except Exception as exc:  # noqa: BLE001 - proof capture reports failures
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        return result


def route_auth_negative_probe(data: Dict[str, Any]) -> Dict[str, Any]:
    """Verify token-protected manifests reject a no-Authorization route probe."""
    llm = data["voice"]["llm"]
    env_name = llm.get("api_key_env")
    base_url = llm["base_url"].rstrip("/")
    url = base_url + "/route"
    result: Dict[str, Any] = {
        "required": bool(env_name),
        "auth_env": env_name,
        "url": url,
        "auth_enforced": False,
    }
    if not env_name:
        result["ok"] = True
        result["auth_enforced"] = True
        return result

    body = {
        "model": llm["model"],
        "messages": [{"role": "user", "content": "voice Mini validation auth-negative proof"}],
        "modality": "voice",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - configured router URL only
            result.update({
                "ok": False,
                "status": getattr(resp, "status", resp.getcode()),
                "error": "route accepted an unauthenticated request",
            })
            return result
    except urllib.error.HTTPError as exc:
        result.update({
            "ok": exc.code in (401, 403),
            "status": exc.code,
            "auth_enforced": exc.code in (401, 403),
        })
        if exc.code not in (401, 403):
            result["error"] = "expected 401/403, got HTTP %s" % exc.code
        return result
    except Exception as exc:  # noqa: BLE001 - evidence capture should report, not crash
        result["ok"] = False
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        return result


def _stage_config_from_table(table: Dict[str, Any], cls) -> Any:
    allowed = {field.name for field in fields(cls)}
    kwargs = {key: value for key, value in table.items() if key in allowed}
    kwargs.setdefault("base_url", "")
    kwargs.setdefault("model", "")
    return cls(**kwargs)


def build_benchmark_sample(data: Dict[str, Any]) -> tuple[Dict[str, Any], bytes, int]:
    """Create a speech sample through local TTS for the STT benchmark input."""
    cfg = _stage_config_from_table(data["voice"].get("tts", {}), TTSStageConfig)
    chunks: List[bytes] = []
    for chunk in stream_speech(BENCHMARK_SAMPLE_TEXT, cfg):
        if chunk:
            chunks.append(chunk)
    pcm = b"".join(chunks)
    sample = {
        "source": "tts_endpoint",
        "text": BENCHMARK_SAMPLE_TEXT,
        "audio_bytes": len(pcm),
        "sample_rate": cfg.source_sample_rate,
    }
    if not pcm:
        raise RuntimeError("TTS benchmark sample generation produced no audio")
    return sample, pcm, cfg.source_sample_rate


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


def _endpoint_host_port(base_url: str) -> tuple[str, Optional[int]]:
    parsed = urllib.parse.urlparse(base_url)
    try:
        port = parsed.port
    except ValueError:
        port = None
    return (parsed.hostname or "").lower(), port


def _parse_lsof_listeners(text: str, *, host: str, port: int) -> List[Dict[str, Any]]:
    listeners: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    expected = "%s:%d" % (host, port)
    wildcard = "*:%d" % port
    for raw in text.splitlines():
        if not raw:
            continue
        tag, value = raw[0], raw[1:]
        if tag == "p":
            if current:
                listeners.append(current)
            current = {"pid": value}
        elif tag == "c":
            current["command_name"] = value
        elif tag == "n":
            current["listener"] = value
    if current:
        listeners.append(current)
    return [
        item for item in listeners
        if str(item.get("pid", "")).isdigit()
        and (
            expected in str(item.get("listener", ""))
            or wildcard in str(item.get("listener", ""))
        )
    ]


def process_mem_for_endpoint(
    base_url: str, *, _run: Callable[..., Any] = subprocess.run,
) -> Optional[Dict[str, Any]]:
    """Attribute native process RSS to the listener for one loopback endpoint."""
    host, port = _endpoint_host_port(base_url)
    if host != "127.0.0.1" or not port:
        return None
    if sys.platform != "darwin":
        return None
    probe = _run_probe(
        ["lsof", "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN", "-F", "pcn"],
        timeout=5.0,
        _run=_run,
    )
    if not probe.get("ok"):
        return None
    listeners = _parse_lsof_listeners(str(probe.get("stdout") or ""), host=host, port=port)
    pids = sorted({int(item["pid"]) for item in listeners})
    if len(pids) != 1:
        return None
    pid = pids[0]
    ps = _run_probe(["ps", "-p", str(pid), "-o", "rss=", "-o", "command="], timeout=5.0, _run=_run)
    if not ps.get("ok"):
        return None
    first = next((line.strip() for line in str(ps.get("stdout") or "").splitlines() if line.strip()), "")
    if not first:
        return None
    rss_text, _, command = first.partition(" ")
    if not rss_text.isdigit():
        return None
    rss_mb = round(int(rss_text) / 1024.0, 2)
    if rss_mb <= 0:
        return None
    return {
        "source": "macos_process_rss",
        "pid": pid,
        "rss_mb": rss_mb,
        "endpoint_host": host,
        "port": port,
        "listener": listeners[0].get("listener"),
        "command": command.strip()[:500],
    }


def memory_proof_for_serve(
    serve_name: str,
    base_url: str,
    serves_manifest_path: Optional[str],
    *,
    observed_after_benchmark: bool,
    lifecycle: str = "managed",
    model_probe: Optional[Dict[str, Any]] = None,
    _run: Callable[..., Any] = subprocess.run,
) -> Optional[Dict[str, Any]]:
    if lifecycle != "external":
        container_mem = container_mem_for_serve(serve_name, serves_manifest_path)
        if container_mem:
            return {
                "source": "docker_stats",
                "container_mem": container_mem,
                "observed_after_benchmark": observed_after_benchmark,
                "model_probe": model_probe,
            }
    process_mem = process_mem_for_endpoint(base_url, _run=_run)
    if process_mem:
        process_mem["observed_after_benchmark"] = observed_after_benchmark
        process_mem["model_probe"] = model_probe
        return process_mem
    return None


def valid_memory_proof(proof: Any) -> bool:
    if not isinstance(proof, dict) or proof.get("observed_after_benchmark") is not True:
        return False
    model_probe = proof.get("model_probe")
    if not isinstance(model_probe, dict) or model_probe.get("model_present") is not True:
        return False
    source = proof.get("source")
    if source == "docker_stats":
        return bool(isinstance(proof.get("container_mem"), str) and proof["container_mem"].strip())
    if source == "macos_process_rss":
        return (
            isinstance(proof.get("pid"), int)
            and proof["pid"] > 0
            and isinstance(proof.get("rss_mb"), (int, float))
            and proof["rss_mb"] > 0
            and proof.get("endpoint_host") == "127.0.0.1"
            and isinstance(proof.get("port"), int)
            and proof["port"] > 0
        )
    return False


@dataclass
class ServeValidation:
    kind: str
    serve_name: str
    lifecycle: str
    bring_up_ok: bool
    bring_up_returncode: Optional[int]
    startup_s: Optional[float]
    ready: Optional[bool]
    docker_state: Optional[str]
    readiness_detail: Optional[str]
    container_mem: Optional[str]
    container_mem_after_benchmark: Optional[str]
    memory_proof: Optional[Dict[str, Any]]
    memory_proof_after_benchmark: Optional[Dict[str, Any]]
    error: Optional[str]


def validate_one_serve(
    kind: str, serve_config, serve_obj, *, ready_timeout: float, serves_manifest_path: Optional[str],
    lifecycle: str = "managed",
) -> ServeValidation:
    serve_name = serve_config.serve_name
    t0 = time.perf_counter()
    error = None
    ready = None
    docker_state = None
    readiness_detail = None
    bring_up_returncode = None
    if lifecycle != "external":
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

    startup_s = round(time.perf_counter() - t0, 2)
    container_mem = container_mem_for_serve(serve_name, serves_manifest_path)
    memory_proof = memory_proof_for_serve(
        serve_name, getattr(serve_config, "base_url", ""), serves_manifest_path,
        observed_after_benchmark=False,
        lifecycle=lifecycle,
    )

    return ServeValidation(
        kind=kind, serve_name=serve_name, lifecycle=lifecycle,
        bring_up_ok=(lifecycle == "external") or (error is None and (bring_up_returncode in (None, 0))),
        bring_up_returncode=bring_up_returncode,
        startup_s=startup_s, ready=ready, docker_state=docker_state,
        readiness_detail=readiness_detail, container_mem=container_mem,
        container_mem_after_benchmark=None,
        memory_proof=memory_proof,
        memory_proof_after_benchmark=None,
        error=error,
    )


def build_verdict(result: Dict[str, Any]) -> Dict[str, Any]:
    reasons: List[str] = []
    failure_modes: List[str] = []

    host_memory = result.get("host_memory_after_load") or {}
    is_target = is_16gb_class(host_memory)
    if result.get("platform") != "darwin":
        failure_modes.append("host_not_macos_mini")
        reasons.append("target proof must run on macOS Mini hardware (platform=%s)" % result.get("platform"))
    if is_target is False:
        failure_modes.append("host_not_16gb_class")
        reasons.append("host is not a 16GB-class Mini target (total_gb=%s)" % host_memory.get("total_gb"))
    elif is_target is None:
        failure_modes.append("host_memory_unmeasured")
        reasons.append("host total memory could not be measured")
    if not isinstance(host_memory.get("available_gb"), (int, float)):
        failure_modes.append("host_available_memory_unmeasured")
        reasons.append("host available memory after load could not be measured")
    if not isinstance(host_memory.get("used_gb"), (int, float)):
        failure_modes.append("host_used_memory_unmeasured")
        reasons.append("host used memory after load could not be measured")
    if not result.get("host_matches_expected_mini"):
        failure_modes.append("host_not_expected_mini")
    if not result.get("host_hw_model_matches_expected"):
        failure_modes.append("host_hw_model_unmatched")

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
    route_auth_negative = result.get("route_auth_negative") or {}
    if result.get("llm_auth_env") and route_auth_negative.get("auth_enforced") is not True:
        failure_modes.append("llm_auth_not_enforced")
    if result.get("benchmark_sample_error"):
        failure_modes.append("benchmark_sample_error")
    sample = result.get("benchmark_sample") or {}
    if not isinstance(sample.get("audio_bytes"), int) or sample.get("audio_bytes", 0) <= 0:
        failure_modes.append("benchmark_sample_missing")
    if result.get("benchmark_error"):
        failure_modes.append("benchmark_error")
    elif not result.get("benchmark"):
        failure_modes.append("benchmark_missing")
    else:
        benchmark = result.get("benchmark") or {}
        if not benchmark.get("tts_first_audio_observed") or not benchmark.get("tts_output_bytes"):
            failure_modes.append("first_audio_missing")
        if not str(benchmark.get("stt_hypothesis") or "").strip():
            failure_modes.append("stt_hypothesis_missing")
        if not str(benchmark.get("llm_reply") or "").strip():
            failure_modes.append("llm_reply_missing")
        if not isinstance(benchmark.get("tts_audio_seconds"), (int, float)):
            failure_modes.append("tts_audio_duration_missing")
        elif float(benchmark["tts_audio_seconds"]) < MIN_TTS_OUTPUT_SECONDS:
            failure_modes.append("tts_audio_too_short")
        if not isinstance(benchmark.get("ttfa_ms"), (int, float)):
            failure_modes.append("ttfa_missing")
        if not isinstance(benchmark.get("turn_latency_ms"), (int, float)):
            failure_modes.append("turn_latency_missing")
        if not isinstance(benchmark.get("tts_rtf"), (int, float)):
            failure_modes.append("tts_rtf_missing")

    stt_model_probe = (
        result.get("stt", {}).get("memory_proof_after_benchmark") or {}
    ).get("model_probe")
    tts_model_probe = (
        result.get("tts", {}).get("memory_proof_after_benchmark") or {}
    ).get("model_probe")
    if result.get("stt", {}).get("ready") and (
        not isinstance(stt_model_probe, dict) or stt_model_probe.get("model_present") is not True
    ):
        failure_modes.append("stt_model_not_advertised")
    if result.get("tts", {}).get("ready") and (
        not isinstance(tts_model_probe, dict) or tts_model_probe.get("model_present") is not True
    ):
        failure_modes.append("tts_model_not_advertised")
    if result.get("stt", {}).get("ready") and not valid_memory_proof(
        result.get("stt", {}).get("memory_proof_after_benchmark")
    ):
        failure_modes.append("stt_memory_missing")
    if result.get("tts", {}).get("ready") and not valid_memory_proof(
        result.get("tts", {}).get("memory_proof_after_benchmark")
    ):
        failure_modes.append("tts_memory_missing")

    available = host_memory.get("available_gb")
    if isinstance(available, (int, float)) and available < 2.0:
        failure_modes.append("host_available_memory_low")
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
    target_host_pattern: str, target_hw_model_pattern: str,
) -> Dict[str, Any]:
    voice = data["voice"]
    rss_before = peak_rss_mb()
    host_before = host_memory_snapshot()
    host_identity = host_identity_snapshot()

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
        lifecycle=voice["stt"].get("lifecycle", "managed"),
    )
    tts_validation = validate_one_serve(
        "tts", tts_cfg, TTSServe(tts_cfg), ready_timeout=ready_timeout, serves_manifest_path=serves_manifest_path,
        lifecycle=voice["tts"].get("lifecycle", "managed"),
    )
    stt_result = asdict(stt_validation)
    tts_result = asdict(tts_validation)
    host_after_serves_ready = host_memory_snapshot()
    route_proof = route_decision_probe(data, prompt="voice Mini validation route proof")
    route_auth_negative = route_auth_negative_probe(data)

    benchmark_result: Optional[Dict[str, Any]] = None
    benchmark_error: Optional[str] = None
    benchmark_sample: Optional[Dict[str, Any]] = None
    benchmark_sample_error: Optional[str] = None
    benchmark_pcm: Optional[bytes] = None
    benchmark_sample_rate = 16000
    try:
        benchmark_sample, benchmark_pcm, benchmark_sample_rate = build_benchmark_sample(data)
    except Exception as exc:  # noqa: BLE001 - report sample generation failures as validation evidence
        benchmark_sample_error = str(exc)
    try:
        # LLM stage is routed to fakoli-dark's router (whatever [voice.llm]
        # in the manifest declares -- e.g. the tailnet address, per the saved
        # Mini<->router tailnet-binding note); STT/TTS calls stay local. This
        # makes LIVE network calls -- see run_benchmark_from_manifest's own
        # honesty note in anvil_serving/voice/benchmark.py.
        if benchmark_sample_error:
            raise RuntimeError("benchmark sample generation failed: %s" % benchmark_sample_error)
        benchmark_result = voice_benchmark.run_benchmark_from_manifest(
            data,
            pcm=benchmark_pcm,
            sample_rate=benchmark_sample_rate,
            reference_text=BENCHMARK_SAMPLE_TEXT,
        )
    except Exception as exc:  # noqa: BLE001 - a benchmark failure is itself a reportable failure mode, not a crash
        benchmark_error = str(exc)

    stt_model_probe = endpoint_models_probe(
        voice["stt"]["base_url"], voice["stt"]["model"], timeout=float(voice["stt"].get("timeout", 10.0)),
    )
    tts_model_probe = endpoint_models_probe(
        voice["tts"]["base_url"], voice["tts"]["model"], timeout=float(voice["tts"].get("timeout", 20.0)),
    )
    stt_memory_proof = memory_proof_for_serve(
        stt_result["serve_name"], voice["stt"]["base_url"], serves_manifest_path,
        observed_after_benchmark=True,
        lifecycle=stt_result.get("lifecycle", "managed"),
        model_probe=stt_model_probe,
    )
    tts_memory_proof = memory_proof_for_serve(
        tts_result["serve_name"], voice["tts"]["base_url"], serves_manifest_path,
        observed_after_benchmark=True,
        lifecycle=tts_result.get("lifecycle", "managed"),
        model_probe=tts_model_probe,
    )
    stt_result["memory_proof_after_benchmark"] = stt_memory_proof
    tts_result["memory_proof_after_benchmark"] = tts_memory_proof
    stt_result["container_mem_after_benchmark"] = (
        stt_memory_proof.get("container_mem")
        if isinstance(stt_memory_proof, dict) and stt_memory_proof.get("source") == "docker_stats"
        else None
    )
    tts_result["container_mem_after_benchmark"] = (
        tts_memory_proof.get("container_mem")
        if isinstance(tts_memory_proof, dict) and tts_memory_proof.get("source") == "docker_stats"
        else None
    )
    host_after_load = host_memory_snapshot()
    rss_after = peak_rss_mb()
    hostname = platform.node()
    llm = voice["llm"]
    llm_auth_env = llm.get("api_key_env")

    result = {
        "platform": sys.platform,
        "hostname": hostname,
        "host_identity": host_identity,
        "target_host_pattern": target_host_pattern,
        "target_hw_model_pattern": target_hw_model_pattern,
        "host_matches_expected_mini": host_identity_matches_target(host_identity, target_host_pattern),
        "host_hw_model_matches_expected": hardware_model_matches_target(host_identity, target_hw_model_pattern),
        "host_memory_before": host_before,
        "host_memory_after_serves_ready": host_after_serves_ready,
        "host_memory_after_load": host_after_load,
        "host_is_16gb_class": is_16gb_class(host_after_load),
        "driver_process_rss_before_mb": rss_before,
        "driver_process_peak_rss_mb": rss_after,
        "driver_process_rss_note": (
            "resource module unavailable on this platform (expected on Windows; "
            "target platform is macOS/Linux)" if rss_after is None else
            "this is the DRIVER SCRIPT's own footprint, not the STT/TTS serves' -- see memory_proof per serve"
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
        "route_auth_negative": route_auth_negative,
        "stt_local_endpoint": is_loopback_url(voice["stt"]["base_url"]),
        "tts_local_endpoint": is_loopback_url(voice["tts"]["base_url"]),
        "benchmark_sample": benchmark_sample,
        "benchmark_sample_error": benchmark_sample_error,
        "benchmark": benchmark_result,
        "benchmark_error": benchmark_error,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    result["verdict"] = build_verdict(result)
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=DEFAULT_CONFIG, help="voice manifest TOML (STT/LLM/TTS endpoints)")
    p.add_argument(
        "--serves-manifest",
        default=None,
        help="serves.toml path for managed containers; external native serves are attributed by loopback listener RSS",
    )
    p.add_argument("--ready-timeout", type=float, default=60.0, help="per-serve readiness-probe timeout, seconds")
    p.add_argument(
        "--target-host-pattern",
        default=os.environ.get("ANVIL_VOICE_MINI_HOST_PATTERN", DEFAULT_TARGET_HOST_PATTERN),
        help="case-insensitive regex the host name must match for target Mini proof",
    )
    p.add_argument(
        "--target-hw-model-pattern",
        default=os.environ.get("ANVIL_VOICE_MINI_HW_MODEL_PATTERN", DEFAULT_TARGET_HW_MODEL_PATTERN),
        help="regex the macOS hw.model value must match for target Mini proof",
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
        help="return exit code 0 even when the verdict is not supported (for exploratory negative-control runs)",
    )
    return p


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    return str(value)


def _serve_cell(result: Dict[str, Any]) -> str:
    ready = "ready" if result.get("ready") else "not-ready"
    proof = result.get("memory_proof_after_benchmark") or result.get("memory_proof")
    if isinstance(proof, dict) and proof.get("source") == "docker_stats":
        mem = proof.get("container_mem") or "mem=n/a"
    elif isinstance(proof, dict) and proof.get("source") == "macos_process_rss":
        mem = "rss=%sMB pid=%s" % (_fmt(proof.get("rss_mb")), _fmt(proof.get("pid")))
    else:
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
        target_hw_model_pattern=args.target_hw_model_pattern,
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
    if verdict != "supported" and not args.allow_unsupported:
        print(
            "mini_validation: verdict %s; pass --allow-unsupported for exploratory negative-control runs"
            % verdict,
            file=sys.stderr,
        )
        return 1
    if not append_ok:
        print("mini_validation: could not append required findings row", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
