"""Read-only endpoint and isolated-worker gates for expensive benchmark jobs."""

from __future__ import annotations

import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import sys
from typing import Any, Callable, Mapping
import urllib.error
import urllib.request

from .jobs import (
    BenchmarkJobError,
    resolve_owned_run_path,
    utc_now,
    validate_job_spec,
)


BENCHMARK_PREFLIGHT_SCHEMA = "anvil-serving.benchmark-preflight/v1"
MAX_MODELS_RESPONSE_BYTES = 1024 * 1024
MAX_HARNESS_ASSETS = 128
EndpointOpener = Callable[..., Any]


def _check(name: str, passed: bool, code: str | None, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "code": code, "detail": detail}


def _requirements(value: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(value or {})
    allowed = {
        "min_free_disk_bytes",
        "platforms",
        "architectures",
        "container_required",
        "harness_assets",
        "harness_locks",
        "model_host_id",
    }
    unexpected = sorted(set(raw) - allowed)
    if unexpected:
        raise BenchmarkJobError(
            "bad_preflight_requirements",
            "preflight requirements contain unsupported fields",
            {"unsupported": unexpected},
        )
    minimum = raw.get("min_free_disk_bytes", 0)
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 0:
        raise BenchmarkJobError(
            "bad_preflight_requirements", "min_free_disk_bytes must be non-negative"
        )
    platforms = raw.get("platforms", [])
    architectures = raw.get("architectures", [])
    assets = raw.get("harness_assets", [])
    locks = raw.get("harness_locks", {})
    if not isinstance(platforms, list) or not all(isinstance(item, str) for item in platforms):
        raise BenchmarkJobError("bad_preflight_requirements", "platforms must be strings")
    if not isinstance(architectures, list) or not all(
        isinstance(item, str) for item in architectures
    ):
        raise BenchmarkJobError("bad_preflight_requirements", "architectures must be strings")
    if not isinstance(assets, list) or len(assets) > MAX_HARNESS_ASSETS or not all(
        isinstance(item, str) and item for item in assets
    ):
        raise BenchmarkJobError("bad_preflight_requirements", "harness_assets are invalid")
    if not isinstance(locks, Mapping) or len(locks) > MAX_HARNESS_ASSETS:
        raise BenchmarkJobError("bad_preflight_requirements", "harness_locks are invalid")
    normalized_locks = {}
    for name, digest in locks.items():
        if (
            not isinstance(name, str)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest.lower())
        ):
            raise BenchmarkJobError("bad_preflight_requirements", "harness lock is invalid")
        normalized_locks[name] = digest.lower()
    container_required = raw.get("container_required", False)
    if not isinstance(container_required, bool):
        raise BenchmarkJobError(
            "bad_preflight_requirements", "container_required must be a boolean"
        )
    model_host_id = raw.get("model_host_id")
    if model_host_id is not None and not isinstance(model_host_id, str):
        raise BenchmarkJobError("bad_preflight_requirements", "model_host_id must be a string")
    return {
        "min_free_disk_bytes": minimum,
        "platforms": sorted(set(item.lower() for item in platforms)),
        "architectures": sorted(set(item.lower() for item in architectures)),
        "container_required": container_required,
        "harness_assets": list(assets),
        "harness_locks": normalized_locks,
        "model_host_id": model_host_id,
    }


