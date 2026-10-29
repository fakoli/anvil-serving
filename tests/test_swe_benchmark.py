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
from anvil_serving.benchmarking.jobs import BenchmarkJobError, canonical_json_bytes
from anvil_serving.benchmarking.profiles import load_profile
from anvil_serving.benchmarking import swe
from anvil_serving.benchmarking import swe_container_custody
from anvil_serving.benchmarking import harnesses
from anvil_serving.benchmarking import swe_agent_adapter as adapter
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
    assert value["commands"]["agent"][1].endswith("swe_agent_adapter.py")
    assert value["harnesses"]["agent_observation"]["adapter_sha256"] == hashlib.sha256(
        Path(value["commands"]["agent"][1]).read_bytes()
    ).hexdigest()
    assert Path(value["paths"]["request_events"]).parent == Path(value["paths"]["run"])
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


@pytest.mark.parametrize("force_nonempty_content", [True, False])
def test_plan_forwards_typed_force_nonempty_content(tmp_path, force_nonempty_content):
    value = plan(
        tmp_path,
        request_controls={
            "thinking_mode": "enabled",
            "force_nonempty_content": force_nonempty_content,
        },
    )

    assert value["request_controls"]["chat_template_kwargs"] == {
        "enable_thinking": True,
        "force_nonempty_content": force_nonempty_content,
    }
    assert value["request_controls"]["force_nonempty_content"] is force_nonempty_content
    expected = "true" if force_nonempty_content else "false"
    assert (
        "      chat_template_kwargs:\n"
        "        enable_thinking: true\n"
        f"        force_nonempty_content: {expected}\n"
    ) in value["config_text"]


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


@pytest.mark.parametrize(
    ("controls", "code"),
    [
        ({"force_nonempty_content": True}, "force_nonempty_content_requires_enabled_thinking"),
        ({"thinking_mode": "enabled", "force_nonempty_content": 1}, "bad_force_nonempty_content"),
        ({"thinking_mode": "enabled", "unknown": True}, "bad_request_controls"),
    ],
)
def test_plan_rejects_invalid_force_nonempty_content_controls(tmp_path, controls, code):
    with pytest.raises(BenchmarkJobError) as exc:
        plan(tmp_path, request_controls=controls)

    assert exc.value.code == code


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
        (
            {"info": {"exit_status": "Submitted"}},
            "diff --git a/a b/a\n",
            {"status": "Submitted", "kind": "submitted"},
            "resolved",
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None},
        ),
        (
            {"info": {"exit_status": "LimitsExceeded"}},
            "",
            {"status": "LimitsExceeded", "kind": "limits_exceeded", "limit_kind": "unknown"},
            "unresolved",
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None},
        ),
        (
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
        ),
        (
            {"info": {"exit_status": "RepeatedFormatError"}, "messages": [
                {"extra": {"response": {"choices": [{"finish_reason": "tool_calls"}]}}},
            ]},
            None,
            {"status": "RepeatedFormatError", "kind": "repeated_format_error", "format_cause": "unknown"},
            "ungraded",
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None},
        ),
        (
            {"info": {"exit_status": "RepeatedFormatError"}, "messages": [
                {"extra": {"response": {"choices": [
                    {"finish_reason": "length"},
                    {"finish_reason": "length"},
                    {"finish_reason": "length"},
                ]}}},
            ]},
            None,
            {"status": "RepeatedFormatError", "kind": "repeated_format_error", "format_cause": "unknown"},
            "ungraded",
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None},
        ),
        (
            {"info": {"exit_status": "RepeatedFormatError", "model_stats": {"api_calls": 1}}, "messages": [
                {"extra": {"response": {"id": "one", "choices": [{"finish_reason": "length"}]}}},
                {"extra": {"response": {"id": "one", "choices": [{"finish_reason": "length"}]}}},
                {"extra": {"response": {"id": "one", "choices": [{"finish_reason": "length"}]}}},
            ]},
            None,
            {"status": "RepeatedFormatError", "kind": "repeated_format_error", "format_cause": "unknown"},
            "ungraded",
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None},
        ),
        (
            None,
            None,
            {"status": None, "kind": "missing"},
            "ungraded",
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None},
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


