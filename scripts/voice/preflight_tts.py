#!/usr/bin/env python
"""RUN ON fakoli-dark (requires sm_120 GPU / real TTS serves)

TTS candidate preflight (anvil task T009): Kokoro-82M, Orpheus-3B, and
Qwen3-TTS 1.7B are the candidate families from
``docs/findings/2026-07-04-hf-speech-to-speech-review.md`` (Qwen3-TTS in
the TTS architecture notes; Kokoro and Orpheus in s8's sm_120 component
table). This script synthesizes one sample line of text against each
configured, already-running TTS serve over the SAME real wire call the TTS stage makes
(:func:`anvil_serving.voice.stages.tts.stream_speech`), measures TTFA
(time-to-first-audio-byte) + RTF (real-time factor: synth wall-clock /
seconds of audio produced), and prints a comparison table plus an optional
``--report`` JSON dump.

Feeds ``docs/findings/2026-07-voice-tts-ab.md``.

WHAT THIS DOES **NOT** MEASURE: perceptual audio quality (naturalness,
prosody, artifact rate). That needs a human listening test (or an automated
MOS-predictor model) -- deliberately out of scope for an automated preflight
script. This script records only automated PCM sanity, and sets the ``quality``
field to "not measured" until a human listener note is added separately.

HONESTY NOTE: this script requires a real sm_120 box with the candidate TTS
serves reachable at the configured ``base_url``s. Every number is real
measurement math (the same TTFA/RTF arithmetic
``anvil_serving/voice/benchmark.py`` already unit-tests) applied to WHATEVER
the configured endpoints answer with. With no ``--candidate`` flags, the
default packet proof exercises the three T009 candidate slots and fails until
all three endpoints are runnable. For a Kokoro-only smoke check, pass an
explicit Kokoro ``--candidate``.

Guarded import: ``torch`` is imported ONLY for informational local-GPU
context (see ``preflight_stt.py``'s identical guard) -- absence never blocks
the TTS run itself (pure HTTP via ``urllib``, see ``stages/tts.py``).

Usage::

    python scripts/voice/preflight_tts.py --report --capture-dir /tmp/tts-ab
    python scripts/voice/preflight_tts.py \\
        --candidate name=kokoro,base_url=http://127.0.0.1:30011/v1,model=kokoro,container=kokoro-tts \\
        --text "The quick brown fox jumps over the lazy dog."
"""
from __future__ import annotations

import argparse
import array
import json
import math
import subprocess
import sys
import time
import urllib.error
import urllib.request
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

# Defaults are the task-level T009 proof set. The no-arg proof command must
# fail closed until every required candidate endpoint starts and synthesizes.
DEFAULT_REPORT_PATH = "docs/findings/2026-07-voice-tts-ab.json"

DEFAULT_CANDIDATES: List[Dict[str, Any]] = [
    {
        "name": "kokoro-82m",
        "base_url": "http://127.0.0.1:30011/v1",
        "model": "kokoro",
        "source_sample_rate": 24000,
        "container_name": "kokoro-tts",
    },
    {
        "name": "orpheus-3b",
        "base_url": "http://127.0.0.1:30013/v1",
        "model": "orpheus-3b",
        "source_sample_rate": 24000,
        "container_name": "orpheus-tts",
    },
    {
        "name": "qwen3-tts-1.7b",
        "base_url": "http://127.0.0.1:30014/v1",
        "model": "qwen3-tts",
        "source_sample_rate": 24000,
        "container_name": "qwen3-tts",
    },
]


@dataclass
class Candidate:
    name: str
    base_url: str
    model: str
    source_sample_rate: int = 24000
    api_key_env: Optional[str] = None
    container_name: Optional[str] = None


def parse_candidate_arg(raw: str) -> Candidate:
    """Parse ``--candidate name=...,base_url=...,model=...[,source_sample_rate=...][,api_key_env=...][,container_name=...]``."""
    fields: Dict[str, str] = {}
    for part in raw.split(","):
        if "=" not in part:
            raise argparse.ArgumentTypeError("--candidate field %r must be key=value" % part)
        key, _, value = part.partition("=")
        fields[key.strip()] = value.strip()
    for required in ("name", "base_url", "model"):
        if required not in fields:
            raise argparse.ArgumentTypeError("--candidate is missing required field %r" % required)
    allowed = {"name", "base_url", "model", "source_sample_rate", "api_key_env", "container", "container_name"}
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise argparse.ArgumentTypeError("--candidate has unknown field(s): %s" % ", ".join(unknown))
    if "source_sample_rate" in fields:
        fields["source_sample_rate"] = int(fields["source_sample_rate"])
    if "container" in fields and "container_name" not in fields:
        fields["container_name"] = fields["container"]
    fields.pop("container", None)
    return Candidate(**fields)


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
        with urllib.request.urlopen(url, timeout=min(timeout, 5.0)) as resp:  # noqa: S310 - configured serve URL
            status = resp.getcode()
        return {"ready": 200 <= status < 300, "url": url, "status": status}
    except urllib.error.HTTPError as exc:
        return {"ready": False, "url": url, "status": exc.code, "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 - readiness probe is diagnostic, never fatal
        return {"ready": False, "url": url, "detail": str(exc)}


def models_probe(base_url: str, *, timeout: float) -> Dict[str, Any]:
    url = base_url.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=min(timeout, 5.0)) as resp:  # noqa: S310 - configured serve URL
            status = resp.getcode()
            body = resp.read(4096)
        payload: Any
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:  # noqa: BLE001 - diagnostic only
            payload = body[:200].decode("utf-8", errors="replace")
        return {"ready": 200 <= status < 300, "url": url, "status": status, "payload": payload}
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


