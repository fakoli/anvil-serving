"""Managed preparation and inspection of pinned external benchmark assets."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable, Mapping, Sequence

from .artifacts import atomic_write_json, is_filesystem_root, path_is_within, real_path
from .jobs import BenchmarkJobError, canonical_json_bytes, resolve_owned_run_path, utc_now
from .profiles import validate_profile


HARNESS_ASSETS_SCHEMA = "anvil-serving.benchmark-harness-assets/v1"
SWE_PYTHON_ENVIRONMENT_SCHEMA = "anvil-serving.swe-python-environment/v1"
MAX_HARNESS_OUTPUT_BYTES = 64 * 1024
DEFAULT_HARNESS_COMMAND_TIMEOUT = 1800
CommandRunner = Callable[[Sequence[str], str | None, float], Any]


def resolve_container_binary() -> str | None:
    """Resolve Docker for detached workers whose service PATH may be minimal."""
    discovered = shutil.which("docker")
    if discovered:
        return discovered
    if sys.platform == "darwin":
        for candidate in (
            "/usr/local/bin/docker",
            "/opt/homebrew/bin/docker",
            "/Applications/Docker.app/Contents/Resources/bin/docker",
        ):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


def _default_runner(argv: Sequence[str], cwd: str | None, timeout: float):
    return subprocess.run(
        list(argv),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _bounded_text(value: Any) -> tuple[str, bool]:
    raw = value if isinstance(value, bytes) else str(value or "").encode("utf-8")
    truncated = len(raw) > MAX_HARNESS_OUTPUT_BYTES
    return raw[:MAX_HARNESS_OUTPUT_BYTES].decode("utf-8", "replace"), truncated


def _run(
    runner: CommandRunner,
    argv: Sequence[str],
    *,
    cwd: str | None = None,
    timeout: float = DEFAULT_HARNESS_COMMAND_TIMEOUT,
) -> dict[str, Any]:
    try:
        result = runner(tuple(argv), cwd, timeout)
    except subprocess.TimeoutExpired as exc:
        stdout, stdout_truncated = _bounded_text(exc.stdout)
        stderr, stderr_truncated = _bounded_text(exc.stderr)
        raise BenchmarkJobError(
            "harness_timeout",
            "managed harness command timed out",
            {
                "command": list(argv),
                "stdout": stdout,
                "stderr": stderr,
                "output_truncated": stdout_truncated or stderr_truncated,
            },
        ) from exc
    stdout, stdout_truncated = _bounded_text(getattr(result, "stdout", b""))
    stderr, stderr_truncated = _bounded_text(getattr(result, "stderr", b""))
    return {
        "command": list(argv),
        "returncode": int(getattr(result, "returncode", 1)),
        "stdout": stdout,
        "stderr": stderr,
        "output_truncated": stdout_truncated or stderr_truncated,
    }


def _safe_cache_root(path: str) -> str:
    root = real_path(path)
    if is_filesystem_root(root):
        raise BenchmarkJobError("unsafe_cache_root", "harness cache cannot be a filesystem root")
    Path(root).mkdir(parents=True, exist_ok=True)
    return root


def _cache_path(root: str, name: str, identity: str) -> str:
    path = real_path(os.path.join(root, name, identity))
    if not path_is_within(path, root):
        raise BenchmarkJobError("unsafe_cache_path", "harness cache path escaped its root")
    return path


def _directory_bytes(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def _lock_path(path: str) -> Path:
    return Path(path) / ".anvil-harness-lock.json"


def _existing_lock(path: str, expected: Mapping[str, Any]) -> bool:
    try:
        value = json.loads(_lock_path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return value == dict(expected)


def _venv_python(path: str) -> str:
    relative = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    return os.path.join(path, *relative)


def _normalized_freeze(value: str, *, cache_root: str) -> list[str]:
    normalized_root = real_path(cache_root).replace("\\", "/").rstrip("/")
    packages = []
    for raw in value.splitlines():
        line = raw.strip().replace("\\", "/")
        if not line:
            continue
        packages.append(line.replace(normalized_root, "$CACHE_ROOT"))
    return sorted(packages, key=str.casefold)


def _swe_environment_identity(
    assets: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    required = ("mini-swe-agent", "swe-bench")
    if any(name not in assets for name in required):
        raise BenchmarkJobError(
            "missing_harness_asset", "SWE Python environment requires both source adapters"
        )
    return {
        "schema": SWE_PYTHON_ENVIRONMENT_SCHEMA,
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "platform": platform.system().lower(),
        "architecture": platform.machine().lower(),
        "sources": {
            name: {
                "source": assets[name]["source"],
                "revision": assets[name]["revision"],
            }
            for name in required
        },
    }


def _reuse_swe_python_environment(
    *,
    target: str,
    cache_root: str,
    cache_key: str,
    identity: Mapping[str, Any],
    runner: CommandRunner,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    lock_path = Path(target) / ".anvil-swe-python-environment.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkJobError(
            "harness_environment_mismatch",
            "cached SWE Python environment has no valid ownership record",
        ) from exc
    freeze = _run(
        runner,
        (_venv_python(target), "-m", "pip", "freeze", "--all"),
        cwd=target,
    )
    logs = [freeze]
    observed = _normalized_freeze(freeze["stdout"], cache_root=cache_root)
    if (
        freeze["returncode"] != 0
        or lock.get("identity") != dict(identity)
        or lock.get("resolved_packages") != observed
    ):
        raise BenchmarkJobError(
            "harness_environment_mismatch",
            "cached SWE Python environment no longer matches its recorded inventory",
        )
    return {
        **identity,
        "cache_key": cache_key,
        "executable": os.path.relpath(_venv_python(target), target).replace("\\", "/"),
        "resolved_packages": observed,
        "resolved_packages_sha256": hashlib.sha256(
            canonical_json_bytes(observed)
        ).hexdigest(),
        "reused": True,
    }, logs


def _prepare_swe_python_environment(
    *,
    assets: Mapping[str, Mapping[str, Any]],
    cache_root: str,
    offline: bool,
    max_download_bytes: int,
    runner: CommandRunner,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Install pinned SWE sources into an isolated, inventory-checked venv."""
    identity = _swe_environment_identity(assets)
    environment_id = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    target = _cache_path(cache_root, "swe-python-environments", environment_id)
    cache_key = f"swe-python-environments/{environment_id}"
    logs: list[dict[str, Any]] = []

    if os.path.isdir(target):
        return _reuse_swe_python_environment(
            target=target,
            cache_root=cache_root,
            cache_key=cache_key,
            identity=identity,
            runner=runner,
        )

    if offline:
        raise BenchmarkJobError(
            "harness_assets_offline",
            "isolated SWE Python environment is absent from the offline cache",
        )

    parent = Path(target).parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".swe-python-", dir=str(parent))
    try:
        create = _run(runner, (sys.executable, "-m", "venv", staging))
        logs.append(create)
        if create["returncode"] != 0:
            raise BenchmarkJobError(
                "harness_environment_failure",
                "isolated SWE Python environment could not be created",
                {"step": create},
            )
        python = _venv_python(staging)
        source_paths = [
            real_path(os.path.join(cache_root, assets[name]["cache_key"]))
            for name in ("mini-swe-agent", "swe-bench")
        ]
        if any(not path_is_within(path, cache_root) for path in source_paths):
            raise BenchmarkJobError(
                "unsafe_cache_path", "SWE source path escaped the harness cache"
            )
        install = _run(
            runner,
            (
                python,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                *source_paths,
            ),
            cwd=staging,
        )
        logs.append(install)
        if install["returncode"] != 0:
            raise BenchmarkJobError(
                "harness_environment_failure",
                "pinned SWE harness dependencies could not be installed",
                {"step": install},
            )
        freeze = _run(
            runner,
            (python, "-m", "pip", "freeze", "--all"),
            cwd=staging,
        )
        logs.append(freeze)
        if freeze["returncode"] != 0:
            raise BenchmarkJobError(
                "harness_environment_failure",
                "installed SWE harness inventory could not be recorded",
                {"step": freeze},
            )
        resolved = _normalized_freeze(freeze["stdout"], cache_root=cache_root)
        if not resolved:
            raise BenchmarkJobError(
                "harness_environment_failure", "installed SWE harness inventory is empty"
            )
        cache_bytes = _directory_bytes(staging)
        if cache_bytes > max_download_bytes:
            raise BenchmarkJobError(
                "harness_download_too_large",
                "isolated SWE Python environment exceeded its download bound",
                {"cache_bytes": cache_bytes, "max_download_bytes": max_download_bytes},
            )
        atomic_write_json(
            os.path.join(staging, ".anvil-swe-python-environment.json"),
            {"identity": identity, "resolved_packages": resolved},
        )
        try:
            os.replace(staging, target)
            staging = ""
        except OSError:
            if not os.path.isdir(target):
                raise
            winner, winner_logs = _reuse_swe_python_environment(
                target=target,
                cache_root=cache_root,
                cache_key=cache_key,
                identity=identity,
                runner=runner,
            )
            logs.extend(winner_logs)
            return winner, logs
        return {
            **identity,
            "cache_key": cache_key,
            "executable": os.path.relpath(_venv_python(target), target).replace("\\", "/"),
            "resolved_packages": resolved,
            "resolved_packages_sha256": hashlib.sha256(
                canonical_json_bytes(resolved)
            ).hexdigest(),
            "reused": False,
        }, logs
    finally:
        if staging and path_is_within(real_path(staging), str(parent.resolve())):
            shutil.rmtree(staging, ignore_errors=True)


