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
    assert set(profile["suites"]) == {"context", "agentic", "swe"}


@pytest.mark.parametrize("name", sorted(PROFILE_NAMES))
def test_packaged_profiles_match_public_config(name):
    root = Path(__file__).resolve().parents[1]
    packaged = root / "anvil_serving" / "_benchmark_profiles" / f"{name}.json"
    public = root / "configs" / "benchmarks" / f"{name}.json"
    assert json.loads(packaged.read_text(encoding="utf-8")) == json.loads(
        public.read_text(encoding="utf-8")
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
