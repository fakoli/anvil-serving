"""Selected SWE image identities and managed paired-image preparation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from .. import model_pull_native
from ..service_runtime.docker import _is_local_endpoint
from .harnesses import _bounded_text
from .jobs import BenchmarkJobError


_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SPEC_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
DEFAULT_MIN_FREE_BYTES = 100 * 1024**3
_IMAGE_FORMAT = (
    '{"image_id":{{json .Id}},"repo_digests":{{json .RepoDigests}}}'
)


def _mapping(value, selected, message):
    if (
        not isinstance(value, dict)
        or not isinstance(selected, list)
        or set(value) != set(selected)
        or any(
            not isinstance(item, str) or not _IMAGE_ID.fullmatch(item)
            for item in value.values()
        )
    ):
        raise BenchmarkJobError("bad_swe_image_policy", message)
    return {instance: value[instance] for instance in selected}


def validate_paired_image_ids(value, selected):
    if value is None:
        return None
    return _mapping(
        value,
        selected,
        "paired image IDs must match every selected instance exactly",
    )


def validate_paired_image_repo_digests(value, selected):
    return _mapping(
        value,
        selected,
        "paired image registry digests must match every selected instance exactly",
    )


def instance_image_tag(instance):
    return (
        "swebench/sweb.eval.x86_64."
        + instance.replace("__", "_1776_").lower()
        + ":latest"
    )


def _run(argv, *, runner, cwd, env, timeout):
    """Return one bounded command record suitable for a retained receipt."""
    started = time.monotonic()
    try:
        result = runner(list(argv), cwd, timeout, env)
    except subprocess.TimeoutExpired as exc:
        stdout, stdout_truncated = _bounded_text(exc.stdout)
        stderr, stderr_truncated = _bounded_text(exc.stderr)
        return {
            "command": list(argv),
            "returncode": None,
            "duration_s": round(time.monotonic() - started, 6),
            "stdout": stdout,
            "stderr": stderr,
            "output_truncated": stdout_truncated or stderr_truncated,
            "timed_out": True,
        }
    except OSError as exc:
        stderr, truncated = _bounded_text(str(exc))
        return {
            "command": list(argv),
            "returncode": None,
            "duration_s": round(time.monotonic() - started, 6),
            "stdout": "",
            "stderr": stderr,
            "output_truncated": truncated,
            "timed_out": False,
        }

    stdout, stdout_truncated = _bounded_text(getattr(result, "stdout", b""))
    stderr, stderr_truncated = _bounded_text(getattr(result, "stderr", b""))
    return {
        "command": list(argv),
        "returncode": getattr(result, "returncode", None),
        "duration_s": round(time.monotonic() - started, 6),
        "stdout": stdout,
        "stderr": stderr,
        "output_truncated": bool(
            stdout_truncated
            or stderr_truncated
            or getattr(result, "stdout_truncated", False)
            or getattr(result, "stderr_truncated", False)
        ),
        "timed_out": bool(getattr(result, "timed_out", False)),
    }


def _require_command(command, code, message, *, timeout_code=None):
    if command["timed_out"]:
        raise BenchmarkJobError(timeout_code or f"{code}_timeout", f"{message} timed out")
    if command["returncode"] != 0:
        raise BenchmarkJobError(code, message)


def _json_string(command, code, message):
    _require_command(command, code, message)
    try:
        value = json.loads(command["stdout"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise BenchmarkJobError(code, message) from exc
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or any(character in value for character in "\x00\r\n")
    ):
        raise BenchmarkJobError(code, message)
    return value


def _local_docker(*, executable, runner, env):
    """Pin a local Docker endpoint and identify its physical storage root."""
    commands = []
    context = env.get("DOCKER_CONTEXT")
    if context and (
        not isinstance(context, str)
        or len(context) > 256
        or context.startswith("-")
        or any(ord(character) < 32 or ord(character) == 127 for character in context)
    ):
        raise BenchmarkJobError(
            "docker_context_unavailable",
            "local Docker context selection is invalid",
        )
    endpoint = None if context else env.get("DOCKER_HOST") or None
    if endpoint is None:
        argv = [
            executable,
            "context",
            "inspect",
            "--format",
            "{{json .Endpoints.docker.Host}}",
        ]
        if context:
            argv.append(context)
        command = _run(argv, runner=runner, cwd=None, env=env, timeout=30)
        commands.append(command)
        endpoint = _json_string(
            command,
            "docker_context_unavailable",
            "local Docker context could not be resolved",
        )
    if not isinstance(endpoint, str) or not endpoint.startswith("unix://"):
        raise BenchmarkJobError(
            "nonlocal_container_endpoint",
            "SWE image preparation requires a local Unix Docker endpoint",
        )
    if not _is_local_endpoint(endpoint):
        raise BenchmarkJobError(
            "nonlocal_container_endpoint",
            "SWE image preparation requires a local Unix Docker endpoint",
        )

    prefix = [executable, "--host", endpoint]
    info = _run(
        [*prefix, "info", "--format", "{{json .DockerRootDir}}"],
        runner=runner,
        cwd=None,
        env=env,
        timeout=30,
    )
    commands.append(info)
    docker_root = _json_string(
        info,
        "docker_info_unavailable",
        "local Docker storage root could not be resolved",
    )
    if not os.path.isabs(docker_root):
        raise BenchmarkJobError(
            "docker_info_unavailable",
            "local Docker storage root must be an absolute path",
        )
    docker_root = os.path.realpath(docker_root)
    if not os.path.isdir(docker_root):
        raise BenchmarkJobError(
            "docker_info_unavailable",
            "local Docker storage root is not an accessible directory",
        )
    return prefix, endpoint, docker_root, commands


def _inspect(
    expected_id,
    tag,
    *,
    command_prefix,
    runner,
    cwd,
    env,
    command_log=None,
):
    command = _run(
        [
            *command_prefix,
            "image",
            "inspect",
            "--format",
            _IMAGE_FORMAT,
            tag,
        ],
        runner=runner,
        cwd=cwd,
        env=env,
        timeout=30,
    )
    if command_log is not None:
        command_log.append(command)
    if command["timed_out"] or command["returncode"] != 0:
        raise ValueError("missing image")
    try:
        row = json.loads(command["stdout"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("unreadable image observation") from exc
    digests = (row.get("repo_digests") or []) if isinstance(row, dict) else None
    if (
        not isinstance(row, dict)
        or row.get("image_id") != expected_id
        or not isinstance(digests, list)
        or len(digests) > 256
        or any(
            not isinstance(value, str)
            or len(value) > 4096
            or not _IMAGE_ID.fullmatch(value.rsplit("@", 1)[-1])
            for value in digests
        )
    ):
        raise ValueError("image identity differs")
    return {"tag": tag, "image_id": expected_id, "repo_digests": sorted(digests)}


def _has_registry_digest(observation, expected):
    return any(
        value.rsplit("@", 1)[-1] == expected
        for value in observation["repo_digests"]
    )


def observe_paired_images(expected, *, executable, runner, cwd, env):
    """Read selected cached tags only; missing or changed images never trigger pulls."""
    observations = []
    for instance, image_id in expected.items():
        tag = instance_image_tag(instance)
        try:
            observation = _inspect(
                image_id,
                tag,
                command_prefix=[executable],
                runner=runner,
                cwd=cwd,
                env=env,
            )
        except Exception as exc:
            raise BenchmarkJobError(
                "swe_image_identity_mismatch",
                "selected cached SWE image is missing, changed, or unreadable",
            ) from exc
        observations.append({"instance_id": instance, **observation})
    return observations


def prepare_paired_images(
    image_ids,
    repo_digests,
    *,
    executable,
    runner,
    cwd,
    env,
    receipt_path,
    lock_root,
    ownership_id,
    run_id,
    spec_sha256,
    worker_id,
    observed_hostname,
    confirm=False,
    min_free_bytes=DEFAULT_MIN_FREE_BYTES,
    disk_usage=shutil.disk_usage,
):
    """Preview or prepare the exact images bound by one frozen SWE job spec."""
    selected = list(image_ids or {})
    image_ids = _mapping(
        image_ids,
        selected,
        "paired image IDs must match every selected instance exactly",
    )
    repo_digests = validate_paired_image_repo_digests(repo_digests, selected)
    if (
        not _SPEC_SHA256.fullmatch(str(spec_sha256))
        or isinstance(min_free_bytes, bool)
        or not isinstance(min_free_bytes, int)
        or min_free_bytes < DEFAULT_MIN_FREE_BYTES
    ):
        raise BenchmarkJobError(
            "bad_swe_image_policy",
            "SWE image preparation requires a bound specification and 100 GiB reserve",
        )
    images = [
        {
            "instance_id": instance,
            "tag": instance_image_tag(instance),
            "image_id": image_ids[instance],
            "registry_digest": repo_digests[instance],
        }
        for instance in selected
    ]
    worker = {"declared_id": worker_id, "observed_hostname": observed_hostname}
    if not confirm:
        return {
            "status": "preview",
            "ownership_id": ownership_id,
            "run_id": run_id,
            "spec_sha256": spec_sha256,
            "worker": worker,
            "images": images,
            "min_free_bytes": min_free_bytes,
            "deferred": [
                "local Docker endpoint inspection",
                "Docker storage admission",
                "pinned Docker pull",
                "official tag update",
                "receipt write",
            ],
        }

    prefix, endpoint, docker_root, discovery = _local_docker(
        executable=executable,
        runner=runner,
        env=env,
    )
    target = Path(receipt_path)
    receipt = {
        "schema": "anvil-serving.swe-image-preparation/v1",
        "status": "running",
        "ownership_id": ownership_id,
        "run_id": run_id,
        "spec_sha256": spec_sha256,
        "receipt_path": str(target),
        "worker": worker,
        "container_runtime": {
            "endpoint": endpoint,
            "docker_root": docker_root,
            "commands": discovery,
        },
        "min_free_bytes": min_free_bytes,
        "images": [],
    }

    try:
        with model_pull_native._writer(Path(lock_root), "swe-images"):
            with os.fdopen(model_pull_native._reserve_receipt(target), "r+b") as handle:
                def write_receipt():
                    model_pull_native._write_receipt(
                        target, receipt, handle.fileno()
                    )

                write_receipt()
                for image in images:
                    entry = {**image, "status": "running", "commands": []}
                    receipt["images"].append(entry)
                    write_receipt()
                    try:
                        entry["free_bytes_before"] = disk_usage(docker_root).free
                        if entry["free_bytes_before"] < min_free_bytes:
                            raise BenchmarkJobError(
                                "insufficient_disk",
                                "SWE image preparation lacks the required 100 GiB reserve",
                            )
                        pinned = (
                            f"docker.io/{image['tag']}@{image['registry_digest']}"
                        )
                        pull = _run(
                            [*prefix, "pull", "--quiet", pinned],
                            runner=runner,
                            cwd=cwd,
                            env=env,
                            timeout=1800,
                        )
                        entry["commands"].append(pull)
                        _require_command(
                            pull,
                            "swe_image_pull_failed",
                            "pinned SWE image pull failed",
                            timeout_code="swe_image_pull_timeout",
                        )
                        entry["free_bytes_after_pull"] = disk_usage(docker_root).free
                        if entry["free_bytes_after_pull"] < min_free_bytes:
                            raise BenchmarkJobError(
                                "insufficient_disk_after_pull",
                                "pinned SWE image pull crossed the required 100 GiB reserve",
                            )
                        pinned_observation = _inspect(
                            image["image_id"],
                            pinned,
                            command_prefix=prefix,
                            runner=runner,
                            cwd=cwd,
                            env=env,
                            command_log=entry["commands"],
                        )
                        if not _has_registry_digest(
                            pinned_observation, image["registry_digest"]
                        ):
                            raise ValueError("registry digest differs")
                        tag = _run(
                            [*prefix, "tag", pinned, image["tag"]],
                            runner=runner,
                            cwd=cwd,
                            env=env,
                            timeout=30,
                        )
                        entry["commands"].append(tag)
                        _require_command(
                            tag,
                            "swe_image_tag_failed",
                            "verified SWE image could not receive its official tag",
                            timeout_code="swe_image_tag_timeout",
                        )
                        observation = _inspect(
                            image["image_id"],
                            image["tag"],
                            command_prefix=prefix,
                            runner=runner,
                            cwd=cwd,
                            env=env,
                            command_log=entry["commands"],
                        )
                        if not _has_registry_digest(
                            observation, image["registry_digest"]
                        ):
                            raise ValueError("registry digest differs")
                        entry.update(
                            status="verified",
                            pinned_observation=pinned_observation,
                            observation=observation,
                        )
                    except ValueError as exc:
                        entry.update(
                            status="failed", error="swe_image_identity_mismatch"
                        )
                        receipt["status"] = "failed"
                        write_receipt()
                        raise BenchmarkJobError(
                            "swe_image_identity_mismatch",
                            "SWE image identity did not verify",
                        ) from exc
                    except BenchmarkJobError as exc:
                        entry.update(status="failed", error=exc.code)
                        receipt["status"] = "failed"
                        write_receipt()
                        raise
                    except Exception as exc:
                        entry.update(status="failed", error="swe_image_prepare_failed")
                        receipt["status"] = "failed"
                        write_receipt()
                        raise BenchmarkJobError(
                            "swe_image_prepare_failed",
                            "SWE image preparation failed unexpectedly",
                        ) from exc
                    write_receipt()
                receipt["status"] = "verified"
                write_receipt()
    except model_pull_native.NativePullError as exc:
        raise BenchmarkJobError("swe_image_receipt_conflict", str(exc)) from None
    return receipt
