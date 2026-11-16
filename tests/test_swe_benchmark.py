from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from anvil_serving.benchmarking.harnesses import HARNESS_ASSETS_SCHEMA
from anvil_serving.benchmarking.jobs import canonical_json_bytes
from anvil_serving.benchmarking.profiles import load_profile
from anvil_serving.benchmarking import swe
from anvil_serving.benchmarking import harnesses
from anvil_serving.benchmarking import swe_agent_adapter as adapter
from anvil_serving.benchmarking.swe import (
    build_swe_run_plan,
    classify_swe_failure,
    run_swe_benchmark,
)


INSTANCE = "astropy__astropy-12907"
SCOUT_INSTANCES = [f"project__case-{index}" for index in range(5)]


@pytest.fixture(autouse=True)
def supported_worker(monkeypatch):
    monkeypatch.setattr(harnesses.platform, "system", lambda: "Linux")
    monkeypatch.setattr(swe.shutil, "disk_usage", lambda path: SimpleNamespace(free=2**40))


def build_fixture_plan(tmp_path, *args, **kwargs):
    data = b"synthetic dataset content"
    relative = "data/test-00000-of-00001.parquet"
    path = tmp_path / "cache" / "swe-bench-verified" / swe.SWE_DATASET_REVISION / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    with patch.dict(swe.SWE_DATA_FILES, {relative: hashlib.sha256(data).hexdigest()}, clear=True):
        return build_swe_run_plan(*args, **kwargs)


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


