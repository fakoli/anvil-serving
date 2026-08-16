"""Versioned, content-addressed profiles for context, agentic, and SWE jobs."""

from __future__ import annotations

import copy
import hashlib
from importlib import resources
import json
import re
from typing import Any, Mapping

from .jobs import BenchmarkJobError, canonical_json_bytes
from .limits import MAX_BENCHMARK_JOB_SECONDS, MAX_CONTEXT_TARGET_TOKENS


BENCHMARK_PROFILE_SCHEMA = "anvil-serving.benchmark-profile/v1"
PROFILE_NAMES = frozenset({"smoke", "scout", "deep"})
SUITE_NAMES = frozenset({"context", "agentic", "swe"})
PROFILE_ROOT = resources.files("anvil_serving") / "_benchmark_profiles"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_HEX_RE = re.compile(r"^[0-9a-f]{40,64}$")
_IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


def profile_content_sha256(value: Mapping[str, Any]) -> str:
    content = copy.deepcopy(dict(value))
    content.pop("content_sha256", None)
    return hashlib.sha256(canonical_json_bytes(content)).hexdigest()


def _bounded_integer(value: Any, *, name: str, minimum: int, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise BenchmarkJobError(
            "bad_profile", f"{name} must be between {minimum} and {maximum}"
        )
    return value


def _validate_adapter(name: str, value: Any) -> dict[str, Any]:
    if not _ID_RE.fullmatch(name) or not isinstance(value, Mapping):
        raise BenchmarkJobError("bad_profile", "adapter lock is invalid")
    adapter = dict(value)
    kind = adapter.get("kind")
    allowed = {"kind", "source", "revision"} if kind in {"git", "dataset"} else {
        "kind",
        "image",
    }
    if set(adapter) != allowed:
        raise BenchmarkJobError("bad_profile", f"adapter {name!r} has unsupported fields")
    if kind in {"git", "dataset"}:
        source = adapter.get("source")
        revision = adapter.get("revision")
        if not isinstance(source, str) or not source.startswith("https://"):
            raise BenchmarkJobError("bad_profile", f"adapter {name!r} source is invalid")
        if not isinstance(revision, str) or not _HEX_RE.fullmatch(revision):
            raise BenchmarkJobError(
                "mutable_adapter", f"adapter {name!r} requires an immutable revision"
            )
    elif kind == "image":
        if not isinstance(adapter.get("image"), str) or not _IMAGE_RE.fullmatch(
            adapter["image"]
        ):
            raise BenchmarkJobError(
                "mutable_image", f"adapter {name!r} requires an image digest"
            )
    else:
        raise BenchmarkJobError("bad_profile", f"adapter {name!r} has an unknown kind")
    return adapter


def _validate_suite(name: str, value: Any, adapters: Mapping[str, Any]) -> dict[str, Any]:
    if name not in SUITE_NAMES or not isinstance(value, Mapping):
        raise BenchmarkJobError("unknown_suite", f"unsupported benchmark suite {name!r}")
    suite = copy.deepcopy(dict(value))
    common = {"cases", "repetitions", "timeout_s", "scoring", "requirements", "adapters"}
    extra = {
        "context": {"token_buckets", "positions", "output_headroom_tokens"},
        "agentic": {"max_steps", "max_completion_tokens"},
        "swe": {"instance_limit", "max_steps", "max_completion_tokens"},
    }[name]
    if set(suite) != common | extra:
        raise BenchmarkJobError("bad_profile", f"suite {name!r} has unsupported fields")
    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases or not all(
        isinstance(item, str) and _ID_RE.fullmatch(item) for item in cases
    ):
        raise BenchmarkJobError("bad_profile", f"suite {name!r} cases are invalid")
    _bounded_integer(suite.get("repetitions"), name="repetitions", minimum=1, maximum=20)
    _bounded_integer(
        suite.get("timeout_s"), name="timeout_s", minimum=1, maximum=MAX_BENCHMARK_JOB_SECONDS
    )
    scoring = suite.get("scoring")
    if not isinstance(scoring, Mapping) or not scoring:
        raise BenchmarkJobError("missing_scoring_policy", f"suite {name!r} needs scoring")
    floor = scoring.get("pass_rate_floor")
    if not isinstance(floor, (int, float)) or isinstance(floor, bool) or not 0 <= floor <= 1:
        raise BenchmarkJobError("bad_profile", "pass_rate_floor must be from 0 through 1")
    if not isinstance(suite.get("requirements"), Mapping):
        raise BenchmarkJobError("bad_profile", f"suite {name!r} requirements must be an object")
    adapter_names = suite.get("adapters")
    if not isinstance(adapter_names, list) or not all(
        isinstance(item, str) and item in adapters for item in adapter_names
    ):
        raise BenchmarkJobError("bad_profile", f"suite {name!r} references an unknown adapter")
    if name == "context":
        buckets = suite.get("token_buckets")
        if not isinstance(buckets, list) or not buckets or buckets != sorted(set(buckets)):
            raise BenchmarkJobError("bad_profile", "context token_buckets must be sorted unique")
        for bucket in buckets:
            _bounded_integer(
                bucket,
                name="context token bucket",
                minimum=128,
                maximum=MAX_CONTEXT_TARGET_TOKENS,
            )
        positions = suite.get("positions")
        if not isinstance(positions, list) or not positions or not all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and 0 <= item <= 1
            for item in positions
        ):
            raise BenchmarkJobError("bad_profile", "context positions are invalid")
        _bounded_integer(
            suite.get("output_headroom_tokens"),
            name="output_headroom_tokens",
            minimum=1,
            maximum=65536,
        )
        baseline = scoring.get("baseline_bucket")
        if baseline not in buckets:
            raise BenchmarkJobError("bad_profile", "context baseline_bucket is absent")
        drop = scoring.get("max_relative_drop")
        if not isinstance(drop, (int, float)) or isinstance(drop, bool) or not 0 <= drop <= 1:
            raise BenchmarkJobError("bad_profile", "max_relative_drop must be from 0 through 1")
    else:
        _bounded_integer(suite.get("max_steps"), name="max_steps", minimum=1, maximum=1000)
        _bounded_integer(
            suite.get("max_completion_tokens"),
            name="max_completion_tokens",
            minimum=1,
            maximum=65536,
        )
        if name == "swe":
            _bounded_integer(
                suite.get("instance_limit"), name="instance_limit", minimum=1, maximum=500
            )
    return suite


