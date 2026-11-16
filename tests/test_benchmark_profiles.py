import copy

import pytest

from anvil_serving.benchmarking.jobs import BenchmarkJobError
from anvil_serving.benchmarking.profiles import (
    load_profile,
    profile_content_sha256,
    validate_profile,
)


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