def container_info(container_name: Optional[str]) -> Optional[Dict[str, Any]]:
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


def readiness(candidate: Candidate, *, timeout: float) -> Dict[str, Any]:
    health = endpoint_health(candidate.base_url, timeout=timeout)
    models = models_probe(candidate.base_url, timeout=timeout)
    container = container_info(candidate.container_name)
    model_ids = model_ids_from_payload(models.get("payload"))
    model_ready = candidate.model in model_ids
    ready = (
        bool(health.get("ready"))
        and bool(models.get("ready"))
        and model_ready
        and (container is None or container.get("status") == "running")
    )
    result: Dict[str, Any] = {
        "ready": ready,
        "health": health,
        "models": models,
        "model_ready": model_ready,
        "advertised_models": model_ids,
        "detail": "raw endpoint health/models/container probe plus expected model-id match",
    }
    if container is not None:
        result["container"] = container
    return result


def save_wav(path: str, pcm: bytes, sample_rate: int) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)


def pcm_sanity(pcm: bytes) -> Dict[str, Any]:
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not samples:
        return {"samples": 0, "rms": 0.0, "peak": 0, "nonzero_samples": 0}
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    return {
        "samples": len(samples),
        "rms": round(rms, 2),
        "peak": max(abs(sample) for sample in samples),
        "nonzero_samples": sum(1 for sample in samples if sample),
    }


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
            chunks.append(chunk)
    except TTSClientError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - record one candidate failure, not abort the A/B run
        error = str(exc)
    t_end = time.perf_counter()
    if error is None and total_bytes == 0:
        error = "no audio bytes received"

    ttfa_ms = None if error else ((first_audio_time or t_end) - t0) * 1000.0
    synth_s = max(0.0, t_end - t0)
    audio_s = (total_bytes / 2) / config.source_sample_rate if total_bytes else 0.0
    rtf = (synth_s / audio_s) if audio_s > 0 else None

    capture_path = None
    captured_pcm = b"".join(chunks) if chunks else b""
    if capture_dir and chunks:
        capture_path = str(Path(capture_dir) / ("%s.wav" % candidate.name))
        Path(capture_dir).mkdir(parents=True, exist_ok=True)
        save_wav(capture_path, captured_pcm, config.source_sample_rate)
    audio_sanity_note = (
        "automated PCM sanity only; no human listening pass by this script"
        if captured_pcm else
        "not measured; no audio captured"
    )
    quality_note = "not measured; human listening pass required"

    return {
        "name": candidate.name,
        "base_url": candidate.base_url,
        "model": candidate.model,
        "ttfa_ms": round(ttfa_ms, 2) if ttfa_ms is not None else None,
        "synth_seconds": round(synth_s, 4),
        "audio_seconds": round(audio_s, 4),
        "rtf": round(rtf, 4) if rtf is not None else None,
        "audio_bytes": total_bytes,
        "audio_sanity": pcm_sanity(captured_pcm) if captured_pcm else None,
        "audio_sanity_note": audio_sanity_note,
        "source_sample_rate": config.source_sample_rate,
        "capture_path": capture_path,
        "quality": quality_note,
        "error": error,
    }


def format_table(results: List[Dict[str, Any]]) -> str:
    header = "%-16s %-10s %-8s %-8s  capture" % ("candidate", "ttfa", "rtf", "status")
    lines = [header, "-" * len(header)]
    for r in results:
        if r["error"]:
            status = "ERROR"
        elif not r.get("readiness", {}).get("ready", True):
            status = "NOT_READY"
        else:
            status = "ok"
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
             "[,container_name=...|container=...] (repeatable; defaults to the full T009 candidate set)",
    )
    p.add_argument("--text", default=DEFAULT_TEXT, help="sample line of text to synthesize")
    p.add_argument("--timeout", type=float, default=20.0, help="per-request HTTP timeout (seconds)")
    p.add_argument("--ready-timeout", type=float, default=30.0, help="readiness-probe timeout per candidate (seconds)")
    p.add_argument("--capture-dir", help="save each candidate's synthesized audio as a WAV here (for a human quality pass)")
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

    if not args.text.strip():
        print("preflight_tts: --text must be non-empty", file=sys.stderr)
        return 2

    results = []
    for candidate in candidates:
        result = run_one(candidate, args.text, args.timeout, capture_dir=args.capture_dir)
        result["readiness"] = readiness(candidate, timeout=args.ready_timeout)
        results.append(result)
    print(format_table(results))

    report = {"gpu": gpu_info(), "text": args.text, "candidates": results}
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print("preflight_tts: wrote report to %s" % args.report)
    proof_failed = any(
        r["error"] or not r.get("readiness", {}).get("ready", False)
        for r in results
    )
    if not args.allow_errors and proof_failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
