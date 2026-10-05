from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from anvil_serving.benchmarking.harnesses import HARNESS_ASSETS_SCHEMA
from anvil_serving.benchmarking.jobs import BenchmarkJobError, canonical_json_bytes
from anvil_serving.benchmarking.profiles import load_profile
from anvil_serving.benchmarking import swe
from anvil_serving.benchmarking import harnesses
from anvil_serving.benchmarking.swe import (
    SWE_DATASET,
    build_swe_run_plan,
    classify_swe_failure,
    run_swe_benchmark,
    validate_swe_selection,
)


INSTANCE = "astropy__astropy-12907"
SCOUT_INSTANCES = [f"project__case-{index}" for index in range(5)]


@pytest.fixture(autouse=True)
def supported_worker(monkeypatch):
    monkeypatch.setattr(harnesses.platform, "system", lambda: "Linux")


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


def plan(tmp_path, *, request_controls=None, paired_image_ids=None):
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


def test_plan_pins_selection_router_and_both_harnesses(tmp_path):
    value = plan(tmp_path)
    assert value["dataset"] == SWE_DATASET
    assert value["commands"]["agent"][value["commands"]["agent"].index("--subset") + 1] == value["dataset_snapshot"]["root"]
    assert value["commands"]["grader"][value["commands"]["grader"].index("--dataset_name") + 1] == value["dataset_snapshot"]["root"]
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
    assert "    - --network\n    - none\n" in value["config_text"]
    assert value["task_container_network"] == "none"
    assert "    - --memory\n    - 8g\n" in value["config_text"]
    assert "    - --memory-swap\n    - 8g\n" in value["config_text"]
    assert "    - --cpus\n    - '4'\n" in value["config_text"]
    assert "    - --pids-limit\n    - '512'\n" in value["config_text"]
    assert value["container_limits"]["mem_limit"] == 8589934592
    assert value["container_limits"]["network_mode"] == "none"
    assert value["commands"]["grader"][1].endswith("swe_grader_adapter.py")
    assert value["harnesses"]["grader_containment"]["adapter_sha256"] == hashlib.sha256(
        Path(value["commands"]["grader"][1]).read_bytes()
    ).hexdigest()
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
    assert '    extra_body:\n      reasoning_effort: "xhigh"\n' in value["config_text"]
    assert '\n    reasoning_effort: "xhigh"\n' not in value["config_text"]


def test_plan_records_and_wires_explicit_sampling_to_litellm_model_kwargs(tmp_path):
    value = plan(
        tmp_path, request_controls={"temperature": 1.0, "top_p": 0.95}
    )

    assert value["request_controls"]["sampling"] == {
        "temperature": {"requested": 1.0, "effective_request": 1.0, "sent": True},
        "top_p": {"requested": 0.95, "effective_request": 0.95, "sent": True},
    }
    assert "    temperature: 1.0\n    top_p: 0.95\n" in value["config_text"]
    assert "extra_body:\n      temperature" not in value["config_text"]
    assert "extra_body:\n      top_p" not in value["config_text"]


@pytest.mark.parametrize("controls", [
    {"temperature": "1.0"},
    {"top_p": True},
])
def test_plan_rejects_invalid_sampler_request_controls(tmp_path, controls):
    with pytest.raises(BenchmarkJobError) as exc:
        plan(tmp_path, request_controls=controls)

    assert exc.value.code == "bad_request_controls"


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


@pytest.mark.parametrize("identities", [{}, {INSTANCE: "latest"}, {"other__case": "sha256:" + "a" * 64}])
def test_paired_images_require_exact_selection_and_immutable_ids(tmp_path, identities):
    with pytest.raises(BenchmarkJobError, match="every selected instance"):
        plan(tmp_path, paired_image_ids=identities)


@pytest.mark.parametrize("changed_boundary", [None, "before_agent", "before_grader", "after_grader"])
def test_paired_images_record_boundaries_and_stop_before_dependent_execution(tmp_path, changed_boundary):
    expected = "sha256:" + "a" * 64
    value = plan(tmp_path, paired_image_ids={INSTANCE: expected})
    assert "    - --pull\n    - never\n" in value["config_text"]
    assert "--image-identities-sha256" in value["commands"]["grader"]
    delegate = SuccessfulRunner(value)
    boundaries = iter(["before_agent", "before_grader", "after_grader"])

    def run(argv, cwd, timeout, env):
        if argv[1:3] == ["image", "inspect"]:
            boundary = next(boundaries)
            observed = "sha256:" + "b" * 64 if changed_boundary == boundary else expected
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                "image_id": observed,
                "repo_digests": ["swebench/example@" + observed],
            }))
        return delegate(argv, cwd, timeout, env)

    result = run_swe_benchmark(value, runner=run, environ={"ANVIL_ROUTER_TOKEN": "token"})
    if changed_boundary is None:
        assert result["state"] == "completed"
        assert [x["stage"] for x in result["image_observations"]] == ["before_agent", "before_grader", "after_grader"]
        assert all(x["images"][0]["image_id"] == expected for x in result["image_observations"])
    else:
        assert result["state"] == "incomplete"
        assert result["failure"] == {"class": "image_failure", "stage": changed_boundary,
                                     "code": "swe_image_identity_mismatch"}
        assert len(delegate.calls) == {"before_agent": 0, "before_grader": 1, "after_grader": 2}[changed_boundary]


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


