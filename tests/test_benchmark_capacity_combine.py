import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "skills"
    / "anvil-serving-benchmark-docs"
    / "scripts"
    / "combine_capacity_artifacts.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("capacity_combine", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _artifact(
    path: Path,
    *,
    start="2026-09-05T00:00:00Z",
    ctx=4096,
    **overrides,
):
    payload = {
        "schema": "anvil-serving.benchmark/v1",
        "measurement_protocol": "capacity-v3",
        "run_id": "run-" + path.stem,
        "started_at": start,
        "finished_at": "2026-09-05T00:00:10Z",
        "wall_clock_ms": 10000.0,
        "base_url": "http://127.0.0.1:30000/v1",
        "model": path.stem,
        "engine": "sglang",
        "gpu": path.stem,
        "requests": 1,
        "completed": 1,
        "failed": 0,
        "concurrency": 1,
        "context_tokens": ctx,
        "max_context_tokens": 262144,
        "max_tokens": 4,
        "response_words": 2,
        "prompt_cache_mode": "unique",
        "shared_prefix_tokens": 0,
        "request_canaries": True,
        "controlled_output_policy": "observe",
        "context_seed": 0,
        "serve_flags": {
            "shared_prefix_burst": False,
            "thinking_mode": "disabled",
            "reasoning_effort": None,
        },
        "request_timings": [{
            "request_index": 0,
            "time_to_first_output_ms": 100.0,
            "ttft_ms": 100.0,
            "e2e_ms": 200.0,
            "generation_ms": 100.0,
            "effective_prefill_tok_s": 1000.0,
            "decode_tok_s": 30.0,
            "mean_inter_token_latency_ms": 25.0,
            "prompt_tokens": 100,
            "output_tokens": 4,
            "output_token_source": "usage",
            "request_canary": {
                "passed": True, "marker_at_start": True,
                "foreign_marker_count": 0, "capture_complete": True,
            },
            "controlled_output": {
                "policy": "observe", "exact_adherence": True,
                "requested_words": 2, "observed_code_words": 2,
                "observed_extra_words": 0, "capture_complete": True,
                "passed": True,
            },
        }],
        "metrics": {"throughput_tok_s": 0.4, "output_tokens": 4},
    }
    payload.update(overrides)
    if "request_timings" not in overrides:
        row = payload["request_timings"][0]
        if not payload["request_canaries"]:
            row["request_canary"] = None
        if not payload["response_words"] or payload["controlled_output_policy"] is None:
            row["controlled_output"] = None
        else:
            row["controlled_output"].update({
                "policy": payload["controlled_output_policy"],
                "requested_words": payload["response_words"],
                "observed_code_words": payload["response_words"],
            })
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_combine_synchronized_replica_artifacts(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left)
    _artifact(right)
    combined = _module().combine([left, right])
    assert combined["replica_count"] == 2
    assert combined["concurrency"] == 2
    assert combined["metrics"]["output_tokens"] == 8
    assert combined["metrics"]["throughput_tok_s"] == pytest.approx(8 / 11)
    assert combined["metrics"]["throughput_tok_s_lower_bound"] == pytest.approx(8 / 11)
    assert combined["metrics"]["throughput_tok_s_upper_bound"] == pytest.approx(0.8)
    assert combined["metrics"]["throughput_estimate_kind"] == "conservative-lower-bound"
    assert combined["wall_clock_ms_lower_bound"] == pytest.approx(10000)
    assert combined["wall_clock_ms_upper_bound"] == pytest.approx(11000)
    assert combined["timeline"]["alignment"] == "legacy-second-precision-bounded"
    assert combined["replica_identity"]["status"] == "legacy-unverified"
    assert combined["metric_population"]["p99_interpretation"] == "descriptive-nearest-rank-only"
    assert combined["replicas"][0]["artifact"] == "left.json"
    assert combined["metrics"]["ttft_samples"] == 2
    assert combined["metrics"]["request_canary_passed"] == 2
    assert combined["metrics"]["controlled_output_exact_adherence_rate"] == 1.0
    assert combined["metrics"]["tpot_mean_ms"] == pytest.approx(100 / 3)
    assert combined["shared_prefix_tokens"] == 0
    assert combined["shared_prefix_identity"]["status"] == "ignored-for-unique-cache"
    assert combined["validation_provenance"]["request_canary_semantics"] == (
        "beginning-and-any-foreign-marker-verified"
    )
    assert combined["replicas"][0]["sha256"] == hashlib.sha256(
        left.read_bytes()
    ).hexdigest()
    assert combined["replicas"][1]["sha256"] == hashlib.sha256(
        right.read_bytes()
    ).hexdigest()


