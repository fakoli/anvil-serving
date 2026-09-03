from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from anvil_serving.benchmarking.harnesses import HARNESS_ASSETS_SCHEMA
from anvil_serving.benchmarking.jobs import BenchmarkJobError, canonical_json_bytes
from anvil_serving.benchmarking.profiles import load_profile
from anvil_serving.benchmarking.swe import (
    SWE_DATASET,
    build_swe_run_plan,
    classify_swe_failure,
    run_swe_benchmark,
    validate_swe_selection,
)


INSTANCE = "astropy__astropy-12907"
SCOUT_INSTANCES = [f"project__case-{index}" for index in range(5)]


def manifest(profile):
    assets = {}
    for name in profile["suites"]["swe"]["adapters"]:
        adapter = profile["adapters"][name]
        if adapter["kind"] in {"git", "dataset"}:
            assets[name] = {
                **adapter,
                "cache_key": f"{name}/{adapter['revision']}",
                "dirty": False,
            }
        else:
            assets[name] = dict(adapter)
    packages = ["mini-swe-agent==2.4.6", "swebench==4.2.0", "typer==0.21.0"]
    return {
        "schema": HARNESS_ASSETS_SCHEMA,
        "profile_sha256": profile["content_sha256"],
        "suite": "swe",
        "assets": assets,
        "python_environment": {
            "schema": "anvil-serving.swe-python-environment/v1",
            "python": {"implementation": "CPython", "version": "3.12.0"},
            "platform": "test",
            "architecture": "test",
            "cache_key": "swe-python-environments/test-environment",
            "executable": "Scripts/python.exe" if os.name == "nt" else "bin/python",
            "resolved_packages": packages,
            "resolved_packages_sha256": hashlib.sha256(
                canonical_json_bytes(packages)
            ).hexdigest(),
            "reused": True,
        },
    }


def plan(tmp_path, *, request_controls=None):
    profile = load_profile("smoke")
    environment = manifest(profile)["python_environment"]
    executable = (
        tmp_path / "cache" / environment["cache_key"] / environment["executable"]
    )
    executable.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        executable.touch()
    else:
        executable.symlink_to(os.sys.executable)
    return build_swe_run_plan(
        profile,
        {**manifest(profile), "python_environment": environment},
        endpoint={
            "base_url": "http://100.64.0.10:8000/v1",
            "model": "deepseek-challenger",
            "auth_env": "ANVIL_ROUTER_TOKEN",
        },
        instance_ids=[INSTANCE],
        run_root=str(tmp_path / "runs"),
        cache_root=str(tmp_path / "cache"),
        ownership_id="campaign",
        run_id="smoke-one",
        request_controls=request_controls,
    )


def scout_plan(tmp_path):
    profile = load_profile("scout")
    environment = manifest(profile)["python_environment"]
    executable = (
        tmp_path / "cache" / environment["cache_key"] / environment["executable"]
    )
    executable.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        executable.touch()
    else:
        executable.symlink_to(os.sys.executable)
    return build_swe_run_plan(
        profile,
        {**manifest(profile), "python_environment": environment},
        endpoint={
            "base_url": "http://100.64.0.10:8000/v1",
            "model": "deepseek-challenger",
            "auth_env": "ANVIL_ROUTER_TOKEN",
        },
        instance_ids=SCOUT_INSTANCES,
        run_root=str(tmp_path / "runs"),
        cache_root=str(tmp_path / "cache"),
        ownership_id="campaign",
        run_id="scout-one",
    )


def test_plan_pins_selection_router_and_both_harnesses(tmp_path):
    value = plan(tmp_path)
    assert value["dataset"] == SWE_DATASET
    assert value["selection"]["kind"] == "explicit_instance_ids"
    assert value["selection"]["instance_ids"] == [INSTANCE]
    assert "^" in value["commands"]["agent"][value["commands"]["agent"].index("--filter") + 1]
    assert value["commands"]["grader"][-1] == INSTANCE
    assert value["harnesses"]["agent"]["revision"] == load_profile("smoke")["adapters"]["mini-swe-agent"]["revision"]
    assert value["harnesses"]["grader"]["revision"] == load_profile("smoke")["adapters"]["swe-bench"]["revision"]
    assert value["commands"]["agent"][0] != os.sys.executable
    assert value["commands"]["agent"][0] == value["commands"]["grader"][0]
    assert value["harnesses"]["python_environment"]["resolved_packages_sha256"]
    assert "secret" not in value["config_text"].lower()
    assert "http://100.64.0.10:8000/v1" in value["config_text"]
    assert "  executable:" in value["config_text"]
    assert "    - --platform\n    - linux/amd64\n" in value["config_text"]
    assert value["request_controls"] == {
        "thinking_mode": "default",
        "reasoning_effort": None,
    }


