from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from anvil_serving import guard
from anvil_serving.benchmarking import jobs_cli
from anvil_serving.benchmarking.jobs import BenchmarkJobError, JOB_SPEC_SCHEMA
from anvil_serving.benchmarking.swe_images import prepare_paired_images


INSTANCE = "django__django-13512"
IMAGE_ID = "sha256:" + "a" * 64
REGISTRY_DIGEST = "sha256:" + "b" * 64
SPEC_SHA256 = "c" * 64
MIN_FREE_BYTES = 100 * 1024**3
DOCKER_ENDPOINT = "unix:///var/run/docker.sock"


def _disk_usage(free_bytes, observed_paths):
    def observe(path):
        observed_paths.append(path)
        return SimpleNamespace(free=free_bytes)

    return observe


def _runner(calls, docker_root, *, wrong_id=False, timeout_pull=False):
    def run(argv, cwd, timeout, env):
        argv = list(argv)
        calls.append(argv)
        if argv[1:3] == ["context", "inspect"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(DOCKER_ENDPOINT),
                stderr=b"",
            )
        if "info" in argv:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(str(docker_root)),
                stderr=b"",
            )
        if "pull" in argv and timeout_pull:
            raise subprocess.TimeoutExpired(
                argv,
                timeout,
                output=b"pull began",
                stderr=b"registry stopped responding",
            )
        if "image" in argv and "inspect" in argv:
            observed_id = REGISTRY_DIGEST if wrong_id else IMAGE_ID
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "image_id": observed_id,
                        "repo_digests": [
                            "docker.io/swebench/example@" + REGISTRY_DIGEST
                        ],
                    }
                ),
                stderr=b"",
            )
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    return run


def _prepare_args(tmp_path, calls, **runner_options):
    docker_root = tmp_path / "docker-root"
    docker_root.mkdir(exist_ok=True)
    lock_root = tmp_path / "run-root"
    lock_root.mkdir(exist_ok=True)
    observed_paths = []
    return {
        "executable": "/usr/bin/docker",
        "runner": _runner(calls, docker_root, **runner_options),
        "cwd": str(tmp_path / "owned-run"),
        "env": {},
        "receipt_path": str(tmp_path / "receipt.json"),
        "lock_root": str(lock_root),
        "ownership_id": "owner",
        "run_id": "run",
        "spec_sha256": SPEC_SHA256,
        "worker_id": "worker-a",
        "observed_hostname": "worker-a",
        "min_free_bytes": MIN_FREE_BYTES,
        "disk_usage": _disk_usage(MIN_FREE_BYTES + 1024, observed_paths),
        "_docker_root": docker_root,
        "_observed_paths": observed_paths,
    }


def _prepare(tmp_path, calls, **options):
    arguments = _prepare_args(tmp_path, calls, **options)
    arguments.pop("_docker_root")
    arguments.pop("_observed_paths")
    return arguments


def test_confirmed_prepare_uses_docker_root_and_verifies_both_identities(tmp_path):
    calls = []
    arguments = _prepare_args(tmp_path, calls)
    docker_root = arguments.pop("_docker_root")
    observed_paths = arguments.pop("_observed_paths")

    result = prepare_paired_images(
        {INSTANCE: IMAGE_ID},
        {INSTANCE: REGISTRY_DIGEST},
        confirm=True,
        **arguments,
    )

    assert result["status"] == "verified"
    assert result["container_runtime"]["endpoint"] == DOCKER_ENDPOINT
    assert result["container_runtime"]["docker_root"] == str(docker_root)
    assert observed_paths == [str(docker_root), str(docker_root)]
    assert result["images"][0]["status"] == "verified"
    assert result["images"][0]["observation"]["image_id"] == IMAGE_ID
    assert result["images"][0]["observation"]["repo_digests"] == [
        "docker.io/swebench/example@" + REGISTRY_DIGEST
    ]
    assert any("pull" in call and "--quiet" in call for call in calls)
    assert any("tag" in call for call in calls)
    assert json.loads((tmp_path / "receipt.json").read_text())["status"] == "verified"


def test_timeout_retains_bounded_command_diagnostics(tmp_path):
    calls = []
    arguments = _prepare(tmp_path, calls, timeout_pull=True)

    with pytest.raises(BenchmarkJobError) as raised:
        prepare_paired_images(
            {INSTANCE: IMAGE_ID},
            {INSTANCE: REGISTRY_DIGEST},
            confirm=True,
            **arguments,
        )

    assert "timed out" in raised.value.message
    command = json.loads((tmp_path / "receipt.json").read_text())["images"][0][
        "commands"
    ][0]
    assert command["timed_out"] is True
    assert command["stdout"] == "pull began"
    assert command["stderr"] == "registry stopped responding"


def _job_spec(worker_id="worker-a"):
    return {
        "schema": JOB_SPEC_SCHEMA,
        "run_id": "swe-run",
        "ownership_id": "campaign",
        "suite": "swe",
        "profile": "smoke",
        "endpoint": {
            "base_url": "http://127.0.0.1:8000/v1",
            "model": "candidate",
        },
        "worker": {"id": worker_id},
        "submitted_at": "2026-10-15T12:00:00Z",
        "timeout_s": 600,
        "parameters": {
            "instance_ids": [INSTANCE],
            "paired_image_ids": {INSTANCE: IMAGE_ID},
            "paired_image_repo_digests": {INSTANCE: REGISTRY_DIGEST},
        },
    }


def test_jobs_cli_accepts_dispatcher_confirmation_scope(tmp_path, monkeypatch):
    run_root = tmp_path / "runs"
    run_root.mkdir()
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(_job_spec()), encoding="utf-8")
    monkeypatch.setenv("ANVIL_BENCHMARK_RUN_ROOT", str(run_root))
    monkeypatch.setattr(jobs_cli.socket, "gethostname", lambda: "worker-a")
    monkeypatch.setattr(jobs_cli, "resolve_container_binary", lambda: "/usr/bin/docker")
    seen = {}

    def fake_prepare(*_args, **kwargs):
        seen.update(kwargs)
        return {"status": "verified"}

    monkeypatch.setattr(jobs_cli, "prepare_paired_images", fake_prepare)
    with guard.confirmation_scope(True):
        result = jobs_cli.run(
            [
                "swe",
                "images",
                "--spec",
                str(spec_path),
                "--worker",
                "worker-a",
            ]
        )

    assert result == {"status": "verified"}
    assert seen["confirm"] is True
    assert Path(seen["receipt_path"]).parent.name == jobs_cli.job_spec_sha256(
        jobs_cli._swe_image_spec(str(spec_path))
    )


def test_image_runner_retains_bounded_head_and_tail(monkeypatch, tmp_path):
    head = b"A" * (40 * 1024)
    tail = b"Z" * (40 * 1024)

    def fake_run(_argv, **kwargs):
        kwargs["stdout"].write(head + tail)
        kwargs["stderr"].write(b"diagnostic")
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(jobs_cli.subprocess, "run", fake_run)
    result = jobs_cli._image_runner(["docker", "pull", "image"], str(tmp_path), 30, {})

    assert result.returncode == 7
    assert result.stdout_truncated is True
    assert result.stdout.startswith("A")
    assert result.stdout.endswith("Z")
    assert "...[bounded output omitted]..." in result.stdout
    assert len(result.stdout.encode()) <= 64 * 1024
    assert result.stderr == "diagnostic"