def test_combine_rejects_mismatched_workloads(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left)
    _artifact(right, ctx=8192)
    with pytest.raises(ValueError, match="context_tokens differs"):
        _module().combine([left, right])


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("max_tokens", 8),
        ("response_words", 3),
        ("prompt_cache_mode", "shared"),
        ("request_canaries", False),
        ("controlled_output_policy", "strict"),
        ("engine", "vllm"),
        ("context_seed", 9),
    ),
)
def test_combine_rejects_mismatched_output_and_cache_controls(
    tmp_path, field, value
):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left)
    _artifact(right, **{field: value})

    with pytest.raises(ValueError, match=rf"{field} differs"):
        _module().combine([left, right])


def test_combine_uses_exact_nanosecond_union_only_for_explicit_common_clock(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    epoch = 1_788_566_400_000_000_000
    _artifact(
        left,
        started_at_unix_ns=epoch,
        finished_at_unix_ns=epoch + 10_000_000_000,
        clock_domain_id="public-host-clock",
    )
    _artifact(
        right,
        started_at_unix_ns=epoch + 500_000_000,
        finished_at_unix_ns=epoch + 10_500_000_000,
        clock_domain_id="public-host-clock",
    )
    combined = _module().combine([left, right])
    assert combined["timeline"]["alignment"] == "exact-explicit-common-clock-domain"
    assert combined["wall_clock_ms"] == pytest.approx(10500)
    assert combined["metrics"]["throughput_tok_s"] == pytest.approx(8 / 10.5)
    assert combined["metrics"]["throughput_estimate_kind"] == "exact"

    _artifact(
        right,
        started_at_unix_ns=epoch + 500_000_000,
        finished_at_unix_ns=epoch + 10_500_000_000,
        clock_domain_id="another-host-clock",
    )
    bounded = _module().combine([left, right])
    assert bounded["timeline"]["alignment"] == "legacy-second-precision-bounded"


def test_combine_derives_conservative_legacy_phase_bounds(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    common = {
        "start": "2026-09-05T04:36:18Z",
        "finished_at": "2026-09-05T04:36:54Z",
    }
    _artifact(left, wall_clock_ms=35969.89649999887, **common)
    _artifact(right, wall_clock_ms=35555.37309998181, **common)
    combined = _module().combine([left, right])
    assert combined["wall_clock_ms_lower_bound"] == pytest.approx(35969.8965)
    assert combined["wall_clock_ms_upper_bound"] == pytest.approx(36525.2696)
    assert combined["metrics"]["throughput_tok_s_lower_bound"] == pytest.approx(
        8 / 36.5252696
    )
    assert combined["metrics"]["throughput_tok_s_upper_bound"] == pytest.approx(
        8 / 35.9698965
    )


def test_combine_requires_matching_configuration_fingerprints_when_present(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    first = "sha256:" + "a" * 64
    second = "sha256:" + "b" * 64
    matched_identity = {
        "model": "replicated-model",
        "source_recipe": "configs/replica.toml#model",
    }
    _artifact(left, configuration_fingerprint=first, **matched_identity)
    _artifact(right, configuration_fingerprint=second, **matched_identity)
    with pytest.raises(ValueError, match="configuration_fingerprint"):
        _module().combine([left, right])

    _artifact(right, configuration_fingerprint=first, **matched_identity)
    combined = _module().combine([left, right])
    assert combined["replica_identity"]["status"] == "declared-matching-fingerprint"
    assert combined["replica_identity"]["attestation"].startswith("operator-declared")


def test_combine_rejects_invalid_fingerprint_and_modern_identity_mismatch(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left, configuration_fingerprint="sha256:not-a-real-digest")
    _artifact(right, configuration_fingerprint="sha256:not-a-real-digest")
    with pytest.raises(ValueError, match="64 lowercase hex"):
        _module().combine([left, right])

    fingerprint = "sha256:" + "c" * 64
    _artifact(left, configuration_fingerprint=fingerprint, model="model-a")
    _artifact(right, configuration_fingerprint=fingerprint, model="model-b")
    with pytest.raises(ValueError, match="model values must match"):
        _module().combine([left, right])

    _artifact(left, configuration_fingerprint=fingerprint, model="model", source_recipe="a")
    _artifact(right, configuration_fingerprint=fingerprint, model="model", source_recipe="b")
    with pytest.raises(ValueError, match="source_recipe values must match"):
        _module().combine([left, right])


def test_combine_labels_legacy_canary_and_controlled_output_uncertainty(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    legacy_timing = [{
        "request_index": 0, "time_to_first_output_ms": 100.0,
        "ttft_ms": 100.0, "e2e_ms": 200.0, "generation_ms": 100.0,
        "prompt_tokens": 100, "output_tokens": 4,
        "request_canary": {"passed": True, "foreign_marker_count": 0},
    }]
    _artifact(left, controlled_output_policy=None, request_timings=legacy_timing)
    _artifact(right, controlled_output_policy=None, request_timings=legacy_timing)
    combined = _module().combine([left, right])
    assert combined["validation_provenance"]["request_canary_semantics"] == (
        "legacy-substring-only"
    )
    assert combined["validation_provenance"]["controlled_output_adherence"] == (
        "legacy-unobserved"
    )
    assert combined["validation_provenance"]["limitation"]


def test_combine_rejects_incomplete_timing_population_and_failed_canary(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left)
    _artifact(right, request_timings=[])
    with pytest.raises(ValueError, match="request_timings"):
        _module().combine([left, right])

    _artifact(right, request_timings=[{
        "request_index": 0, "time_to_first_output_ms": 100.0,
        "ttft_ms": 100.0, "e2e_ms": 200.0, "generation_ms": 100.0,
        "prompt_tokens": 100, "output_tokens": 4,
        "request_canary": {"passed": False},
    }])
    with pytest.raises(ValueError, match="request canary"):
        _module().combine([left, right])


def test_combine_rejects_duplicate_paths_symlinks_hardlinks_digests_and_run_identity(
    tmp_path,
):
    module = _module()
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left)
    _artifact(right)
    with pytest.raises(ValueError, match="duplicate replica input"):
        module.combine([left, left])

    symlink = tmp_path / "left-symlink.json"
    try:
        symlink.symlink_to(left)
    except OSError:
        symlink = None
    if symlink is not None:
        with pytest.raises(ValueError, match="filesystem alias"):
            module.combine([left, symlink])

    hardlink = tmp_path / "left-hardlink.json"
    os.link(left, hardlink)
    with pytest.raises(ValueError, match="filesystem alias"):
        module.combine([left, hardlink])

    right.write_bytes(left.read_bytes())
    with pytest.raises(ValueError, match="distinct SHA-256"):
        module.combine([left, right])

    shared_identity = {
        "run_id": "same-run", "model": "same-model",
        "base_url": "http://127.0.0.1:30000/v1", "gpu": "same-gpu",
    }
    _artifact(left, replica_note="left", **shared_identity)
    _artifact(right, replica_note="right", **shared_identity)
    with pytest.raises(ValueError, match="composite run identity"):
        module.combine([left, right])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("ttft_ms", float("nan"), "finite and nonnegative"),
        ("e2e_ms", -1.0, "finite and nonnegative"),
        ("generation_ms", float("inf"), "finite and nonnegative"),
        ("output_tokens", "4", "must be an integer"),
        ("prompt_tokens", "100", "must be an integer"),
    ),
)
def test_combine_rejects_invalid_timing_and_token_primitives(
    tmp_path, field, value, message
):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left)
    _artifact(right)
    payload = json.loads(right.read_text(encoding="utf-8"))
    payload["request_timings"][0][field] = value
    right.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        _module().combine([left, right])