def plan(tmp_path, *, request_controls=None, paired_image_ids=None, prompt_contract="legacy/v1", profile_name="smoke", timeout_s=None):
    profile = load_profile(profile_name)
    environment = manifest(profile)["python_environment"]
    executable = (
        tmp_path / "cache" / environment["cache_key"] / environment["executable"]
    )
    executable.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        executable.touch()
    else:
        executable.symlink_to(os.sys.executable)
    return build_fixture_plan(tmp_path,
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
        paired_image_ids=paired_image_ids,
        prompt_contract=prompt_contract,
        timeout_s=timeout_s,
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
    return build_fixture_plan(tmp_path,
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


def test_prompt_contract_preserves_legacy_bytes_and_discloses_v2_limits(tmp_path):
    legacy = plan(tmp_path)
    repeated = plan(tmp_path / "again", prompt_contract="legacy/v1")
    disclosed = plan(tmp_path / "v2", prompt_contract="disclosed/v2")
    assert legacy["prompt_contract"] == "legacy/v1"
    assert legacy["config_text"] == repeated["config_text"]
    assert "You have at most 5 model calls" not in legacy["config_text"]
    assert disclosed["prompt_contract"] == "disclosed/v2"
    suite = load_profile("smoke")["suites"]["swe"]
    assert yaml.safe_load(disclosed["config_text"])["agent"]["step_limit"] == suite["max_steps"]
    disclosure = disclosed["commands"]["agent"][disclosed["commands"]["agent"].index("--prompt-disclosure") + 1]
    assert f"You have at most {suite['max_steps']} model calls (one call per step)." in disclosure
    assert f"Each model completion is capped at {suite['max_completion_tokens']} tokens." in disclosure
    assert "Submit your final answer as a patch" in disclosure

    pinned = tmp_path / "pinned-swebench.yaml"
    pinned.write_text("agent:\n  system_template: |\n    Full shell and patch submission instructions.\n")
    generated = adapter.prompt_contract_config([str(pinned)], disclosure, tmp_path / "v2.yaml")
    rendered = yaml.safe_load(Path(generated).read_text())["agent"]["system_template"]
    assert "Full shell and patch submission instructions." in rendered
    assert disclosure in rendered


class SuccessfulRunner:
    def __init__(self, value):
        self.plan = value
        self.calls = []

    def __call__(self, argv, cwd, timeout, env):
        self.calls.append((list(argv), cwd, timeout, dict(env)))
        if argv[1].endswith("swe_agent_adapter.py"):
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
    assert [stage["name"] for stage in result["stages"]] == ["agent", "official_grader"]
    for stage in result["stages"]:
        assert stage["started_at"].endswith("Z") and stage["ended_at"].endswith("Z")
        assert stage["duration_s"] >= 0
        assert stage["clock"]["duration"] == "monotonic"
        assert stage["clock"]["synchronized"] is None
        assert "buffered" in stage["output_timing"]
    serialized = json.dumps(result)
    assert "not-recorded" not in serialized


@pytest.mark.parametrize(
    ("trajectory", "patch", "expected_exit", "expected_grading", "expected_tokens"),
    [
        pytest.param(
            {"info": {"exit_status": "RepeatedFormatError"}, "messages": [
                {"extra": {"response": {"choices": [{"finish_reason": "length"}],
                                        "usage": {"prompt_tokens": 2, "completion_tokens": 3}}}},
                {"extra": {"response": {"choices": [{"finish_reason": "length"}],
                                        "usage": {"prompt_tokens": 5, "completion_tokens": 7}}}},
                {"extra": {"response": {"choices": [{"finish_reason": "length"}],
                                        "usage": {"prompt_tokens": 11, "completion_tokens": 13}}}},
            ]},
            "diff",
            {"status": "RepeatedFormatError", "kind": "repeated_format_error", "format_cause": "output_cap"},
            "ungraded",
            {"prompt_tokens": 18, "completion_tokens": 23, "total_tokens": 41},
            id="trajectory2-diff-expected_exit2-ungraded-expected_tokens2",
        ),
        pytest.param(
            {"info": {"exit_status": "RepeatedFormatError", "model_stats": {"api_calls": 1}}, "messages": [
                {"extra": {"response": {"id": "one", "choices": [{"finish_reason": "length"}]}}},
                {"extra": {"response": {"id": "one", "choices": [{"finish_reason": "length"}]}}},
                {"extra": {"response": {"id": "one", "choices": [{"finish_reason": "length"}]}}},
            ]},
            None,
            {"status": "RepeatedFormatError", "kind": "repeated_format_error", "format_cause": "unknown"},
            "ungraded",
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None},
            id="trajectory5-None-expected_exit5-ungraded-expected_tokens5",
        ),
    ],
)
def test_swe_instance_diagnostics_are_additive_and_evidence_bound(
    trajectory, patch, expected_exit, expected_grading, expected_tokens
):
    prediction = {} if patch is None else {"model_patch": patch}
    diagnostics = swe._swe_diagnostics(trajectory, prediction)
    assert diagnostics["exit"] == expected_exit
    assert diagnostics["prediction"] == {
        "present": isinstance(patch, str),
        "nonempty": bool(patch),
        "sha256": hashlib.sha256((patch or "").encode("utf-8")).hexdigest(),
        "bytes": len((patch or "").encode("utf-8")),
    }
    instance = {"diagnostics": diagnostics, "grader": {"completed": expected_grading != "ungraded", "resolved": expected_grading == "resolved"}}
    swe._set_grading_observation(instance)
    assert instance["diagnostics"]["grading"]["status"] == expected_grading
    assert swe._trajectory_metrics(trajectory) == expected_tokens


def test_missing_paired_image_never_pulls_or_calls_model(tmp_path):
    value = plan(tmp_path, paired_image_ids={INSTANCE: "sha256:" + "a" * 64})
    calls = []
    def missing(argv, cwd, timeout, env):
        calls.append(argv)
        assert argv[1:3] == ["image", "inspect"]
        return SimpleNamespace(returncode=1, stdout="")
    result = run_swe_benchmark(value, runner=missing, environ={"ANVIL_ROUTER_TOKEN": "token"})
    assert len(calls) == 1
    assert result["failure"]["stage"] == "before_agent"


def test_missing_trajectory_preserves_partial_official_grading(tmp_path):
    value = scout_plan(tmp_path)
    calls = []

    def partial_instance(argv, cwd, timeout, env):
        calls.append(list(argv))
        if argv[1].endswith("swe_agent_adapter.py"):
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
    assert result["stages"][0]["started_at"].endswith("Z")
    assert result["stages"][0]["ended_at"].endswith("Z")
    assert classify_swe_failure(stage="agent", returncode=1, text="401 Unauthorized") == "model_failure"
    assert classify_swe_failure(stage="agent", returncode=1, text="Cannot connect to Docker daemon") == "infrastructure_failure"
    assert classify_swe_failure(stage="grader", returncode=0, text="tests failed") == "test_failure"
    assert classify_swe_failure(stage="agent", returncode=1, text="exec format error") == "image_failure"


def test_dataset_rechecked_after_agent_before_grader(tmp_path):
    value = plan(tmp_path)
    runner = SuccessfulRunner(value)
    def mutate_after_agent(argv, cwd, timeout, env):
        result = runner(argv, cwd, timeout, env)
        snapshot = value["dataset_snapshot"]
        (Path(snapshot["root"]) / next(iter(snapshot["files"]))).write_bytes(b"changed")
        return result
    result = run_swe_benchmark(value, runner=mutate_after_agent, environ={"ANVIL_ROUTER_TOKEN": "token"})
    assert result["state"] == "incomplete"
    assert result["failure"]["code"] == "swe_dataset_mismatch"
    assert Path(value["paths"]["result"]).is_file()
    assert len(runner.calls) == 1


def _budget_events(value, *, exhausted=None):
    recorder = adapter.EventRecorder(value["paths"]["request_events"])
    recorder.record("adapter_ready", mini_revision=adapter.MINI_REVISION, source_hashes=adapter.SOURCE_HASHES,
                    adapter_sha256=value["harnesses"]["agent_observation"]["adapter_sha256"],
                    dependencies=adapter.SUPPORTED_DEPENDENCIES, transport_seam="httpx.Client.send",
                    retry_seam="minisweagent.models.litellm_model.retry",
                    clock={"wall": "UTC system clock", "wall_precision_s": 0.000001,
                           "wall_resolution_s": 0.000001, "duration": "monotonic",
                           "duration_resolution_s": 0.000001, "synchronized": None})
    budget = adapter.SuiteBudget(value["budgets"], recorder)
    budget.model_calls = 1
    if exhausted:
        with pytest.raises(adapter.BudgetStopped):
            budget.stop(exhausted)
    recorder.close(outcome="failed" if exhausted else "completed")


def test_v2_shared_deadline_includes_agent_and_grader(tmp_path, monkeypatch):
    value = plan(tmp_path, profile_name="pilot-v2", timeout_s=60)
    runner = SuccessfulRunner(value)
    now = [10.0]
    monkeypatch.setattr(swe.time, "monotonic", lambda: now[0])
    def timed(argv, cwd, timeout, env):
        if argv[1].endswith("swe_agent_adapter.py"):
            assert timeout == 60
            _budget_events(value)
            now[0] += 40
        else:
            assert timeout == 20
            now[0] += 21
        return runner(argv, cwd, timeout, env)
    result = run_swe_benchmark(value, runner=timed, environ={"ANVIL_ROUTER_TOKEN": "token"})
    assert len(runner.calls) == 2
    assert result["state"] == "incomplete"
    assert result["failure"] == {"class": "budget_exhausted", "stage": "official_grader", "code": "deadline_s"}


def test_v2_external_timeout_preserves_budget_failure(tmp_path):
    value = plan(tmp_path, profile_name="pilot-v2")
    def timeout(*_args):
        raise subprocess.TimeoutExpired("fixture", 1)
    result = run_swe_benchmark(value, runner=timeout, environ={"ANVIL_ROUTER_TOKEN": "token"})
    assert result["failure"] == {"class": "budget_exhausted", "stage": "agent", "code": "deadline_s"}
    assert result["request_observations"]["status"] == "missing"