@pytest.mark.parametrize(
    ("trajectory", "expected"),
    [
        (
            {"info": {"model_stats": {"api_calls": 2}}, "messages": [
                {"extra": {"response": {"id": "one", "usage": {"prompt_tokens": 2, "completion_tokens": 3}}}},
                {"extra": {"response": {"id": "two"}}},
            ]},
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None},
        ),
        (
            {"messages": [
                {"extra": {"response": {"id": "one", "usage": {"prompt_tokens": 2, "completion_tokens": 3}}}},
                {"extra": {"response": {"id": "one", "usage": {"prompt_tokens": 5, "completion_tokens": 7}}}},
            ]},
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None},
        ),
        (
            {"info": {"model_stats": {"api_calls": 2}}, "usage": {"prompt_tokens": 4, "completion_tokens": 5}},
            {"prompt_tokens": 4, "completion_tokens": 5, "total_tokens": 9},
        ),
    ],
)
def test_trajectory_metrics_require_complete_unique_response_coverage(trajectory, expected):
    assert swe._trajectory_metrics(trajectory) == expected


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
        if argv[1].endswith("swe_agent_adapter.py"):
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
    assert result["instances"][0]["failure_class"] == "budget_exhausted"
    assert result["failure"] == {"class": "budget_exhausted", "stage": "agent", "code": "swe_limits_exceeded"}


def test_agent_instance_failure_is_graded_but_remains_an_agent_failure(tmp_path):
    value = plan(tmp_path)
    calls = []

    def failed_instance(argv, cwd, timeout, env):
        calls.append(list(argv))
        if argv[1].endswith("swe_agent_adapter.py"):
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


@pytest.mark.parametrize("outcome", ["success", "nonzero", "timeout", "unexpected"])
def test_server_logs_use_actual_agent_interval_on_every_exit(tmp_path, outcome):
    value = plan(tmp_path)
    delegate = SuccessfulRunner(value)
    captured = []

    def collector(started_at, ended_at):
        captured.append((started_at, ended_at))
        return {"status": "partial", "coverage_complete": False}

    def runner(argv, cwd, timeout, env):
        if argv[1].endswith("swe_agent_adapter.py"):
            if outcome == "timeout":
                raise subprocess.TimeoutExpired(argv, timeout, stderr=b"buffered retry")
            if outcome == "unexpected":
                raise OSError("private transport detail")
            if outcome == "nonzero":
                return SimpleNamespace(returncode=1, stdout=b"", stderr=b"failed")
        return delegate(argv, cwd, timeout, env)

    if outcome == "unexpected":
        with pytest.raises(OSError):
            run_swe_benchmark(value, runner=runner, log_collector=collector,
                              environ={"ANVIL_ROUTER_TOKEN": "token"})
        result = json.loads(Path(value["paths"]["result"]).read_text())
    else:
        result = run_swe_benchmark(value, runner=runner, log_collector=collector,
                                   environ={"ANVIL_ROUTER_TOKEN": "token"})
        assert captured == [(result["stages"][0]["started_at"], result["stages"][0]["ended_at"])]
    assert captured == [(result["agent_interval"]["started_at"], result["agent_interval"]["ended_at"])]
    assert result["server_log_evidence"]["status"] == "partial"
    assert "private transport detail" not in json.dumps(result)


def test_log_collection_failure_cannot_change_grade_or_expose_error_body(tmp_path):
    value = plan(tmp_path)
    def collector(*_args):
        raise RuntimeError("private error body")
    result = run_swe_benchmark(value, runner=SuccessfulRunner(value), log_collector=collector,
                               environ={"ANVIL_ROUTER_TOKEN": "token"})
    assert result["state"] == "completed" and result["summary"]["resolved"] == 1
    assert result["server_log_evidence"]["status"] == "unavailable"
    assert result["server_log_evidence"]["error"] == "RuntimeError"
    assert "private error body" not in json.dumps(result)


def test_stage_wall_clock_jump_does_not_replace_monotonic_duration(monkeypatch):
    monkeypatch.setattr(swe, "utc_now", lambda: "2026-10-16T00:00:00.000000Z")
    stage = swe._result_record(["fixture"], SimpleNamespace(returncode=0), 2.5,
                               "2026-10-16T00:00:03.000000Z")
    assert stage["duration_s"] == 2.5
    assert stage["ended_at"] < stage["started_at"]
    assert stage["clock"]["synchronized"] is None