def test_plan_forwards_and_records_reasoning_effort(tmp_path):
    value = plan(tmp_path, request_controls={"reasoning_effort": "xhigh"})

    assert value["request_controls"] == {
        "thinking_mode": "default",
        "reasoning_effort": "xhigh",
    }
    assert 'reasoning_effort: "xhigh"' in value["config_text"]


def test_plan_rejects_conflicting_reasoning_controls(tmp_path):
    with pytest.raises(BenchmarkJobError) as exc:
        plan(
            tmp_path,
            request_controls={"reasoning_effort": "xhigh", "thinking_mode": "enabled"},
        )

    assert exc.value.code == "conflicting_reasoning_controls"


def test_selection_cannot_be_implicit_short_or_duplicated():
    profile = load_profile("smoke")
    with pytest.raises(BenchmarkJobError) as exc:
        validate_swe_selection(profile, [])
    assert exc.value.code == "explicit_swe_selection_required"
    with pytest.raises(BenchmarkJobError):
        validate_swe_selection(profile, ["not-an-instance"])


class SuccessfulRunner:
    def __init__(self, value):
        self.plan = value
        self.calls = []

    def __call__(self, argv, cwd, timeout, env):
        self.calls.append((list(argv), cwd, timeout, dict(env)))
        if "swebench.py" in argv[1]:
            output = Path(self.plan["paths"]["output"])
            instance_dir = output / INSTANCE
            instance_dir.mkdir(parents=True, exist_ok=True)
            (output / "preds.json").write_text(json.dumps({
                INSTANCE: {
                    "model_name_or_path": "openai/deepseek-challenger",
                    "instance_id": INSTANCE,
                    "model_patch": "diff --git a/a.py b/a.py\n",
                }
            }), encoding="utf-8")
            (instance_dir / f"{INSTANCE}.traj.json").write_text(json.dumps({
                "info": {
                    "exit_status": "Submitted",
                    "duration_s": 12.5,
                    "usage": {"prompt_tokens": 101, "completion_tokens": 22},
                },
                "messages": [{"extra": {"response": {"id": "req-anvil-1"}}}],
            }), encoding="utf-8")
        else:
            report = Path(self.plan["paths"]["grader_work"]) / (
                f"openai__deepseek-challenger.{self.plan['run_id']}.json"
            )
            report.write_text(json.dumps({
                "completed_ids": [INSTANCE],
                "resolved_ids": [INSTANCE],
                "error_ids": [],
                "schema_version": 2,
            }), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")


def test_completed_run_requires_official_grader_and_keeps_instance_evidence(tmp_path):
    value = plan(tmp_path)
    runner = SuccessfulRunner(value)
    result = run_swe_benchmark(
        value,
        runner=runner,
        environ={"ANVIL_ROUTER_TOKEN": "not-recorded\r\n"},
    )
    assert result["state"] == "completed"
    assert result["official_grader_complete"] is True
    assert result["request_controls"]["thinking_mode"] == "default"
    assert result["summary"]["resolve_rate"] == 1.0
    instance = result["instances"][0]
    assert instance["tokens"] == {"prompt_tokens": 101, "completion_tokens": 22, "total_tokens": 123}
    assert instance["request_ids"] == ["req-anvil-1"]
    assert instance["grader"]["resolved"] is True
    assert runner.calls[0][3]["OPENAI_API_KEY"] == "not-recorded"
    assert runner.calls[0][3]["MSWEA_GLOBAL_CONFIG_DIR"] == value["paths"][
        "mini_config_home"
    ]
    assert runner.calls[0][3]["MSWEA_SILENT_STARTUP"] == "1"
    assert result["promotion"]["authorized"] is False
    serialized = json.dumps(result)
    assert "not-recorded" not in serialized


def test_agent_completion_without_official_report_is_incomplete(tmp_path):
    value = plan(tmp_path)
    runner = SuccessfulRunner(value)

    def no_report(argv, cwd, timeout, env):
        result = runner(argv, cwd, timeout, env)
        if "swebench.harness.run_evaluation" in argv:
            for path in Path(value["paths"]["grader_work"]).glob("*.json"):
                path.unlink()
        return result

    result = run_swe_benchmark(
        value,
        runner=no_report,
        environ={"ANVIL_ROUTER_TOKEN": "token"},
    )
    assert result["state"] == "incomplete"
    assert result["official_grader_complete"] is False
    assert result["failure"]["stage"] == "official_grader"


def test_agent_instance_failure_is_graded_but_remains_an_agent_failure(tmp_path):
    value = plan(tmp_path)
    calls = []

    def failed_instance(argv, cwd, timeout, env):
        calls.append(list(argv))
        if "swebench.py" in argv[1]:
            output = Path(value["paths"]["output"])
            output.mkdir(parents=True, exist_ok=True)
            (output / "preds.json").write_text(
                json.dumps(
                    {
                        INSTANCE: {
                            "model_name_or_path": "openai/deepseek-challenger",
                            "instance_id": INSTANCE,
                            "model_patch": "",
                        }
                    }
                ),
                encoding="utf-8",
            )
            return SimpleNamespace(
                returncode=0,
                stdout=b"CalledProcessError: docker image could not start",
                stderr=b"",
            )
        report = Path(value["paths"]["grader_work"]) / (
            f"openai__deepseek-challenger.{value['run_id']}.json"
        )
        report.write_text(
            json.dumps(
                {
                    "completed_ids": [INSTANCE],
                    "resolved_ids": [],
                    "error_ids": [],
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout=b"graded", stderr=b"")

    result = run_swe_benchmark(
        value,
        runner=failed_instance,
        environ={"ANVIL_ROUTER_TOKEN": "token"},
    )

    assert len(calls) == 2
    assert result["state"] == "incomplete"
    assert result["official_grader_complete"] is True
    assert result["instances"][0]["grader"]["completed"] is True
    assert result["failure"] == {
        "class": "image_failure",
        "stage": "agent",
        "code": "missing_swe_trajectory",
    }
    assert result["stages"][0]["status"] == "failed"


def test_missing_trajectory_preserves_partial_official_grading(tmp_path):
    value = scout_plan(tmp_path)
    calls = []

    def partial_instance(argv, cwd, timeout, env):
        calls.append(list(argv))
        if "swebench.py" in argv[1]:
            output = Path(value["paths"]["output"])
            output.mkdir(parents=True, exist_ok=True)
            predictions = {}
            for instance_id in SCOUT_INSTANCES:
                predictions[instance_id] = {
                    "model_name_or_path": "openai/deepseek-challenger",
                    "instance_id": instance_id,
                    "model_patch": "diff --git a/a.py b/a.py\n",
                }
            (output / "preds.json").write_text(
                json.dumps(predictions), encoding="utf-8"
            )
            for instance_id in SCOUT_INSTANCES[:-1]:
                instance_dir = output / instance_id
                instance_dir.mkdir(parents=True)
                (instance_dir / f"{instance_id}.traj.json").write_text(
                    json.dumps({"info": {"exit_status": "Submitted"}}),
                    encoding="utf-8",
                )
            return SimpleNamespace(returncode=0, stdout=b"agent batch complete", stderr=b"")
        report = Path(value["paths"]["grader_work"]) / (
            f"openai__deepseek-challenger.{value['run_id']}.json"
        )
        report.write_text(
            json.dumps(
                {
                    "completed_ids": SCOUT_INSTANCES[:-1],
                    "resolved_ids": SCOUT_INSTANCES[:-1],
                    "error_ids": [],
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout=b"partial grading complete", stderr=b"")

    result = run_swe_benchmark(
        value,
        runner=partial_instance,
        environ={"ANVIL_ROUTER_TOKEN": "token"},
    )

    assert len(calls) == 2
    assert result["state"] == "incomplete"
    assert result["official_grader_complete"] is False
    assert result["summary"] == {
        "attempted": 5,
        "graded": 4,
        "resolved": 4,
        "resolve_rate": 1.0,
    }
    assert all(
        instance["grader"]["completed"] for instance in result["instances"][:-1]
    )
    assert result["instances"][-1]["grader"]["completed"] is False
    assert result["failure"] == {
        "class": "broken_harness",
        "stage": "agent",
        "code": "missing_swe_trajectory",
    }


def test_timeout_and_failures_are_distinct(tmp_path):
    value = plan(tmp_path)

    def timeout(*_args):
        raise subprocess.TimeoutExpired(cmd="mini", timeout=1)

    result = run_swe_benchmark(
        value,
        runner=timeout,
        environ={"ANVIL_ROUTER_TOKEN": "token"},
    )
    assert result["failure"] == {"class": "timeout", "stage": "agent"}
    assert classify_swe_failure(stage="agent", returncode=1, text="401 Unauthorized") == "model_failure"
    assert classify_swe_failure(stage="agent", returncode=1, text="Cannot connect to Docker daemon") == "infrastructure_failure"
    assert classify_swe_failure(stage="grader", returncode=0, text="tests failed") == "test_failure"
    assert classify_swe_failure(stage="agent", returncode=1, text="exec format error") == "image_failure"