def _endpoint_models(
    spec: Mapping[str, Any], *, opener: EndpointOpener, timeout_s: float
) -> tuple[list[dict[str, Any]], int | None]:
    endpoint = spec["endpoint"]
    auth_env = endpoint.get("auth_env")
    token = (os.environ.get(auth_env) or "").strip() if auth_env else None
    if auth_env and not token:
        raise BenchmarkJobError(
            "missing_credentials", "endpoint credential environment variable is not set"
        )
    request = urllib.request.Request(endpoint["base_url"] + "/models")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with opener(request, timeout=timeout_s) as response:
            raw = response.read(MAX_MODELS_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise BenchmarkJobError(
                "authorization_denied", "endpoint rejected benchmark authentication"
            ) from exc
        raise BenchmarkJobError(
            "route_failure", f"endpoint models route returned HTTP {exc.code}"
        ) from exc
    except (OSError, urllib.error.URLError) as exc:
        raise BenchmarkJobError("route_failure", "endpoint models route is unreachable") from exc
    if len(raw) > MAX_MODELS_RESPONSE_BYTES:
        raise BenchmarkJobError("route_failure", "endpoint models response is too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
        models = payload["data"]
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise BenchmarkJobError("route_failure", "endpoint models response is malformed") from exc
    if not isinstance(models, list) or not all(isinstance(item, dict) for item in models):
        raise BenchmarkJobError("route_failure", "endpoint models response is malformed")
    selected = next((item for item in models if item.get("id") == endpoint["model"]), None)
    if selected is None:
        raise BenchmarkJobError(
            "model_mismatch", "requested routed model is absent from endpoint identity"
        )
    context = next(
        (
            selected.get(field)
            for field in ("max_model_len", "context_length", "max_context_length")
            if isinstance(selected.get(field), int)
        ),
        None,
    )
    return models, context


def _asset_path(root: str, relative: str) -> Path:
    if os.path.isabs(relative) or "\x00" in relative:
        raise BenchmarkJobError("absent_harness_assets", "harness asset path is unsafe")
    base = Path(root).resolve()
    path = (base / relative).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise BenchmarkJobError("absent_harness_assets", "harness asset escapes its root") from exc
    if path.is_symlink() or not path.is_file():
        raise BenchmarkJobError("absent_harness_assets", "required harness asset is absent")
    return path


def run_benchmark_preflight(
    spec: Mapping[str, Any],
    *,
    run_root: str,
    requirements: Mapping[str, Any] | None = None,
    assets_root: str | None = None,
    opener: EndpointOpener = urllib.request.urlopen,
    timeout_s: float = 10.0,
    disk_usage: Callable[[str], Any] = shutil.disk_usage,
    container_binary: str | None = None,
) -> dict[str, Any]:
    """Observe endpoint and worker readiness without changing model lifecycle."""
    normalized = validate_job_spec(spec)
    required = _requirements(requirements)
    worker_id = normalized["worker"]["id"]
    worker_platform = sys.platform.lower()
    architecture = platform.machine().lower()
    Path(run_root).mkdir(parents=True, exist_ok=True)
    run_path = resolve_owned_run_path(
        run_root,
        ownership_id=normalized["ownership_id"],
        run_id=normalized["run_id"],
    )
    Path(run_path).mkdir(parents=True, exist_ok=True)
    free_disk = int(disk_usage(run_path).free)
    docker = container_binary if container_binary is not None else shutil.which("docker")
    checks = []
    isolated = not required["model_host_id"] or required["model_host_id"] != worker_id
    checks.append(
        _check(
            "worker_isolation",
            isolated,
            None if isolated else "worker_not_isolated",
            "worker differs from model host" if isolated else "worker matches model host",
        )
    )
    platform_ok = not required["platforms"] or worker_platform in required["platforms"]
    architecture_ok = (
        not required["architectures"] or architecture in required["architectures"]
    )
    checks.append(
        _check(
            "worker_compatibility",
            platform_ok and architecture_ok,
            None if platform_ok and architecture_ok else "incompatible_architecture",
            f"platform={worker_platform} architecture={architecture}",
        )
    )
    disk_ok = free_disk >= required["min_free_disk_bytes"]
    checks.append(
        _check(
            "free_disk",
            disk_ok,
            None if disk_ok else "insufficient_disk",
            f"free_bytes={free_disk}",
        )
    )
    container_ok = not required["container_required"] or bool(docker)
    checks.append(
        _check(
            "container_capability",
            container_ok,
            None if container_ok else "container_capability_absent",
            "available" if docker else "unavailable",
        )
    )
    probe_path = Path(run_path) / ".preflight-write-probe"
    try:
        with probe_path.open("x", encoding="utf-8") as handle:
            handle.write("ready\n")
        probe_path.unlink()
        writable = True
    except OSError:
        writable = False
    checks.append(
        _check(
            "owned_output",
            writable,
            None if writable else "owned_output_unwritable",
            "writable" if writable else "unwritable",
        )
    )
    assets_ok = True
    asset_observations = []
    try:
        if (required["harness_assets"] or required["harness_locks"]) and not assets_root:
            raise BenchmarkJobError(
                "absent_harness_assets", "benchmark harness asset root is not configured"
            )
        for relative in sorted(
            set(required["harness_assets"]) | set(required["harness_locks"])
        ):
            path = _asset_path(assets_root or "", relative)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            expected = required["harness_locks"].get(relative)
            if expected and digest != expected:
                raise BenchmarkJobError(
                    "harness_lock_mismatch", "benchmark harness asset digest does not match"
                )
            asset_observations.append({"name": relative, "sha256": digest})
    except BenchmarkJobError as exc:
        assets_ok = False
        asset_error = exc
    checks.append(
        _check(
            "harness_assets",
            assets_ok,
            None if assets_ok else asset_error.code,
            "all locked assets present" if assets_ok else asset_error.message,
        )
    )
    served_models = []
    configured_context = None
    endpoint_error = None
    try:
        served_models, configured_context = _endpoint_models(
            normalized, opener=opener, timeout_s=timeout_s
        )
    except BenchmarkJobError as exc:
        endpoint_error = exc
    checks.append(
        _check(
            "endpoint_identity",
            endpoint_error is None,
            endpoint_error.code if endpoint_error else None,
            endpoint_error.message if endpoint_error else "requested model is served",
        )
    )
    unsupported = []
    if configured_context is None:
        unsupported.append("configured_context")
    artifact = {
        "schema": BENCHMARK_PREFLIGHT_SCHEMA,
        "run_id": normalized["run_id"],
        "created_at": utc_now(),
        "passed": all(item["passed"] for item in checks),
        "requirements": required,
        "observed": {
            "endpoint": {
                "base_url": normalized["endpoint"]["base_url"],
                "requested_model": normalized["endpoint"]["model"],
                "served_models": [item.get("id") for item in served_models],
                "configured_context": configured_context,
                "authentication": "configured" if normalized["endpoint"].get("auth_env") else "none",
            },
            "worker": {
                "id": worker_id,
                "platform": worker_platform,
                "architecture": architecture,
                "free_disk_bytes": free_disk,
                "container_capable": bool(docker),
                "owned_output_writable": writable,
                "harness_assets": asset_observations,
            },
        },
        "checks": checks,
        "unsupported_telemetry": unsupported,
        "side_effects": {"model_lifecycle": False, "route_mutation": False},
    }
    return artifact


def require_benchmark_preflight(artifact: Mapping[str, Any]) -> None:
    """Fail closed before an expensive profile when its independent gate failed."""
    if artifact.get("schema") != BENCHMARK_PREFLIGHT_SCHEMA or artifact.get("passed") is not True:
        failures = [
            item.get("code")
            for item in artifact.get("checks", [])
            if isinstance(item, Mapping) and item.get("passed") is False
        ]
        raise BenchmarkJobError(
            "preflight_failed",
            "benchmark profile cannot start until required preflight checks pass",
            {"failures": failures},
        )