@pytest.mark.parametrize("timed_out", [False, True])
def test_attempt_reader_failure_preserves_agent_outcome(tmp_path, monkeypatch, timed_out):
    value = plan(tmp_path)
    def unreadable(_path, **_kwargs):
        raise OSError("private filesystem details")
    monkeypatch.setattr(swe, "read_attempt_evidence", unreadable)
    def timeout(*_args):
        raise subprocess.TimeoutExpired("fixture", 1)
    result = run_swe_benchmark(value, runner=timeout if timed_out else SuccessfulRunner(value),
                               environ={"ANVIL_ROUTER_TOKEN": "token"})
    assert result["request_observations"] == {"status": "unavailable", "error": "OSError", "events": []}
    assert result["failure"] == ({"class": "timeout", "stage": "agent"} if timed_out else None)
    assert "private filesystem" not in json.dumps(result)


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


def test_v2_plan_freezes_exact_budgets_config_argv_and_adapter_hash(tmp_path):
    value = plan(tmp_path, profile_name="pilot-v2", timeout_s=60)
    budgets = load_profile("pilot-v2")["suites"]["swe"]["budgets"]
    assert value["schema"] == swe.SWE_PLAN_SCHEMA_V2
    assert value["budgets"] == budgets
    assert value["timeout_s"] == 60
    argv = value["commands"]["agent"]
    assert json.loads(argv[argv.index("--budgets") + 1]) == budgets
    assert argv.index("--budgets") < argv.index("--")
    config_budget = value["config_text"].splitlines()[0].removeprefix("# suite_budgets: ")
    assert json.loads(config_budget) == budgets
    assert yaml.safe_load(value["config_text"])["model"]["model_kwargs"]["max_tokens"] == budgets["max_completion_tokens"]
    assert hashlib.sha256(Path(argv[1]).read_bytes()).hexdigest() == value["harnesses"]["agent_observation"]["adapter_sha256"]
    custody_hash = value["harnesses"]["container_custody"]["adapter_sha256"]
    assert custody_hash == hashlib.sha256(Path(swe_container_custody.__file__).read_bytes()).hexdigest()
    assert argv[argv.index("--container-custody-adapter-sha256") + 1] == custody_hash
    grader = value["commands"]["grader"]
    assert grader[grader.index("--container-custody-adapter-sha256") + 1] == custody_hash
    assert hashlib.sha256(canonical_json_bytes({key: item for key, item in value.items() if key != "plan_sha256"})).hexdigest() == value["plan_sha256"]
    legacy = plan(tmp_path / "legacy")
    assert "budgets" not in legacy and "--budgets" not in legacy["commands"]["agent"]
    assert not legacy["config_text"].startswith("# suite_budgets")


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


def test_v2_exhaustion_survives_zero_exit_without_grading_or_success(tmp_path):
    value = plan(tmp_path, profile_name="pilot-v2")
    calls = []
    def exhausted(argv, *_args):
        calls.append(argv)
        _budget_events(value, exhausted="max_retries")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    result = run_swe_benchmark(value, runner=exhausted, environ={"ANVIL_ROUTER_TOKEN": "token"})
    assert len(calls) == 1
    assert result["state"] == "incomplete"
    assert result["budgets"] == value["budgets"]
    assert result["failure"] == {"class": "budget_exhausted", "stage": "agent", "code": "max_retries"}
    assert result["request_observations"]["budget"]["model_calls"] == 1
    assert json.loads(Path(value["paths"]["result"]).read_text())["failure"] == result["failure"]


def test_v2_requires_complete_budget_evidence_even_if_grading_would_pass(tmp_path):
    value = plan(tmp_path, profile_name="pilot-v2")
    runner = SuccessfulRunner(value)
    result = run_swe_benchmark(value, runner=runner, environ={"ANVIL_ROUTER_TOKEN": "token"})
    assert len(runner.calls) == 1
    assert result["state"] == "incomplete"
    assert result["failure"]["code"] == "missing_swe_budget_evidence"


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


def test_v2_default_runner_reuses_owned_bounded_process(tmp_path, monkeypatch):
    value = plan(tmp_path, profile_name="pilot-v2")
    calls = []
    delegate = SuccessfulRunner(value)
    def bounded(argv, **kwargs):
        calls.append(kwargs)
        assert kwargs["owned_process_group"] is True
        assert kwargs["max_output_bytes"] == swe.MAX_HARNESS_OUTPUT_BYTES
        if argv[1].endswith("swe_agent_adapter.py"):
            _budget_events(value)
        return delegate(argv, kwargs["cwd"], kwargs["timeout"], kwargs["env"])
    monkeypatch.setattr(swe, "_run_bounded_process", bounded)
    result = run_swe_benchmark(value, environ={"ANVIL_ROUTER_TOKEN": "token"})
    assert result["state"] == "completed"
    assert len(calls) == 2


