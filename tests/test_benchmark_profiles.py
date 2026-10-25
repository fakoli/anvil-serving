import copy
import json
from pathlib import Path

import pytest

from anvil_serving.benchmarking.jobs import BenchmarkJobError
from anvil_serving.benchmarking.profiles import (
    PROFILE_NAMES,
    load_profile,
    profile_content_sha256,
    validate_profile,
)


@pytest.mark.parametrize("name", sorted(PROFILE_NAMES))
def test_checked_in_profiles_are_content_addressed_and_valid(name):
    profile = load_profile(name)
    assert profile["content_sha256"] == profile_content_sha256(profile)
    assert set(profile["suites"]) == ({"context", "agentic", "swe", "aider", "performance"} if name == "pilot-v2" else {"context", "agentic", "swe"})


@pytest.mark.parametrize("name", sorted(PROFILE_NAMES))
def test_packaged_profiles_match_public_config(name):
    root = Path(__file__).resolve().parents[1]
    packaged = root / "anvil_serving" / "_benchmark_profiles" / f"{name}.json"
    public = root / "configs" / "benchmarks" / f"{name}.json"
    assert json.loads(packaged.read_text(encoding="utf-8")) == json.loads(
        public.read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("name", sorted(PROFILE_NAMES))
def test_swe_bench_verified_uses_the_fetchable_official_revision(name):
    profile = load_profile(name)
    assert (
        profile["adapters"]["swe-bench-verified"]["revision"]
        == "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"
    )


def test_context_capacity_reserves_output_headroom():
    smoke = load_profile("smoke", observed_context=65536)
    assert smoke["suites"]["context"]["token_buckets"][-1] == 32768
    with pytest.raises(BenchmarkJobError) as exc:
        load_profile("deep", observed_context=640000)
    assert exc.value.code == "context_capacity_exceeded"


def test_mutable_adapter_and_image_locks_are_rejected():
    profile = load_profile("smoke")
    mutable_git = copy.deepcopy(profile)
    mutable_git["adapters"]["ruler"]["revision"] = "main"
    mutable_git["content_sha256"] = profile_content_sha256(mutable_git)
    with pytest.raises(BenchmarkJobError) as exc:
        validate_profile(mutable_git)
    assert exc.value.code == "mutable_adapter"

    mutable_image = copy.deepcopy(profile)
    mutable_image["adapters"]["worker-base"]["image"] = "python:3.12-slim"
    mutable_image["content_sha256"] = profile_content_sha256(mutable_image)
    with pytest.raises(BenchmarkJobError) as exc:
        validate_profile(mutable_image)
    assert exc.value.code == "mutable_image"


def test_missing_scoring_and_unknown_suite_fail_closed():
    profile = load_profile("smoke")
    missing = copy.deepcopy(profile)
    missing["suites"]["agentic"]["scoring"] = {}
    missing["content_sha256"] = profile_content_sha256(missing)
    with pytest.raises(BenchmarkJobError) as exc:
        validate_profile(missing)
    assert exc.value.code == "missing_scoring_policy"

    unknown = copy.deepcopy(profile)
    unknown["suites"]["mystery"] = unknown["suites"]["agentic"]
    unknown["content_sha256"] = profile_content_sha256(unknown)
    with pytest.raises(BenchmarkJobError) as exc:
        validate_profile(unknown)
    assert exc.value.code == "unknown_suite"


def test_v1_contract_is_frozen_when_current_registry_grows(monkeypatch):
    from anvil_serving.benchmarking import profiles

    assert profiles.V1_PROFILE_NAMES == {"smoke", "scout", "deep"}
    assert profiles.V1_SUITE_NAMES == {"context", "agentic", "swe"}
    legacy = load_profile("smoke")
    assert profiles.suite_budgets(legacy, "context") is None
    monkeypatch.setattr(profiles, "PROFILE_NAMES", PROFILE_NAMES | {"future"})
    monkeypatch.setattr(profiles, "SUITE_NAMES", profiles.SUITE_NAMES | {"future"})
    assert validate_profile(legacy) == legacy
    for field in ("name", "suites"):
        changed = copy.deepcopy(legacy)
        if field == "name":
            changed[field] = "future"
        else:
            changed[field]["future"] = changed[field]["agentic"]
        changed["content_sha256"] = profile_content_sha256(changed)
        with pytest.raises(BenchmarkJobError):
            validate_profile(changed)


def test_v2_budget_identity_and_copy():
    from anvil_serving.benchmarking.profiles import suite_budgets

    profile = load_profile("pilot-v2")
    budgets = suite_budgets(profile, "agentic")
    assert budgets == profile["suites"]["agentic"]["budgets"]
    budgets["max_model_calls"] += 1
    assert budgets != suite_budgets(profile, "agentic")
    profile["suites"]["agentic"]["budgets"] = budgets
    with pytest.raises(BenchmarkJobError, match="content identity"):
        validate_profile(profile)
    profile["content_sha256"] = profile_content_sha256(profile)
    assert validate_profile(profile)["suites"]["agentic"]["budgets"] == budgets


def test_pilot_native_tasks_have_individual_scenarios_and_approved_limits():
    from anvil_serving.benchmarking.agentic import build_agentic_scenario
    from anvil_serving.benchmarking.suite_runner import _agentic_selection

    suite = load_profile("pilot-v2")["suites"]["agentic"]
    expected = ["structured-edit", "tool-sequence", "parallel-tools",
                "dependent-result", "tool-recovery", "debug-loop-v2"]
    assert suite["cases"] == expected
    assert (suite["max_steps"], suite["max_completion_tokens"], suite["timeout_s"]) == (24, 8192, 600)
    for task in expected:
        selected = _agentic_selection(suite, {"case_ids": [task], "recovery_result": "error"})
        assert selected["case_ids"] == [task]
        assert build_agentic_scenario(task, recovery_result="error")


@pytest.mark.parametrize("field", [
    "max_model_calls", "max_completion_tokens", "deadline_s", "max_retries", "min_free_disk_bytes",
])
@pytest.mark.parametrize("value", [True, None, "1", 1.5, -1, float("inf"), float("nan"), 2**64])
def test_v2_rejects_invalid_budget_values(field, value):
    profile = load_profile("pilot-v2")
    profile["suites"]["context"]["budgets"][field] = value
    with pytest.raises(BenchmarkJobError) as exc:
        validate_profile(profile)
    assert exc.value.code == "bad_profile"


@pytest.mark.parametrize("change", ["missing", "extra", "tokens", "deadline", "disk", "retries"])
def test_v2_rejects_missing_extra_or_contradictory_budgets(change):
    profile = load_profile("pilot-v2")
    budgets = profile["suites"]["context"]["budgets"]
    if change == "missing":
        del budgets["max_retries"]
    elif change == "extra":
        budgets["max_total_tokens"] = 10000
    elif change == "retries":
        budgets.update(max_model_calls=1, max_retries=1)
    else:
        budgets[{"tokens": "max_completion_tokens", "deadline": "deadline_s", "disk": "min_free_disk_bytes"}[change]] += 1
    profile["content_sha256"] = profile_content_sha256(profile)
    with pytest.raises(BenchmarkJobError) as exc:
        validate_profile(profile)
    assert exc.value.code == "bad_profile"


@pytest.mark.parametrize("name", ["smoke", "pilot-v2"])
def test_profile_schema_and_name_cannot_be_reinterpreted(name):
    from anvil_serving.benchmarking.profiles import BENCHMARK_PROFILE_SCHEMA, BENCHMARK_PROFILE_SCHEMA_V2

    profile = load_profile(name)
    profile["schema"] = BENCHMARK_PROFILE_SCHEMA if name == "pilot-v2" else BENCHMARK_PROFILE_SCHEMA_V2
    profile["content_sha256"] = profile_content_sha256(profile)
    with pytest.raises(BenchmarkJobError) as exc:
        validate_profile(profile)
    assert exc.value.code == "bad_profile"


def test_v2_unknown_suite_and_profile_paths_are_closed():
    from anvil_serving.benchmarking.profiles import suite_budgets

    profile = load_profile("pilot-v2")
    with pytest.raises(BenchmarkJobError) as exc:
        suite_budgets(profile, "mystery")
    assert exc.value.code == "unknown_suite"
    profile["suites"]["mystery"] = profile["suites"]["agentic"]
    profile["content_sha256"] = profile_content_sha256(profile)
    with pytest.raises(BenchmarkJobError) as exc:
        validate_profile(profile)
    assert exc.value.code == "unknown_suite"
    with pytest.raises(BenchmarkJobError) as exc:
        load_profile("../../pilot-v2.json")
    assert exc.value.code == "profile_not_found"


def test_cli_plan_and_metadata_share_profile_registry():
    from anvil_serving.benchmarking.jobs_cli import _parser, run
    from anvil_serving.commands.eval import _benchmark_suite

    node = _benchmark_suite("context", "Context")
    option = next(option for option in node.options if option.flags == ("--profile",))
    assert set(option.value_name.split("|")) == PROFILE_NAMES
    for name in PROFILE_NAMES:
        args = _parser().parse_args(["context", "plan", "--profile", name])
        assert args.profile == name
    plan = run(["context", "plan", "--profile", "pilot-v2", "--dry-run"])
    profile = load_profile("pilot-v2")
    assert plan["profile_sha256"] == profile["content_sha256"]
    assert plan["configuration"]["budgets"] == profile["suites"]["context"]["budgets"]
    with pytest.raises(SystemExit):
        _parser().parse_args(["context", "plan", "--profile", "/tmp/profile.json"])


@pytest.mark.parametrize("name, digest", [
    ("smoke", "b43f6a199164c2cbb92bde24672beebd781f58851d2d1bb944fc3d4d24a8e360"),
    ("scout", "59b8a4907019f2edf89548c4edcc99d276261d214a4791ceabaedc453e573cd2"),
    ("deep", "79876126301c2215e72e942ba0e23bb8024be0418babe40fdfda40dfafc37757"),
])
def test_v1_profile_bytes_stay_frozen(name, digest):
    import hashlib

    root = Path(__file__).resolve().parents[1]
    for directory in ("anvil_serving/_benchmark_profiles", "configs/benchmarks"):
        assert hashlib.sha256((root / directory / f"{name}.json").read_bytes()).hexdigest() == digest