def validate_profile(value: Any, *, observed_context: int | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BenchmarkJobError("bad_profile", "benchmark profile must be an object")
    profile = copy.deepcopy(dict(value))
    required_keys = {"schema", "name", "description", "content_sha256", "adapters", "suites"}
    if set(profile) != required_keys or profile.get("schema") != BENCHMARK_PROFILE_SCHEMA:
        raise BenchmarkJobError("bad_profile", "benchmark profile schema or fields are invalid")
    name = profile.get("name")
    if name not in PROFILE_NAMES:
        raise BenchmarkJobError("bad_profile", "benchmark profile name is unknown")
    if not isinstance(profile.get("description"), str) or not profile["description"]:
        raise BenchmarkJobError("bad_profile", "benchmark profile needs a description")
    adapters_raw = profile.get("adapters")
    if not isinstance(adapters_raw, Mapping) or not adapters_raw:
        raise BenchmarkJobError("bad_profile", "benchmark profile needs adapter locks")
    adapters = {
        adapter_name: _validate_adapter(adapter_name, adapter)
        for adapter_name, adapter in adapters_raw.items()
    }
    suites_raw = profile.get("suites")
    if not isinstance(suites_raw, Mapping) or set(suites_raw) != SUITE_NAMES:
        raise BenchmarkJobError("unknown_suite", "profile must define context, agentic, and swe")
    suites = {
        suite_name: _validate_suite(suite_name, suite, adapters)
        for suite_name, suite in suites_raw.items()
    }
    expected = profile_content_sha256(profile)
    if profile.get("content_sha256") != expected:
        raise BenchmarkJobError("profile_digest_mismatch", "profile content identity is invalid")
    profile["adapters"] = adapters
    profile["suites"] = suites
    if observed_context is not None:
        _bounded_integer(
            observed_context,
            name="observed_context",
            minimum=128,
            maximum=MAX_CONTEXT_TARGET_TOKENS,
        )
        context = suites["context"]
        usable = observed_context - context["output_headroom_tokens"]
        if usable < 128 or context["token_buckets"][-1] > usable:
            raise BenchmarkJobError(
                "context_capacity_exceeded",
                "profile context buckets exceed observed capacity after output headroom",
                {"observed_context": observed_context, "usable_context": usable},
            )
    return profile


def load_profile(name: str, *, observed_context: int | None = None) -> dict[str, Any]:
    if name not in PROFILE_NAMES:
        raise BenchmarkJobError("profile_not_found", f"unknown benchmark profile {name!r}")
    path = PROFILE_ROOT / f"{name}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkJobError("profile_unavailable", "benchmark profile cannot be loaded") from exc
    return validate_profile(value, observed_context=observed_context)