def _prepare_repository(
    *,
    name: str,
    adapter: Mapping[str, Any],
    cache_root: str,
    offline: bool,
    max_download_bytes: int,
    runner: CommandRunner,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    revision = adapter["revision"]
    target = _cache_path(cache_root, name, revision)
    lock = {
        "kind": adapter["kind"],
        "source": adapter["source"],
        "revision": revision,
    }
    if os.path.isdir(target) and _existing_lock(target, lock):
        return {
            **lock,
            "cache_key": f"{name}/{revision}",
            "cache_bytes": _directory_bytes(target),
            "reused": True,
            "dirty": False,
        }, []
    if offline:
        raise BenchmarkJobError(
            "harness_assets_offline", f"pinned adapter {name!r} is absent from the offline cache"
        )
    parent = Path(target).parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=f".{name}-", dir=str(parent))
    logs = []
    try:
        commands = (
            ("git", "init", "--quiet"),
            ("git", "remote", "add", "origin", adapter["source"]),
            ("git", "fetch", "--depth", "1", "origin", revision),
            ("git", "checkout", "--detach", "FETCH_HEAD"),
            ("git", "rev-parse", "HEAD"),
            ("git", "status", "--porcelain"),
        )
        for argv in commands:
            result = _run(runner, argv, cwd=staging)
            logs.append(result)
            if result["returncode"] != 0:
                raise BenchmarkJobError(
                    "harness_repository_failure",
                    f"pinned adapter {name!r} could not be prepared",
                    {"step": result},
                )
        observed_revision = logs[-2]["stdout"].strip()
        dirty = bool(logs[-1]["stdout"].strip())
        if observed_revision != revision or dirty:
            raise BenchmarkJobError(
                "harness_revision_mismatch",
                f"pinned adapter {name!r} identity did not verify",
                {"observed_revision": observed_revision, "dirty": dirty},
            )
        cache_bytes = _directory_bytes(staging)
        if cache_bytes > max_download_bytes:
            raise BenchmarkJobError(
                "harness_download_too_large",
                f"pinned adapter {name!r} exceeded its download bound",
                {"cache_bytes": cache_bytes, "max_download_bytes": max_download_bytes},
            )
        atomic_write_json(str(_lock_path(staging)), lock)
        try:
            os.replace(staging, target)
            staging = ""
        except OSError:
            if not _existing_lock(target, lock):
                raise
        return {
            **lock,
            "cache_key": f"{name}/{revision}",
            "cache_bytes": cache_bytes,
            "reused": False,
            "dirty": False,
        }, logs
    finally:
        if staging and path_is_within(real_path(staging), str(parent.resolve())):
            shutil.rmtree(staging, ignore_errors=True)