def test_combine_rejects_misordered_timing_and_invalid_performance_eligibility(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left)
    _artifact(right)
    payload = json.loads(right.read_text(encoding="utf-8"))
    payload["request_timings"][0]["time_to_first_output_ms"] = 150.0
    right.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="time_to_first_output_ms <= ttft_ms"):
        _module().combine([left, right])

    _artifact(right, performance_eligible="true")
    with pytest.raises(ValueError, match="must be boolean or null"):
        _module().combine([left, right])


def test_combine_requires_contiguous_per_replica_request_indexes(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left)
    _artifact(right)
    payload = json.loads(right.read_text(encoding="utf-8"))
    payload["request_timings"][0]["request_index"] = 1
    right.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly 0 through completed"):
        _module().combine([left, right])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("policy", "strict", "policy differs"),
        ("requested_words", 3, "requested_words differs"),
        ("capture_complete", False, "adherence is inconsistent"),
        ("observed_code_words", 1, "adherence is inconsistent"),
        ("passed", False, "passed is inconsistent"),
    ),
)
def test_combine_binds_controlled_output_evidence_to_artifact(
    tmp_path, field, value, message
):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left)
    _artifact(right)
    payload = json.loads(right.read_text(encoding="utf-8"))
    payload["request_timings"][0]["controlled_output"][field] = value
    right.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        _module().combine([left, right])