def test_agent_completion_without_official_report_is_incomplete(tmp_path):
    value = plan(tmp_path)
    runner = SuccessfulRunner(value)

    def no_report(argv, cwd, timeout, env):
        result = runner(argv, cwd, timeout, env)
        if argv[1].endswith("swe_grader_adapter.py"):
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


@pytest.mark.parametrize("graded", [False, True])
def test_limits_exceeded_with_trajectory_and_empty_patch_preserves_agent_failure(tmp_path, graded):
    value = plan(tmp_path)
    runner = SuccessfulRunner(value)

    def limits_exceeded(argv, cwd, timeout, env):
        result = runner(argv, cwd, timeout, env)
        if "swebench.py" in argv[1]:
            output = Path(value["paths"]["output"])
            trajectory_path = output / INSTANCE / f"{INSTANCE}.traj.json"
            trajectory = json.loads(trajectory_path.read_text())
            trajectory["info"]["exit_status"] = "LimitsExceeded"
            trajectory_path.write_text(json.dumps(trajectory))
            predictions_path = output / "preds.json"
            predictions = json.loads(predictions_path.read_text())
            predictions[INSTANCE]["model_patch"] = ""
            predictions_path.write_text(json.dumps(predictions))
        else:
            report_path = next(Path(value["paths"]["grader_work"]).glob("*.json"))
            report_path.write_text(json.dumps({
                "completed_ids": [INSTANCE] if graded else [],
                "resolved_ids": [],
                "error_ids": [],
            }))
        return result

    result = run_swe_benchmark(value, runner=limits_exceeded, environ={"ANVIL_ROUTER_TOKEN": "token"})
    assert len(runner.calls) == 2
    assert result["state"] == "incomplete"
    assert result["official_grader_complete"] is graded
    assert result["summary"]["graded"] == int(graded)
    assert result["instances"][0]["failure_class"] == "model_failure"
    assert result["failure"] == {"class": "model_failure", "stage": "agent", "code": "swe_limits_exceeded"}


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


def test_changed_dataset_rejected_before_model_or_grader(tmp_path):
    value = plan(tmp_path)
    snapshot = value["dataset_snapshot"]
    (Path(snapshot["root"]) / next(iter(snapshot["files"]))).write_bytes(b"LFS pointer or changed bytes")
    with pytest.raises(BenchmarkJobError, match="dataset content changed"):
        run_swe_benchmark(value, runner=lambda *_args: pytest.fail("model invoked"))


@pytest.mark.parametrize("extra", ["test.json", "test.txt", "test.zip"])
def test_extra_dataset_discovery_file_rejected(tmp_path, extra):
    value = plan(tmp_path)
    (Path(value["dataset_snapshot"]["root"]) / extra).write_text("[]")
    with pytest.raises(BenchmarkJobError, match="unexpected files"):
        swe._verify_dataset_snapshot(value["dataset_snapshot"])


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


def test_missing_dataset_file_is_typed_error(tmp_path):
    value = plan(tmp_path)
    snapshot = value["dataset_snapshot"]
    (Path(snapshot["root"]) / next(iter(snapshot["files"]))).unlink()
    with pytest.raises(BenchmarkJobError) as exc:
        swe._verify_dataset_snapshot(snapshot)
    assert exc.value.code == "swe_dataset_unavailable"


def test_windows_plan_and_replay_refuse_before_effects(tmp_path, monkeypatch):
    monkeypatch.setattr(harnesses.platform, "system", lambda: "Windows")
    with pytest.raises(BenchmarkJobError) as exc:
        build_swe_run_plan(load_profile("smoke"), {}, endpoint={}, instance_ids=[],
                           run_root=str(tmp_path / "runs"), cache_root=str(tmp_path / "cache"),
                           ownership_id="campaign", run_id="unsupported")
    assert exc.value.code == "unsupported_swe_worker_platform"
    with pytest.raises(BenchmarkJobError) as exc:
        run_swe_benchmark({}, runner=lambda *_args: pytest.fail("runner called"))
    assert exc.value.code == "unsupported_swe_worker_platform"
    assert list(tmp_path.iterdir()) == []