def _prepare_image(
    *,
    name: str,
    adapter: Mapping[str, Any],
    container_binary: str,
    offline: bool,
    runner: CommandRunner,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    image = adapter["image"]
    inspect = _run(runner, (container_binary, "image", "inspect", image))
    logs = [inspect]
    reused = inspect["returncode"] == 0
    if not reused:
        if offline:
            raise BenchmarkJobError(
                "harness_assets_offline", f"digest-pinned image {name!r} is absent offline"
            )
        pull = _run(runner, (container_binary, "pull", image))
        logs.append(pull)
        if pull["returncode"] != 0:
            raise BenchmarkJobError(
                "harness_image_failure",
                f"digest-pinned image {name!r} could not be prepared",
                {"step": pull},
            )
        verify = _run(runner, (container_binary, "image", "inspect", image))
        logs.append(verify)
        if verify["returncode"] != 0:
            raise BenchmarkJobError(
                "harness_image_failure", f"digest-pinned image {name!r} did not verify"
            )
    return {"kind": "image", "image": image, "reused": reused}, logs


def prepare_harness_assets(
    profile: Mapping[str, Any],
    *,
    suite: str,
    run_root: str,
    ownership_id: str,
    run_id: str,
    cache_root: str,
    offline: bool = False,
    max_download_bytes: int = 20 * 1024**3,
    runner: CommandRunner = _default_runner,
) -> dict[str, Any]:
    """Prepare exact profile assets through one bounded, owned product operation."""
    validated = validate_profile(profile)
    if suite not in validated["suites"]:
        raise BenchmarkJobError("unknown_suite", "benchmark suite is unknown")
    if not isinstance(max_download_bytes, int) or not 1 <= max_download_bytes <= 1024**4:
        raise BenchmarkJobError("bad_download_bound", "max_download_bytes is invalid")
    Path(run_root).mkdir(parents=True, exist_ok=True)
    run_path = resolve_owned_run_path(
        run_root, ownership_id=ownership_id, run_id=run_id
    )
    Path(run_path).mkdir(parents=True, exist_ok=True)
    cache = _safe_cache_root(cache_root)
    assets = {}
    command_logs = []
    for name in validated["suites"][suite]["adapters"]:
        adapter = validated["adapters"][name]
        if adapter["kind"] in {"git", "dataset"}:
            asset, logs = _prepare_repository(
                name=name,
                adapter=adapter,
                cache_root=cache,
                offline=offline,
                max_download_bytes=max_download_bytes,
                runner=runner,
            )
        else:
            container_binary = resolve_container_binary()
            if not container_binary:
                raise BenchmarkJobError(
                    "container_capability_absent",
                    "Docker is required to prepare the digest-pinned harness image",
                )
            asset, logs = _prepare_image(
                name=name,
                adapter=adapter,
                container_binary=container_binary,
                offline=offline,
                runner=runner,
            )
        assets[name] = asset
        command_logs.extend(logs)
    python_environment = None
    if suite == "swe":
        python_environment, logs = _prepare_swe_python_environment(
            assets=assets,
            cache_root=cache,
            offline=offline,
            max_download_bytes=max_download_bytes,
            runner=runner,
        )
        command_logs.extend(logs)
    manifest = {
        "schema": HARNESS_ASSETS_SCHEMA,
        "created_at": utc_now(),
        "run_id": run_id,
        "ownership_id": ownership_id,
        "profile": validated["name"],
        "profile_sha256": validated["content_sha256"],
        "suite": suite,
        "platform": platform.system().lower(),
        "architecture": platform.machine().lower(),
        "offline": offline,
        "assets": assets,
        "python_environment": python_environment,
        "command_logs": command_logs,
    }
    manifest["cache_identity"] = hashlib.sha256(
        canonical_json_bytes(
            {
                "profile": manifest["profile_sha256"],
                "assets": assets,
                "python_environment": python_environment,
            }
        )
    ).hexdigest()
    atomic_write_json(os.path.join(run_path, "assets.json"), manifest)
    return manifest


def harness_asset_status(
    *, run_root: str, ownership_id: str, run_id: str
) -> dict[str, Any]:
    path = resolve_owned_run_path(
        run_root,
        ownership_id=ownership_id,
        run_id=run_id,
        relative="assets.json",
    )
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "absent", "run_id": run_id}
    if value.get("schema") != HARNESS_ASSETS_SCHEMA:
        raise BenchmarkJobError("bad_harness_manifest", "harness manifest schema is invalid")
    return {"status": "ready", "manifest": value}


def cleanup_harness_work(*, run_root: str, ownership_id: str, run_id: str) -> dict[str, Any]:
    """Remove only the run-owned work directory; shared caches and evidence survive."""
    work = resolve_owned_run_path(
        run_root, ownership_id=ownership_id, run_id=run_id, relative="work"
    )
    run_path = resolve_owned_run_path(
        run_root, ownership_id=ownership_id, run_id=run_id
    )
    if not path_is_within(work, run_path) or work == run_path:
        raise BenchmarkJobError("unsafe_harness_cleanup", "harness cleanup target is unsafe")
    existed = os.path.isdir(work)
    if existed:
        shutil.rmtree(work)
    return {"cleaned": existed, "scope": "owned-work", "shared_cache_cleaned": False}