def test_combine_strict_controlled_output_requires_complete_adherent_capture(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left, controlled_output_policy="strict")
    _artifact(right, controlled_output_policy="strict")
    for path in (left, right):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["request_timings"][0]["controlled_output"]["policy"] = "strict"
        path.write_text(json.dumps(payload), encoding="utf-8")
    assert _module().combine([left, right])["completed"] == 2

    payload = json.loads(right.read_text(encoding="utf-8"))
    controlled = payload["request_timings"][0]["controlled_output"]
    controlled.update({
        "capture_complete": False, "exact_adherence": None, "passed": False,
    })
    right.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="strict controlled-output capture is incomplete"):
        _module().combine([left, right])

    controlled.update({
        "capture_complete": True, "exact_adherence": False, "passed": False,
        "observed_code_words": 1,
    })
    right.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="strict controlled output did not adhere"):
        _module().combine([left, right])


def test_combine_rejects_non_object_optional_canary(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left, request_canaries=False)
    _artifact(right, request_canaries=False)
    payload = json.loads(right.read_text(encoding="utf-8"))
    payload["request_timings"][0]["request_canary"] = "recorded"
    right.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="request canary must be an object or null"):
        _module().combine([left, right])


def test_combine_rejects_evidence_for_unrequested_controls(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left, request_canaries=False)
    _artifact(right, request_canaries=False)
    payload = json.loads(right.read_text(encoding="utf-8"))
    payload["request_timings"][0]["request_canary"] = {"passed": True}
    right.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected request canary observation"):
        _module().combine([left, right])

    _artifact(left, response_words=0)
    _artifact(right, response_words=0)
    payload = json.loads(right.read_text(encoding="utf-8"))
    payload["request_timings"][0]["controlled_output"] = {
        "policy": "observe", "requested_words": 0, "capture_complete": True,
        "exact_adherence": True, "passed": True,
    }
    right.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected controlled-output observation"):
        _module().combine([left, right])


