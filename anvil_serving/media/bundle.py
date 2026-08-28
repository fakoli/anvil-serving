"""Managed inventory and staging for pinned ComfyUI workflow assets."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import urllib.parse
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from .errors import MediaError


DEFAULT_LOCK = Path(__file__).resolve().parents[1] / "_media_workflows" / "bundle.lock.json"
BUNDLE_SCHEMA = "anvil-serving.media-bundle-lock/v1"
DEFAULT_HEADROOM_BYTES = 5 * 1024**3
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_TARGET_RE = re.compile(r"^[A-Za-z0-9._/-]{1,512}$")
_URL_RE = re.compile(r"https?://\S+")

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _named_volume(value: str, label: str) -> str:
    if not isinstance(value, str) or not _NAME_RE.fullmatch(value):
        raise MediaError("media_bundle_invalid", f"{label} must be a named Docker volume")
    return value


def _target(value: Any) -> str:
    if not isinstance(value, str) or not _TARGET_RE.fullmatch(value):
        raise MediaError("media_bundle_invalid", "model target is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
        raise MediaError("media_bundle_invalid", "model target is unsafe")
    return value


def _read_lock(path: str | Path) -> Mapping[str, Any]:
    source = Path(path).expanduser().resolve(strict=False)
    try:
        if source.stat().st_size > 2 * 1024 * 1024:
            raise MediaError("media_bundle_invalid", "media bundle lock exceeds its byte limit")
        raw = json.loads(source.read_text(encoding="utf-8"))
    except MediaError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MediaError("media_bundle_invalid", "media bundle lock is unavailable or invalid") from exc
    if not isinstance(raw, Mapping) or raw.get("schema") != BUNDLE_SCHEMA:
        raise MediaError("media_bundle_invalid", "media bundle lock schema is invalid")
    staging = raw.get("staging")
    image = staging.get("container") if isinstance(staging, Mapping) else None
    if not isinstance(image, str) or "@sha256:" not in image:
        raise MediaError("media_bundle_invalid", "media staging container is not digest-pinned")
    workflows = raw.get("workflows")
    if not isinstance(workflows, list) or not workflows or len(workflows) > 64:
        raise MediaError("media_bundle_invalid", "media workflow lock list is invalid")
    return raw


def _workflow(lock: Mapping[str, Any], workflow_id: str, version: str) -> Mapping[str, Any]:
    matches = [
        item
        for item in lock["workflows"]
        if isinstance(item, Mapping)
        and item.get("id") == workflow_id
        and item.get("version") == version
    ]
    if len(matches) != 1:
        raise MediaError("workflow_not_found", "named workflow version is not present in the bundle lock", status=404)
    workflow = matches[0]
    if not _HEX64_RE.fullmatch(str(workflow.get("graph_sha256", ""))):
        raise MediaError("media_bundle_invalid", "workflow graph identity is invalid")
    models = workflow.get("models")
    if not isinstance(models, list) or not models or len(models) > 32:
        raise MediaError("media_bundle_invalid", "workflow model list is invalid")
    seen: set[str] = set()
    for model in models:
        if not isinstance(model, Mapping):
            raise MediaError("media_bundle_invalid", "workflow model entry is invalid")
        repository = model.get("repository")
        revision = model.get("revision")
        path = model.get("path")
        target = _target(model.get("target"))
        size = model.get("size")
        digest = model.get("sha256")
        if (
            not isinstance(repository, str)
            or repository.count("/") != 1
            or not all(repository.split("/"))
            or not _HEX40_RE.fullmatch(str(revision or ""))
            or not isinstance(path, str)
            or not _TARGET_RE.fullmatch(path)
            or PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 1
            or not _HEX64_RE.fullmatch(str(digest or ""))
            or target in seen
        ):
            raise MediaError("media_bundle_invalid", "workflow model identity is invalid")
        seen.add(target)
    return workflow


def _run(
    argv: list[str],
    *,
    runner: Runner,
    error_code: str,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = runner(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise MediaError(error_code, "Docker could not execute the managed media bundle operation", status=503) from exc
    return completed


def _volume_exists(volume: str, *, runner: Runner) -> bool:
    completed = _run(
        ["docker", "volume", "inspect", volume],
        runner=runner,
        error_code="media_bundle_inventory_failed",
    )
    return completed.returncode == 0


def _asset_probe(
    volume: str,
    image: str,
    model: Mapping[str, Any],
    *,
    runner: Runner,
) -> dict[str, Any]:
    target = _target(model["target"])
    quoted = shlex.quote("/models/" + target)
    script = (
        f"if test ! -f {quoted}; then exit 44; fi; "
        f"stat -c '%s' {quoted}; sha256sum {quoted} | cut -d ' ' -f 1"
    )
    completed = _run(
        [
            "docker", "run", "--rm", "--entrypoint", "sh",
            "--mount", f"type=volume,source={volume},target=/models,readonly",
            image, "-c", script,
        ],
        runner=runner,
        error_code="media_bundle_inventory_failed",
    )
    result: dict[str, Any] = {
        "target": target,
        "expectedBytes": model["size"],
        "expectedSha256": model["sha256"],
        "state": "missing",
    }
    if completed.returncode == 44:
        return result
    if completed.returncode != 0:
        raise MediaError(
            "media_bundle_inventory_failed",
            "managed media model inventory failed",
            status=503,
            details={"target": target, "exitCode": completed.returncode},
        )
    lines = completed.stdout.strip().splitlines()
    try:
        observed_size = int(lines[0])
        observed_digest = lines[1]
    except (IndexError, ValueError) as exc:
        raise MediaError("media_bundle_inventory_failed", "managed media model inventory returned invalid data") from exc
    result.update(
        {
            "observedBytes": observed_size,
            "observedSha256": observed_digest,
            "state": (
                "exact"
                if observed_size == model["size"] and observed_digest == model["sha256"]
                else "mismatch"
            ),
        }
    )
    return result


def inventory(
    workflow_id: str,
    version: str,
    *,
    lock_path: str | Path = DEFAULT_LOCK,
    models_volume: str,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Return an exact, bounded inventory for one pinned workflow."""

    volume = _named_volume(models_volume, "models volume")
    lock = _read_lock(lock_path)
    workflow = _workflow(lock, workflow_id, version)
    present = _volume_exists(volume, runner=runner)
    image = lock["staging"]["container"]
    assets = (
        [_asset_probe(volume, image, model, runner=runner) for model in workflow["models"]]
        if present
        else [
            {
                "target": _target(model["target"]),
                "expectedBytes": model["size"],
                "expectedSha256": model["sha256"],
                "state": "missing",
            }
            for model in workflow["models"]
        ]
    )
    return {
        "schema": "anvil-serving.media-bundle-inventory/v1",
        "workflow": {"id": workflow_id, "version": version, "graphSha256": workflow["graph_sha256"]},
        "modelsVolume": volume,
        "volumeExists": present,
        "assets": assets,
        "ready": bool(assets) and all(item["state"] == "exact" for item in assets),
    }