@pytest.mark.parametrize("error, code", [(swe._ProcessOutputOverflow, "swe_process_output_overflow"),
                                         (swe._ProcessPipeError, "swe_process_boundary_failed")])
def test_v2_bounded_runner_exposes_typed_failures(monkeypatch, error, code):
    def fail(*args, **kwargs): raise error("private details")
    monkeypatch.setattr(swe, "_run_bounded_process", fail)
    with pytest.raises(BenchmarkJobError) as caught:
        swe._budget_runner(["fixture"], ".", 1, {})
    assert caught.value.code == code
    assert "private details" not in str(caught.value)


def test_campaign_swe_pre_release_checks_pinned_inputs(tmp_path, monkeypatch):
    from anvil_serving.benchmarking import swe_agent_adapter, swe_grader_adapter

    pinned = plan(tmp_path, paired_image_ids={INSTANCE: 'sha256:' + 'a' * 64}, profile_name='pilot-v2', timeout_s=30)
    digests = {INSTANCE: 'sha256:' + 'b' * 64}
    checks = []
    monkeypatch.setattr(swe, '_verify_dataset_snapshot', lambda snapshot: checks.append('dataset'))
    monkeypatch.setattr(swe_agent_adapter, 'verify_source', lambda root: checks.append('agent'))
    monkeypatch.setattr(swe_grader_adapter, 'verify_source', lambda root: checks.append('grader'))
    monkeypatch.setattr(swe.swe_container_custody, 'verify_source_hash', lambda digest: checks.append('custody'))
    monkeypatch.setattr(swe, 'observe_paired_images', lambda images, **kw: checks.append('image') or [{'instance_id': INSTANCE, 'repo_digests': ['example@' + digests[INSTANCE]]}])
    swe.verify_campaign_swe_plan(pinned, digests)
    assert checks == ['dataset', 'agent', 'grader', 'custody', 'image']
    monkeypatch.setattr(swe_agent_adapter, 'verify_source', lambda root: (_ for _ in ()).throw(RuntimeError('drift')))
    with pytest.raises(BenchmarkJobError):
        swe.verify_campaign_swe_plan(pinned, digests)
    monkeypatch.setattr(swe_agent_adapter, 'verify_source', lambda root: None)
    monkeypatch.setattr(swe, 'observe_paired_images', lambda *a, **k: (_ for _ in ()).throw(BenchmarkJobError('swe_image_identity_mismatch', 'changed')))
    with pytest.raises(BenchmarkJobError, match='changed'):
        swe.verify_campaign_swe_plan(pinned, digests)
    monkeypatch.setattr(swe, '_verify_dataset_snapshot', lambda snapshot: (_ for _ in ()).throw(BenchmarkJobError('swe_dataset_mismatch', 'dataset changed')))
    with pytest.raises(BenchmarkJobError, match='dataset changed'):
        swe.verify_campaign_swe_plan(pinned, digests)


def test_campaign_rejects_unbudgeted_swe_and_wrong_registry_digest(tmp_path, monkeypatch):
    from anvil_serving.benchmarking import swe_agent_adapter, swe_grader_adapter
    ids = {INSTANCE: 'sha256:' + 'a' * 64}
    digests = {INSTANCE: 'sha256:' + 'b' * 64}
    legacy = plan(tmp_path / 'legacy', paired_image_ids=ids)
    with pytest.raises(BenchmarkJobError, match='budgeted'):
        swe.verify_campaign_swe_plan(legacy, digests)
    pinned = plan(tmp_path / 'budgeted', paired_image_ids=ids, profile_name='pilot-v2', timeout_s=30)
    monkeypatch.setattr(swe, '_verify_dataset_snapshot', lambda snapshot: None)
    monkeypatch.setattr(swe_agent_adapter, 'verify_source', lambda root: None)
    monkeypatch.setattr(swe_grader_adapter, 'verify_source', lambda root: None)
    monkeypatch.setattr(swe.swe_container_custody, 'verify_source_hash', lambda digest: None)
    monkeypatch.setattr(swe, 'observe_paired_images', lambda images, **kw: [{'instance_id': INSTANCE, 'repo_digests': ['example@sha256:' + 'd' * 64]}])
    with pytest.raises(BenchmarkJobError, match='inputs changed'):
        swe.verify_campaign_swe_plan(pinned, digests)
