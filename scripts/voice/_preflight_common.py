"""Shared helper: subprocess probes, GPU/container context, and readiness
plumbing common to ``preflight_stt.py`` (T007) and ``preflight_tts.py``
(T009). These preflight scripts each run a real A/B against configured
endpoints; this module holds only the diagnostic/readiness scaffolding
around that (never the transcription/synthesis wire call itself), so it
stays import-safe without ``torch``/GPU/docker present (every probe here is
best-effort and never raises -- see each function's own docstring).
"""
from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

try:
    import torch  # type: ignore
except Exception:  # noqa: BLE001 - any import-time failure just means "no GPU info available"
    torch = None


def _run_probe(argv: List[str], *, timeout: float = 5.0) -> Dict[str, Any]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        return {"ok": False, "detail": "%s not found" % argv[0], "error": str(exc)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "%s timed out" % argv[0]}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def nvidia_smi_info() -> Dict[str, Any]:
    query = "name,compute_cap,memory.total,memory.used"
    probe = _run_probe(["nvidia-smi", "--query-gpu=%s" % query, "--format=csv,noheader"], timeout=5.0)
    if not probe.get("ok"):
        return {"available": False, "source": "nvidia-smi", "detail": probe.get("detail") or probe.get("stderr", "")}
    devices = []
    for idx, line in enumerate(probe.get("stdout", "").splitlines()):
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 4:
            continue
        capability = parts[1].replace(".", "")
        devices.append({
            "index": idx,
            "name": parts[0],
            "capability": "sm_%s" % capability,
            "memory_total": parts[2],
            "memory_used": parts[3],
        })
    return {"available": bool(devices), "source": "nvidia-smi", "devices": devices}


def gpu_info() -> Dict[str, Any]:
    """Best-effort local-GPU context for the report; never raises."""
    if torch is None:
        smi = nvidia_smi_info()
        if smi.get("available"):
            smi["detail"] = "torch not importable; GPU context collected with nvidia-smi"
            return smi
        return {"available": False, "detail": "torch not importable and nvidia-smi unavailable"}
    try:
        if not torch.cuda.is_available():
            smi = nvidia_smi_info()
            if smi.get("available"):
                smi["detail"] = "torch imported but no CUDA device visible; GPU context collected with nvidia-smi"
                return smi
            return {"available": False, "detail": "torch imported but no CUDA device visible"}
        idx = torch.cuda.current_device()
        major, minor = torch.cuda.get_device_capability(idx)
        return {
            "available": True,
            "source": "torch",
            "name": torch.cuda.get_device_name(idx),
            "capability": "sm_%d%d" % (major, minor),
        }
    except Exception as exc:  # noqa: BLE001 - informational probe must never crash the run
        smi = nvidia_smi_info()
        if smi.get("available"):
            smi["detail"] = "torch/CUDA probe raised; GPU context collected with nvidia-smi: %s" % exc
            return smi
        return {"available": False, "detail": "torch/CUDA probe raised: %s" % exc}


def health_url_for_base(base_url: str) -> str:
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    return root.rstrip("/") + "/health"


def endpoint_health(base_url: str, *, timeout: float) -> Dict[str, Any]:
    url = health_url_for_base(base_url)
    try:
        with urllib.request.urlopen(url, timeout=min(timeout, 5.0)) as resp:  # noqa: S310 - configured local serve URL
            status = resp.getcode()
        return {"ready": 200 <= status < 300, "url": url, "status": status}
    except urllib.error.HTTPError as exc:
        return {"ready": False, "url": url, "status": exc.code, "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 - readiness probe is diagnostic, never fatal
        return {"ready": False, "url": url, "detail": str(exc)}


def model_ids_from_payload(payload: Any) -> List[str]:
    """Extract OpenAI-style model IDs from a ``/v1/models`` payload."""
    ids: List[str] = []
    records: List[Any] = []
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            records.extend(data)
        models = payload.get("models")
        if isinstance(models, list):
            records.extend(models)
        model_id = payload.get("id")
        if isinstance(model_id, str):
            ids.append(model_id)
    elif isinstance(payload, list):
        records.extend(payload)
    for record in records:
        if isinstance(record, dict):
            model_id = record.get("id") or record.get("model")
            if isinstance(model_id, str):
                ids.append(model_id)
        elif isinstance(record, str):
            ids.append(record)
    return ids


def container_info(container_name: Optional[str], *, extended: bool = False) -> Optional[Dict[str, Any]]:
    """Best-effort ``docker inspect`` context for a candidate's container.

    ``extended=True`` (preflight_tts) additionally probes the running
    container's ``CUDA_VISIBLE_DEVICES`` env var (via ``docker exec``) and its
    ``HostConfig.DeviceRequests`` (via a second ``docker inspect``) -- both
    skipped by default (preflight_stt) since STT candidates don't need that
    extra GPU-assignment detail in the report.
    """
    if not container_name:
        return None
    template = "{{.State.Status}}\t{{.Config.Image}}\t{{.Name}}"
    probe = _run_probe(["docker", "inspect", container_name, "--format", template], timeout=5.0)
    if not probe.get("ok"):
        return {"name": container_name, "available": False, "detail": probe.get("stderr") or probe.get("detail", "")}
    parts = probe.get("stdout", "").split("\t")
    info: Dict[str, Any] = {
        "name": container_name,
        "available": True,
        "status": parts[0] if len(parts) > 0 else "",
        "image": parts[1] if len(parts) > 1 else "",
        "docker_name": parts[2].lstrip("/") if len(parts) > 2 else "",
    }
    if not extended:
        return info
    if info["status"] == "running":
        env_probe = _run_probe(["docker", "exec", container_name, "printenv", "CUDA_VISIBLE_DEVICES"], timeout=5.0)
        if env_probe.get("ok"):
            info["cuda_visible_devices"] = env_probe.get("stdout", "")
    device_probe = _run_probe(["docker", "inspect", container_name, "--format", "{{json .HostConfig.DeviceRequests}}"], timeout=5.0)
    if device_probe.get("ok") and device_probe.get("stdout"):
        try:
            info["device_requests"] = json.loads(device_probe["stdout"])
        except json.JSONDecodeError:
            info["device_requests"] = device_probe["stdout"]
    return info
