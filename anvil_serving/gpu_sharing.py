"""Read-only CUDA Green Context and CUDA MPS capability inspection.

The inspector deliberately limits itself to inventory queries, library-symbol
lookups, package discovery, and read-only MPS control commands.  It never
creates a CUDA context, starts a daemon, changes a GPU mode, or launches a
workload.  Every external command is time bounded and injectable for no-GPU
unit tests.
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import ctypes.util
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from . import gpus as gpu_inventory
from .guard import confirmation_authorized
from .topology import load_topology, resolve_command_identity


SCHEMA_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 60.0
DEFAULT_PROBE_TIMEOUT_SECONDS = 180.0
MAX_PROBE_TIMEOUT_SECONDS = 300.0
DEFAULT_PROBE_COMPOSE_FILE = Path("examples/fakoli-dark/docker-compose.experiment.yml")
DEFAULT_PROBE_PROFILE = "gpu-sharing-probe"
DEFAULT_PROBE_SERVICE = "gpu-sharing-inspect"
DEFAULT_PROBE_IMAGE = (
    "nvidia/cuda@sha256:9cf8694a27722418a1f175d90f85d5afb5a728fd4a9907d7f0565efecfa14d32"
)
REVIEWED_PROBE_SOURCE_SHA256 = (
    "8f25562f2579b6f2eff6eabb16ecae51c5d95a236481a8fe8db00ed336ce7841"
)
MIN_GREEN_RUNTIME = (13, 1)
STATUSES = frozenset(
    {
        "supported",
        "unsupported",
        "unavailable",
        "unknown",
        "blocked_by_runtime_version",
        "blocked_by_environment",
    }
)
_VERSION_RE = re.compile(
    r"(?:release|CUDA (?:UMD )?Version:)\s*(\d+)\.(\d+)", re.IGNORECASE
)
_MPS_STOPPED_MARKERS = (
    "cannot find mps control daemon",
    "control daemon not found",
    "connection refused",
    "not running",
)
_MPS_READ_ONLY_INPUTS = frozenset({"get_server_list\n", "lspart\n"})
_GPU_UUID_RE = re.compile(r"GPU-[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}")


@dataclass(frozen=True)
class CommandProbe:
    """Bounded subprocess result without exception leakage."""

    state: str
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""


def _bounded_run(
    argv: Sequence[str],
    *,
    timeout: float,
    input_text: str | None = None,
    _run=subprocess.run,
) -> CommandProbe:
    try:
        result = _run(
            list(argv),
            capture_output=True,
            text=True,
            input=input_text,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return CommandProbe("missing")
    except subprocess.TimeoutExpired:
        return CommandProbe("timeout")
    except PermissionError:
        return CommandProbe("permission_denied")
    except OSError as exc:
        return CommandProbe("error", stderr=str(exc))
    return CommandProbe(
        "ok" if result.returncode == 0 else "error",
        returncode=result.returncode,
        stdout=str(result.stdout or "").strip(),
        stderr=str(result.stderr or "").strip(),
    )


def _parse_version(text: str) -> tuple[int, int] | None:
    match = _VERSION_RE.search(text)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _version_text(version: tuple[int, int] | None) -> str | None:
    return None if version is None else f"{version[0]}.{version[1]}"


def _probe_library_symbol(names: Sequence[str], symbol: str) -> dict[str, object]:
    """Look up one exported symbol without initializing CUDA."""
    candidates: list[str] = []
    for name in names:
        located = ctypes.util.find_library(name)
        if located:
            candidates.append(located)
    candidates.extend(name for name in names if name not in candidates)
    errors = []
    for candidate in candidates:
        try:
            library = ctypes.CDLL(candidate)
        except OSError as exc:
            errors.append(f"{candidate}: {exc}")
            continue
        return {
            "library": candidate,
            "symbol": symbol,
            "present": hasattr(library, symbol),
            "error": None,
        }
    return {
        "library": None,
        "symbol": symbol,
        "present": None,
        "error": errors[-1] if errors else "library not found",
    }


def _default_symbol_probe() -> dict[str, dict[str, object]]:
    if os.name == "nt":
        runtime_names = ("cudart64_131", "cudart64_130", "cudart")
        driver_names = ("nvcuda", "nvcuda.dll")
    else:
        runtime_names = ("cudart", "libcudart.so", "libcudart.so.13")
        driver_names = ("cuda", "libcuda.so.1")
    return {
        "runtime": _probe_library_symbol(runtime_names, "cudaGreenCtxCreate"),
        "driver": _probe_library_symbol(driver_names, "cuGreenCtxCreate"),
    }


def _parse_gpu_details(text: str) -> tuple[list[dict[str, object]], list[str]]:
    rows: list[dict[str, object]] = []
    warnings: list[str] = []
    for line_number, parts in enumerate(csv.reader(text.splitlines()), start=1):
        values = [part.strip() for part in parts]
        if len(values) != 5 or not values[0].isdigit():
            warnings.append(f"nvidia-smi returned malformed GPU row {line_number}")
            continue
        compute_capability = values[3] if re.fullmatch(r"\d+\.\d+", values[3]) else None
        if compute_capability is None:
            warnings.append(
                f"nvidia-smi returned an invalid compute capability for GPU row {line_number}"
            )
        rows.append(
            {
                "index": int(values[0]),
                "uuid": values[1],
                "name": values[2],
                "compute_capability": compute_capability,
                "sm_count": None,
                "driver_version": values[4] or None,
            }
        )
    return rows, warnings


def _parse_sm_counts(text: str) -> tuple[dict[str, int], list[str]]:
    counts: dict[str, int] = {}
    warnings: list[str] = []
    for line_number, parts in enumerate(csv.reader(text.splitlines()), start=1):
        values = [part.strip() for part in parts]
        if len(values) != 3 or not values[0].isdigit():
            warnings.append(f"nvidia-smi returned malformed SM-count row {line_number}")
            continue
        try:
            counts[values[1]] = int(values[2])
        except ValueError:
            warnings.append(f"nvidia-smi returned an invalid SM count for row {line_number}")
    return counts, warnings


def _probe_module(name: str, _find_spec=importlib.util.find_spec) -> dict[str, object]:
    try:
        available = _find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        available = False
    return {
        "status": "supported" if available else "unavailable",
        "evidence": [f"python package {name!r} is discoverable"] if available else [],
        "limitation": "package presence does not prove a working CUDA Green Context path",
    }


def _is_wsl(release: str | None = None) -> bool:
    release = platform.release() if release is None else release
    text = release.lower()
    if "microsoft" in text or "wsl" in text:
        return True
    try:
        with open("/proc/version", encoding="utf-8") as handle:
            return "microsoft" in handle.read().lower()
    except OSError:
        return False


def _green_status(
    *,
    compute_capability: str | None,
    toolkit_version: tuple[int, int] | None,
    driver_cuda_version: tuple[int, int] | None,
    runtime_symbol: object,
    wsl: bool,
) -> str:
    if (
        toolkit_version is not None
        and driver_cuda_version is not None
        and toolkit_version > driver_cuda_version
    ):
        return "blocked_by_runtime_version"
    if toolkit_version is not None and toolkit_version < MIN_GREEN_RUNTIME:
        return "blocked_by_runtime_version"
    if runtime_symbol is False:
        return "unavailable"
    if runtime_symbol is not True or compute_capability is None:
        return "unknown"
    # Symbol discovery proves API visibility, not that WSL2 GPU paravirtualization
    # accepts resource creation on this exact device.  The later probe owns that proof.
    if wsl:
        return "unknown"
    return "supported"


def _mps_environment_status(system: str, wsl: bool, binary: str | None) -> str | None:
    if system != "Linux":
        return "blocked_by_environment"
    if wsl:
        return "unknown"
    if binary is None:
        return "unavailable"
    return None


def _mps_status_for_gpu(
    *,
    system: str,
    wsl: bool,
    binary: str | None,
    compute_capability: str | None,
    static_command_visible: bool,
    lspart_responded: bool,
) -> str:
    environment = _mps_environment_status(system, wsl, binary)
    if environment is not None:
        return environment
    if compute_capability is not None:
        try:
            if float(compute_capability) < 8.0:
                return "unsupported"
        except ValueError:
            return "unknown"
    if lspart_responded or static_command_visible:
        return "supported"
    return "unknown"


def inspect_gpu_sharing(
    *,
    roles: Iterable[object] = (),
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    system: str | None = None,
    wsl: bool | None = None,
    inside_container: bool | None = None,
    _run=subprocess.run,
    _gpu_run=subprocess.check_output,
    _which=shutil.which,
    _find_spec=importlib.util.find_spec,
    _symbol_probe: Callable[[], Mapping[str, Mapping[str, object]]] = _default_symbol_probe,
) -> dict[str, object]:
    """Return conservative, deterministic capability evidence without mutation."""
    if timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout must be > 0 and <= {MAX_TIMEOUT_SECONDS:g} seconds")
    system = platform.system() if system is None else system
    wsl = _is_wsl() if wsl is None else wsl
    inside_container = os.path.exists("/.dockerenv") if inside_container is None else inside_container
    warnings: list[str] = []

    observed = gpu_inventory.list_gpus(_run=_gpu_run, timeout=timeout)
    detail_probe = _bounded_run(
        [
            "nvidia-smi",
            "--query-gpu=index,uuid,name,compute_cap,driver_version",
            "--format=csv,noheader,nounits",
        ],
        timeout=timeout,
        _run=_run,
    )
    details: list[dict[str, object]] = []
    if detail_probe.state == "ok":
        details, detail_warnings = _parse_gpu_details(detail_probe.stdout)
        warnings.extend(detail_warnings)
    else:
        warnings.append(f"extended NVIDIA GPU query {detail_probe.state}")

    sm_probe = _bounded_run(
        [
            "nvidia-smi",
            "--query-gpu=index,uuid,multiprocessor_count",
            "--format=csv,noheader,nounits",
        ],
        timeout=timeout,
        _run=_run,
    )
    sm_counts: dict[str, int] = {}
    if sm_probe.state == "ok":
        sm_counts, sm_warnings = _parse_sm_counts(sm_probe.stdout)
        warnings.extend(sm_warnings)
    else:
        warnings.append(f"NVIDIA SM-count query {sm_probe.state}")
    for row in details:
        row["sm_count"] = sm_counts.get(str(row["uuid"]))

    if not observed and details:
        observed = [
            {"index": row["index"], "uuid": row["uuid"], "name": row["name"]}
            for row in details
        ]
    if not observed:
        warnings.append("no NVIDIA GPUs were discoverable")

    role_by_uuid: dict[str, str] = {}
    configured_roles = tuple(roles)
    if configured_roles:
        try:
            resolved_roles = gpu_inventory.resolve_gpu_roles(configured_roles, _run=_gpu_run)
            role_by_uuid = {row["uuid"]: row["role"] for row in resolved_roles}
        except gpu_inventory.GpuRoleResolutionError as exc:
            warnings.append(f"GPU role resolution failed: {exc}")

    detail_by_uuid = {str(row["uuid"]): row for row in details}
    header_probe = _bounded_run(["nvidia-smi"], timeout=timeout, _run=_run)
    driver_cuda_version = _parse_version(header_probe.stdout) if header_probe.state == "ok" else None
    if header_probe.state not in {"ok", "missing"}:
        warnings.append(f"NVIDIA driver CUDA-version query {header_probe.state}")

    nvcc_path = _which("nvcc")
    nvcc_probe = (
        _bounded_run([nvcc_path, "--version"], timeout=timeout, _run=_run)
        if nvcc_path
        else CommandProbe("missing")
    )
    toolkit_version = _parse_version(f"{nvcc_probe.stdout}\n{nvcc_probe.stderr}")
    if nvcc_probe.state in {"timeout", "permission_denied", "error"}:
        warnings.append(f"CUDA toolkit version query {nvcc_probe.state}")
    if (
        toolkit_version is not None
        and driver_cuda_version is not None
        and toolkit_version > driver_cuda_version
    ):
        warnings.append(
            "CUDA toolkit version exceeds the driver-reported CUDA compatibility version"
        )

    symbols = _symbol_probe()
    runtime_symbol = symbols.get("runtime", {}).get("present")
    driver_symbol = symbols.get("driver", {}).get("present")

    docker_path = _which("docker")
    docker_probe = (
        _bounded_run(
            [docker_path, "version", "--format", "{{json .Server.Version}}"],
            timeout=timeout,
            _run=_run,
        )
        if docker_path
        else CommandProbe("missing")
    )
    if docker_probe.state in {"timeout", "permission_denied", "error"}:
        warnings.append(f"Docker engine query {docker_probe.state}")

    mps_path = _which("nvidia-cuda-mps-control")
    mps_help = CommandProbe("missing")
    mps_servers = CommandProbe("missing")
    mps_partitions = CommandProbe("missing")
    static_visible = False
    if system == "Linux" and mps_path:
        mps_help = _bounded_run([mps_path, "--help"], timeout=timeout, _run=_run)
        help_text = f"{mps_help.stdout}\n{mps_help.stderr}".lower()
        static_visible = any(
            marker in help_text for marker in ("static-partition", "sm_partition", "lspart")
        )
        mps_servers = _bounded_run(
            [mps_path], timeout=timeout, input_text="get_server_list\n", _run=_run
        )
        if mps_servers.state == "ok":
            mps_partitions = _bounded_run(
                [mps_path], timeout=timeout, input_text="lspart\n", _run=_run
            )
        for label, probe in (("MPS daemon", mps_servers), ("MPS partition", mps_partitions)):
            if probe.state in {"timeout", "permission_denied"}:
                warnings.append(f"{label} query {probe.state}")

    server_error = f"{mps_servers.stdout}\n{mps_servers.stderr}".lower()
    if mps_servers.state == "ok":
        daemon_status = "running"
    elif any(marker in server_error for marker in _MPS_STOPPED_MARKERS):
        daemon_status = "stopped"
    elif mps_servers.state == "missing":
        daemon_status = "unavailable"
    else:
        daemon_status = "unreadable"
    lspart_responded = mps_partitions.state == "ok"

    gpu_rows = []
    for device in sorted(observed, key=lambda row: (int(row["index"]), str(row["uuid"]))):
        uuid = str(device["uuid"])
        detail = detail_by_uuid.get(uuid, {})
        compute_capability = detail.get("compute_capability")
        green_status = _green_status(
            compute_capability=compute_capability if isinstance(compute_capability, str) else None,
            toolkit_version=toolkit_version,
            driver_cuda_version=driver_cuda_version,
            runtime_symbol=runtime_symbol,
            wsl=wsl,
        )
        mps_status = _mps_status_for_gpu(
            system=system,
            wsl=wsl,
            binary=mps_path,
            compute_capability=compute_capability if isinstance(compute_capability, str) else None,
            static_command_visible=static_visible,
            lspart_responded=lspart_responded,
        )
        assert green_status in STATUSES and mps_status in STATUSES
        gpu_rows.append(
            {
                "uuid": uuid,
                "role": role_by_uuid.get(uuid),
                "runtime_index": int(device["index"]),
                "name": str(device.get("name", "")),
                "compute_capability": compute_capability,
                "sm_count": detail.get("sm_count"),
                "green_context": {
                    "status": green_status,
                    "evidence": [
                        {
                            "source": "cuda_runtime_symbol",
                            "symbol": "cudaGreenCtxCreate",
                            "present": runtime_symbol,
                        },
                        {
                            "source": "cuda_driver_symbol",
                            "symbol": "cuGreenCtxCreate",
                            "present": driver_symbol,
                        },
                    ],
                    "limitation": "no Green Context was created",
                },
                "mps_static_partitioning": {
                    "status": mps_status,
                    "daemon_status": daemon_status,
                    "evidence": [
                        {"source": "control_binary", "path": mps_path},
                        {"source": "static_command_visible", "value": static_visible},
                        {"source": "lspart_responded", "value": lspart_responded},
                    ],
                    "limitation": "no MPS daemon or partition was created or changed",
                },
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "operation": "gpu_sharing_inspect",
        "mutated_state": False,
        "environment": {
            "system": system,
            "wsl": wsl,
            "inside_container": inside_container,
            "driver_version": next(
                (row.get("driver_version") for row in details if row.get("driver_version")),
                None,
            ),
            "driver_cuda_compatibility": _version_text(driver_cuda_version),
            "host_cuda_toolkit": {
                "status": "supported" if toolkit_version else "unavailable",
                "version": _version_text(toolkit_version),
                "nvcc": nvcc_path,
            },
            "host_cuda_runtime": {
                "status": (
                    "supported"
                    if runtime_symbol is True
                    else "unavailable"
                    if runtime_symbol is False
                    else "unknown"
                ),
                "library": symbols.get("runtime", {}).get("library"),
                "green_context_symbol": runtime_symbol,
            },
            "container_cuda_runtime": {
                "status": (
                    "supported"
                    if inside_container and runtime_symbol is True
                    else "unknown"
                ),
                "reason": (
                    "inspector is running inside the container"
                    if inside_container
                    else "no container was started or entered by this read-only inspection"
                ),
            },
            "docker_engine": {
                "status": "supported" if docker_probe.state == "ok" else "unavailable",
                "version": docker_probe.stdout.strip('"') if docker_probe.state == "ok" else None,
            },
        },
        "frameworks": {
            "pytorch_green_context": _probe_module("torch", _find_spec=_find_spec),
            "flashinfer_green_context": _probe_module("flashinfer", _find_spec=_find_spec),
        },
        "mps": {
            "control_binary": mps_path,
            "daemon_status": daemon_status,
            "read_only_commands": sorted(value.strip() for value in _MPS_READ_ONLY_INPUTS),
        },
        "gpus": gpu_rows,
        "warnings": warnings,
    }


def _probe_service_errors(
    service: object, gpu_uuid: str, compose_path: Path
) -> list[str]:
    """Return fail-closed errors for the rendered one-shot Compose service."""
    if not isinstance(service, dict):
        return [f"Compose service {DEFAULT_PROBE_SERVICE!r} is missing"]
    errors: list[str] = []
    environment = service.get("environment")
    if not isinstance(environment, dict) or environment.get("CUDA_VISIBLE_DEVICES") != gpu_uuid:
        errors.append("CUDA_VISIBLE_DEVICES is not pinned to the requested GPU UUID")
    devices = (
        service.get("deploy", {})
        .get("resources", {})
        .get("reservations", {})
        .get("devices", [])
    )
    device_ids = [
        value
        for device in devices
        if isinstance(device, dict)
        for value in device.get("device_ids", [])
    ] if isinstance(devices, list) else []
    if device_ids != [gpu_uuid]:
        errors.append("Compose GPU reservation is not pinned only to the requested UUID")
    if service.get("read_only") is not True:
        errors.append("container root filesystem is not read-only")
    if service.get("privileged") is True:
        errors.append("container is privileged")
    if set(service.get("cap_drop", [])) != {"ALL"}:
        errors.append("container does not drop all Linux capabilities")
    if "no-new-privileges:true" not in service.get("security_opt", []):
        errors.append("container does not set no-new-privileges")
    if service.get("ports"):
        errors.append("probe service publishes network ports")
    if service.get("restart") != "no":
        errors.append("probe service restart policy is not 'no'")
    if DEFAULT_PROBE_PROFILE not in service.get("profiles", []):
        errors.append("probe service is not protected by its explicit Compose profile")
    if service.get("image") != DEFAULT_PROBE_IMAGE:
        errors.append("probe image is not the exact reviewed digest")
    if service.get("platform") != "linux/amd64":
        errors.append("probe platform is not pinned to linux/amd64")
    if service.get("entrypoint") != ["/bin/bash", "-lc"]:
        errors.append("probe entrypoint does not match the reviewed contract")
    expected_command = (
        "nvcc -std=c++17 -O2 /opt/anvil-gpu-sharing/inspect.cu "
        "-lcuda -ldl -o /run/anvil/gpu-sharing-inspect "
        "&& /run/anvil/gpu-sharing-inspect"
    )
    command = " ".join(" ".join(str(value).split()) for value in service.get("command", []))
    if command != expected_command:
        errors.append("probe command does not match the reviewed inspection command")
    volumes = service.get("volumes", [])
    expected_source = (compose_path.parent / "gpu-sharing").resolve()
    if not isinstance(volumes, list) or len(volumes) != 1:
        errors.append("probe must have exactly one reviewed read-only source bind")
    else:
        volume = volumes[0]
        if not isinstance(volume, dict) or (
            volume.get("type") != "bind"
            or Path(str(volume.get("source", ""))).resolve() != expected_source
            or volume.get("target") != "/opt/anvil-gpu-sharing"
            or volume.get("read_only") is not True
        ):
            errors.append("probe source bind does not match the reviewed read-only path")
    source_path = expected_source / "inspect.cu"
    try:
        normalized_source = source_path.read_text(encoding="utf-8").replace("\r\n", "\n")
        source_digest = hashlib.sha256(normalized_source.encode("utf-8")).hexdigest()
    except OSError as exc:
        errors.append(f"reviewed probe source is unreadable: {exc}")
    else:
        if source_digest != REVIEWED_PROBE_SOURCE_SHA256:
            errors.append("probe source does not match the reviewed non-creating implementation")
    return errors


def probe_gpu_sharing(
    *,
    compose_file: str | os.PathLike[str],
    gpu_uuid: str,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    dry_run: bool = True,
    _run=subprocess.run,
    _which=shutil.which,
) -> dict[str, object]:
    """Audit and optionally run the reviewed one-shot CUDA prerequisite probe.

    The live path creates a temporary Docker container and may populate Docker's
    image cache, but the reviewed CUDA source does not create a CUDA context or
    workload. The CLI dispatcher must authorize live execution with ``--confirm``.
    """
    if not _GPU_UUID_RE.fullmatch(gpu_uuid):
        raise ValueError("gpu_uuid must be a full NVIDIA GPU UUID")
    if timeout <= 0 or timeout > MAX_PROBE_TIMEOUT_SECONDS:
        raise ValueError(
            f"timeout must be > 0 and <= {MAX_PROBE_TIMEOUT_SECONDS:g} seconds"
        )
    compose_path = Path(compose_file).expanduser().resolve()
    if not compose_path.is_file():
        raise ValueError(f"Compose file does not exist: {compose_path}")
    docker = _which("docker")
    if not docker:
        raise ValueError("docker executable is unavailable")

    environment = os.environ.copy()
    environment["FAST_GPU_UUID"] = gpu_uuid
    config_command = [
        docker,
        "compose",
        "-f",
        str(compose_path),
        "--profile",
        DEFAULT_PROBE_PROFILE,
        "config",
        "--format",
        "json",
    ]
    run_command = [
        docker,
        "compose",
        "-f",
        str(compose_path),
        "--profile",
        DEFAULT_PROBE_PROFILE,
        "run",
        "--rm",
        "--no-deps",
        DEFAULT_PROBE_SERVICE,
    ]
    def run_compose(argv, **kwargs):
        return _run(argv, env=environment, cwd=str(compose_path.parent), **kwargs)

    config_probe = _bounded_run(
        config_command,
        timeout=min(timeout, 60.0),
        _run=run_compose,
    )
    if config_probe.state != "ok":
        raise ValueError(
            "Compose probe configuration could not be rendered: "
            + (config_probe.stderr or config_probe.state)
        )
    try:
        rendered = json.loads(config_probe.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Compose returned invalid JSON configuration: {exc}") from None
    service = rendered.get("services", {}).get(DEFAULT_PROBE_SERVICE)
    safety_errors = _probe_service_errors(service, gpu_uuid, compose_path)
    if safety_errors:
        raise ValueError("unsafe GPU-sharing probe configuration: " + "; ".join(safety_errors))

    base: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "operation": "gpu_sharing_probe",
        "dry_run": dry_run,
        "confirmed": not dry_run,
        "gpu_uuid": gpu_uuid,
        "compose_file": str(compose_path),
        "compose_profile": DEFAULT_PROBE_PROFILE,
        "compose_service": DEFAULT_PROBE_SERVICE,
        "command": run_command,
        "safety_contract": {
            "gpu_state_mutation_expected": False,
            "cuda_context_created_expected": False,
            "workload_launched_expected": False,
            "temporary_container": True,
            "image_cache_may_change": True,
            "primary_display_safe_only_for_inspection": True,
        },
        "compose_audit": {"ok": True, "errors": []},
    }
    if dry_run:
        return {**base, "ok": True, "executed": False, "result": None}

    run_probe = _bounded_run(
        run_command,
        timeout=timeout,
        _run=run_compose,
    )
    if run_probe.state != "ok":
        return {
            **base,
            "ok": False,
            "executed": True,
            "result": None,
            "error": run_probe.stderr or run_probe.state,
        }
    try:
        result = json.loads(run_probe.stdout)
    except json.JSONDecodeError as exc:
        return {
            **base,
            "ok": False,
            "executed": True,
            "result": None,
            "error": f"probe returned invalid JSON: {exc}",
        }
    contract_ok = (
        isinstance(result, dict)
        and result.get("ok") is True
        and result.get("mutated_state") is False
        and result.get("created_context") is False
        and result.get("launched_workload") is False
        and result.get("cuda_visible_devices") == gpu_uuid
        and result.get("gpu", {}).get("uuid") == gpu_uuid
    )
    return {
        **base,
        "ok": contract_ok,
        "executed": True,
        "result": result,
        **({} if contract_ok else {"error": "probe result violated the non-mutating contract"}),
    }


def _reference_id(value: str | None, prefix: str) -> str | None:
    if value is None:
        return None
    expected = f"{prefix}:"
    return value[len(expected):] if value.startswith(expected) else value


def _topology_roles(args: argparse.Namespace) -> tuple[object, ...]:
    if not args.topology:
        return ()
    topology = load_topology(args.topology, args.topology_overlay)
    target_host = None
    if args.target and args.target.startswith("host:"):
        target_host = _reference_id(args.target, "host")
    if target_host is None:
        identity = resolve_command_identity(
            topology,
            command_host=args.command_host,
            command_runtime=args.command_runtime,
        )
        assert identity is not None
        target_host = identity.host.id
    return tuple(role for role in topology.gpu_roles if role.host == target_host)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anvil-serving host gpu-sharing inspect")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    # Dispatcher-owned topology options are forwarded so this leaf can attach
    # stable declared GPU roles to transient runtime observations.
    parser.add_argument("--topology")
    parser.add_argument("--topology-overlay")
    parser.add_argument("--command-host")
    parser.add_argument("--command-runtime")
    parser.add_argument("--target")
    parser.add_argument("--transport", default="auto")
    parser.add_argument("--experimental-model-workload", action="store_true")
    return parser


def build_probe_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anvil-serving host gpu-sharing probe")
    parser.add_argument("--compose-file", default=str(DEFAULT_PROBE_COMPOSE_FILE))
    parser.add_argument("--gpu-uuid", required=True)
    parser.add_argument("--timeout", type=float, default=DEFAULT_PROBE_TIMEOUT_SECONDS)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv[:1] == ["probe"]:
        args = build_probe_parser().parse_args(raw_argv[1:])
        try:
            data = probe_gpu_sharing(
                compose_file=args.compose_file,
                gpu_uuid=args.gpu_uuid,
                timeout=args.timeout,
                dry_run=args.dry_run or not confirmation_authorized(),
            )
        except ValueError as exc:
            build_probe_parser().error(str(exc))
        print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True))
        return 0 if data["ok"] else 1

    args = build_parser().parse_args(raw_argv)
    try:
        data = inspect_gpu_sharing(roles=_topology_roles(args), timeout=args.timeout)
    except ValueError as exc:
        build_parser().error(str(exc))
    print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True))
    return 0