def _free_bytes(volume: str, image: str, *, runner: Runner) -> int:
    completed = _run(
        [
            "docker", "run", "--rm", "--entrypoint", "sh",
            "--mount", f"type=volume,source={volume},target=/models",
            image, "-c", "df -B1 /models | tail -1 | awk '{print $4}'",
        ],
        runner=runner,
        error_code="media_bundle_stage_failed",
    )
    try:
        return int(completed.stdout.strip()) if completed.returncode == 0 else 0
    except ValueError:
        return 0


def _prepare_layout(
    models_volume: str,
    user_volume: str,
    image: str,
    *,
    runtime_uid: int,
    runtime_gid: int,
    runner: Runner,
) -> None:
    script = (
        "mkdir -p /models/diffusion_models /models/text_encoders /models/vae "
        "/models/loras /models/checkpoints /user/output /user/user /user/input; "
        f"chown -R {runtime_uid}:{runtime_gid} /user/output /user/user /user/input"
    )
    completed = _run(
        [
            "docker", "run", "--rm", "--user", "0:0", "--entrypoint", "sh",
            "--mount", f"type=volume,source={models_volume},target=/models",
            "--mount", f"type=volume,source={user_volume},target=/user",
            image, "-c", script,
        ],
        runner=runner,
        error_code="media_bundle_stage_failed",
    )
    if completed.returncode != 0:
        raise MediaError(
            "media_bundle_stage_failed",
            "managed media volume layout preparation failed",
            status=503,
            details={"exitCode": completed.returncode},
        )