def test_combine_recomputes_derived_metrics_from_primitive_timings(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left)
    _artifact(right)
    for path in (left, right):
        payload = json.loads(path.read_text(encoding="utf-8"))
        row = payload["request_timings"][0]
        row.update({
            "generation_ms": 999.0,
            "effective_prefill_tok_s": 1.0,
            "decode_tok_s": 1.0,
            "mean_inter_token_latency_ms": 1.0,
        })
        path.write_text(json.dumps(payload), encoding="utf-8")
    combined = _module().combine([left, right])
    assert combined["metrics"]["generation_mean_ms"] == pytest.approx(100)
    assert combined["metrics"]["effective_prefill_tok_s_mean"] == pytest.approx(1000)
    assert combined["metrics"]["decode_tok_s_mean"] == pytest.approx(30)
    assert combined["metrics"]["tpot_mean_ms"] == pytest.approx(100 / 3)


def test_combine_reconciles_nanosecond_interval_with_monotonic_wall(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    epoch = 1_788_566_400_000_000_000
    common = {
        "started_at_unix_ns": epoch,
        "finished_at_unix_ns": epoch + 10_000_000_000,
        "clock_domain_id": "public-host-clock",
    }
    _artifact(left, **common)
    _artifact(right, wall_clock_ms=9000.0, **common)
    with pytest.raises(ValueError, match="does not reconcile"):
        _module().combine([left, right])


def test_combine_shared_prefix_identity_is_effective_and_legacy_aware(tmp_path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left, shared_prefix_tokens=999)
    _artifact(right, shared_prefix_tokens=123)
    unique = _module().combine([left, right])
    assert unique["shared_prefix_tokens"] == 0
    assert unique["shared_prefix_identity"]["status"] == "ignored-for-unique-cache"

    _artifact(left, prompt_cache_mode="shared", shared_prefix_tokens=8000)
    _artifact(right, prompt_cache_mode="shared", shared_prefix_tokens=8000)
    shared = _module().combine([left, right])
    assert shared["shared_prefix_tokens"] == 8000
    assert shared["shared_prefix_identity"]["status"] == "declared"

    _artifact(left, prompt_cache_mode="shared", shared_prefix_tokens=None)
    _artifact(right, prompt_cache_mode="shared", shared_prefix_tokens=None)
    legacy = _module().combine([left, right])
    assert legacy["shared_prefix_identity"]["status"] == "legacy-unknown"


def test_combiner_output_safety_rejects_inputs_unrelated_files_and_wrong_suffix(
    tmp_path, monkeypatch
):
    module = _module()
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    _artifact(left)
    _artifact(right)
    payload = module.combine([left, right], artifact_base=tmp_path)

    with pytest.raises(ValueError, match="must not overwrite"):
        module._validate_output_target(left, [left, right], payload)
    output_symlink = tmp_path / "output-symlink.json"
    try:
        output_symlink.symlink_to(left)
    except OSError:
        output_symlink = None
    if output_symlink is not None:
        with pytest.raises(ValueError, match="symbolic link"):
            module._validate_output_target(output_symlink, [left, right], payload)
    output_hardlink = tmp_path / "output-hardlink.json"
    os.link(left, output_hardlink)
    with pytest.raises(ValueError, match="must not alias"):
        module._validate_output_target(output_hardlink, [left, right], payload)
    with pytest.raises(ValueError, match=".json suffix"):
        module._validate_output_target(tmp_path / "aggregate.txt", [left, right], payload)

    unrelated = tmp_path / "unrelated.json"
    unrelated.write_text('{"schema": "something-else"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="non-aggregate"):
        module._validate_output_target(unrelated, [left, right], payload)
    unrelated.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="non-aggregate"):
        module._validate_output_target(unrelated, [left, right], payload)

    output = tmp_path / "aggregate.json"
    module._atomic_write_json(output, payload)
    module._validate_output_target(output, [left, right], payload)
    monkeypatch.setattr(
        sys, "argv",
        [str(SCRIPT), str(left), str(right), "--output", str(output)],
    )
    assert module.main() == 0
    assert b"\r\n" not in output.read_bytes()

    changed = json.loads(output.read_text(encoding="utf-8"))
    changed["replicas"][0]["sha256"] = "0" * 64
    output.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="different inputs"):
        module._validate_output_target(output, [left, right], payload)