def _download_asset(
    volume: str,
    image: str,
    model: Mapping[str, Any],
    *,
    runner: Runner,
) -> None:
    target = _target(model["target"])
    final = "/models/" + target
    partial = final + ".anvil-part"
    parent = str(PurePosixPath(final).parent)
    encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in model["path"].split("/"))
    url = (
        "https://huggingface.co/"
        + model["repository"]
        + "/resolve/"
        + model["revision"]
        + "/"
        + encoded_path
        + "?download=true"
    )
    size_check = f"test \"$(stat -c '%s' {shlex.quote(partial)})\" = {model['size']}"
    digest_check = (
        f"echo {shlex.quote(model['sha256'] + '  ' + partial)} | sha256sum --check"
    )
    script = (
        f"set -eu; mkdir -p {shlex.quote(parent)}; "
        f"test ! -e {shlex.quote(final)}; "
        f"if test -f {shlex.quote(partial)} && {size_check} && {digest_check}; then :; "
        f"else curl --silent --show-error --fail --location --retry 5 --retry-all-errors "
        f"--continue-at - --output {shlex.quote(partial)} {shlex.quote(url)}; fi; "
        f"{size_check}; {digest_check}; mv {shlex.quote(partial)} {shlex.quote(final)}"
    )
    completed = _run(
        [
            "docker", "run", "--rm", "--user", "0:0", "--entrypoint", "sh",
            "--mount", f"type=volume,source={volume},target=/models",
            image, "-c", script,
        ],
        runner=runner,
        error_code="media_bundle_stage_failed",
    )
    if completed.returncode != 0:
        diagnostic = _URL_RE.sub("<url>", completed.stderr.strip())[-512:]
        raise MediaError(
            "media_bundle_stage_failed",
            "pinned media model download or verification failed",
            status=503,
            details={
                "target": target,
                "exitCode": completed.returncode,
                "diagnostic": diagnostic or "download_or_verification_command_failed",
            },
        )


def stage(
    workflow_id: str,
    version: str,
    *,
    lock_path: str | Path = DEFAULT_LOCK,
    models_volume: str,
    user_volume: str,
    dry_run: bool,
    runtime_uid: int = 1000,
    runtime_gid: int = 1000,
    headroom_bytes: int = DEFAULT_HEADROOM_BYTES,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Preview or add exact locked assets; never replace or delete model files."""

    model_volume = _named_volume(models_volume, "models volume")
    output_volume = _named_volume(user_volume, "user volume")
    if (
        isinstance(runtime_uid, bool)
        or isinstance(runtime_gid, bool)
        or not 1 <= runtime_uid <= 65535
        or not 1 <= runtime_gid <= 65535
        or isinstance(headroom_bytes, bool)
        or headroom_bytes < 0
    ):
        raise MediaError("media_bundle_invalid", "media staging ownership or headroom is invalid")
    lock = _read_lock(lock_path)
    workflow = _workflow(lock, workflow_id, version)
    before = inventory(
        workflow_id,
        version,
        lock_path=lock_path,
        models_volume=model_volume,
        runner=runner,
    )
    mismatches = [item["target"] for item in before["assets"] if item["state"] == "mismatch"]
    if mismatches:
        raise MediaError(
            "media_bundle_conflict",
            "existing model files do not match the pinned bundle and were preserved",
            status=409,
            details={"targets": mismatches},
        )
    missing = [item for item in workflow["models"] if next(
        row for row in before["assets"] if row["target"] == item["target"]
    )["state"] == "missing"]
    result = {
        "schema": "anvil-serving.media-bundle-stage/v1",
        "workflow": {"id": workflow_id, "version": version, "graphSha256": workflow["graph_sha256"]},
        "modelsVolume": model_volume,
        "userVolume": output_volume,
        "dryRun": bool(dry_run),
        "missingBytes": sum(item["size"] for item in missing),
        "missingTargets": [_target(item["target"]) for item in missing],
        "applied": False,
        "ready": before["ready"],
    }
    if dry_run:
        return result
    for volume in (model_volume, output_volume):
        if not _volume_exists(volume, runner=runner):
            created = _run(
                ["docker", "volume", "create", volume],
                runner=runner,
                error_code="media_bundle_stage_failed",
            )
            if created.returncode != 0:
                raise MediaError("media_bundle_stage_failed", "managed media volume creation failed", status=503)
    image = lock["staging"]["container"]
    _prepare_layout(
        model_volume,
        output_volume,
        image,
        runtime_uid=runtime_uid,
        runtime_gid=runtime_gid,
        runner=runner,
    )
    available = _free_bytes(model_volume, image, runner=runner)
    required = sum(item["size"] for item in missing) + headroom_bytes
    if available < required:
        raise MediaError(
            "media_bundle_capacity",
            "Docker model volume has insufficient free space for the pinned bundle",
            status=507,
            details={"availableBytes": available, "requiredBytes": required},
        )
    for model in missing:
        _download_asset(model_volume, image, model, runner=runner)
    after = inventory(
        workflow_id,
        version,
        lock_path=lock_path,
        models_volume=model_volume,
        runner=runner,
    )
    if not after["ready"]:
        raise MediaError("media_bundle_stage_failed", "media bundle was not exact after staging", status=503)
    result.update({"applied": True, "ready": True, "assets": after["assets"]})
    return result


__all__ = ["DEFAULT_LOCK", "inventory", "stage"]
