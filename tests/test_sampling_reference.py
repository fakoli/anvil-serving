"""Independent, CPU-only fixtures for the predeclared sampling reference gate."""

import hashlib
import importlib.util
import math
from pathlib import Path
import struct

import pytest

DIRECTORY = Path(__file__).resolve().parents[1] / "configs/runtime-patches/vllm/8244d1c4-split-topp"
specification = importlib.util.spec_from_file_location("sampling_reference", DIRECTORY / "reference.py")
reference = importlib.util.module_from_spec(specification)
specification.loader.exec_module(reference)


def mask(size, *tokens):
    return [i in tokens for i in range(size)]


def test_equal_probabilities_use_minimal_prefix_and_lower_id_ties():
    probabilities, order, count = reference.stable_top_p_reference([0.] * 8, 0.5)
    assert probabilities == [0.125] * 8
    assert order == list(range(8)) and count == 4
    result = reference.compare_masks([0.] * 8, 0.5, mask(8, 0, 1, 2, 3), mask(8, 0, 1, 2, 3))
    assert result["passed"] and result["baseline"]["exact"] and result["patched"]["exact"]
    assert result["reference"]["retained_mass"] == .5
    assert result["reference"]["remove_lowest_mass"] == .375


def test_float32_inputs_and_shifted_exponentials():
    def fp32(value):
        return struct.unpack("f", struct.pack("f", value))[0]
    logits = [fp32(1e9), fp32(1e9 + 1), fp32(-1e9)]
    assert logits[0] == logits[1]
    probabilities, order, count = reference.stable_top_p_reference(logits, fp32(.95))
    assert probabilities == [.5, .5, 0.] and order == [0, 1, 2] and count == 2


def test_nonuniform_prefix_matches_analytic_distribution():
    probabilities, order, count = reference.stable_top_p_reference([0., math.log(2), math.log(4), math.log(8)], .75)
    assert order == [3, 2, 1, 0] and count == 2
    assert probabilities == pytest.approx([1/15, 2/15, 4/15, 8/15])


def test_baseline_undercoverage_fails_even_with_exact_patched_mask():
    result = reference.compare_masks([0.] * 8, .5, mask(8, 0, 1, 2), mask(8, 0, 1, 2, 3))
    assert not result["passed"]
    assert not result["checks"]["baseline_contains_reference"]
    assert result["baseline"]["missing_count"] == 1
    assert result["baseline"]["missing_mass"] == .125


def test_mass_coverage_does_not_excuse_tie_holes_or_missing_reference():
    result = reference.compare_masks([0.] * 8, .5, mask(8, 0, 1, 2, 3), mask(8, 0, 1, 2, 4))
    assert result["patched"]["retained_mass"] == .5
    assert not result["passed"] and result["patched"]["prefix_holes"] == 1
    assert result["patched"]["reference_difference"]["first32"] == [3, 4]
    assert result["version_difference"]["count"] == 2


def test_bounded_overcoverage_requires_no_regression():
    result = reference.compare_masks([0.] * 10, .5, mask(10, *range(8)), mask(10, *range(7)))
    assert result["passed"] and result["patched"]["extra_count"] == 2
    worse = reference.compare_masks([0.] * 10, .5, mask(10, *range(6)), mask(10, *range(7)))
    assert not worse["passed"] and not worse["checks"]["patched_difference_not_worse"]


@pytest.mark.parametrize("reference_count,extra,passed", [(800, 3, True), (800, 4, False), (1000, 4, True), (1000, 5, False)])
def test_count_gate_absolute_exception_and_strict_half_percent(reference_count, extra, passed):
    size = reference_count * 2
    kept = [i < reference_count + extra for i in range(size)]
    result = reference.compare_masks([0.] * size, .5, kept, kept)
    assert result["reference"]["kept_count"] == reference_count
    assert result["checks"]["patched_count_error_bounded"] is passed
    assert result["passed"] is passed


def test_large_difference_has_complete_count_and_hash_bounded_indices():
    result = reference.compare_masks([0.] * 100, .5, [True] * 100, [True] * 100)
    difference = result["patched"]["reference_difference"]
    assert difference["count"] == 50 and difference["first32"] == list(range(50, 82))
    assert difference["sha256_u32be"] == hashlib.sha256(b"".join(struct.pack(">I", i) for i in range(50, 100))).hexdigest()


@pytest.mark.parametrize("logits,top_p", [([], .95), ([float("nan")], .95), ([float("inf")], .95), ([float("-inf")], .95), ([0.], 0.), ([0.], 1.1)])
def test_invalid_reference_inputs_fail_closed(logits, top_p):
    with pytest.raises(ValueError):
        reference.stable_top_p_reference(logits, top_p)


def test_invalid_masks_fail_closed():
    with pytest.raises(ValueError):
        reference.compare_masks([0.] * 2, .5, [True], [True])
    with pytest.raises(ValueError):
        reference.compare_masks([0.] * 2, .5, [1, 0], [True, False])


def test_image_and_runtime_bind_reviewed_helpers():
    from anvil_serving.reasoning_probe_runtime import _PATCH_FILES
    dockerfile = (DIRECTORY / "Dockerfile").read_text()
    for name in ("reference.py", "kernel_regression.py", "verify_patch.py", "launch.py"):
        digest = hashlib.sha256((DIRECTORY / name).read_bytes()).hexdigest()
        assert _PATCH_FILES[name] == digest
        assert f"{digest}  /opt/anvil-sampling/{name}" in dockerfile
    for name in ("README.md", "split-topp.patch", "trace.patch"):
        digest = hashlib.sha256((DIRECTORY / name).read_bytes()).hexdigest()
        assert f"{digest}  /opt/anvil-sampling/{name}" in dockerfile


def test_boundary_disagreement_fails_closed_without_tolerance(monkeypatch):
    real_fsum = reference.math.fsum
    calls = 0
    def disagree(values):
        nonlocal calls
        calls += 1
        value = real_fsum(values)
        return value - 1 if calls == 2 else value
    monkeypatch.setattr(reference.math, "fsum", disagree)
    result = reference.compare_masks([0.] * 8, .5, mask(8, 0, 1, 2, 3), mask(8, 0, 1, 2, 3))
    assert result["passed"] is False
    assert result["oracle_error"] == "oracle_boundary_ambiguous"


@pytest.mark.parametrize("corruption", [0.01, -0.0, math.nan, math.inf])
def test_output_corruption_fails_including_signed_zero(corruption):
    result = reference.compare_outputs([0.] * 8, .5,
        [0.] * 4 + [-math.inf] * 4,
        [corruption] + [0.] * 3 + [-math.inf] * 4)
    assert not result["passed"]
    assert result["invalid_output_counts"] == [0, 1]


def test_valid_output_and_excluded_value_contract():
    output = [0.] * 4 + [-math.inf] * 4
    assert reference.compare_outputs([0.] * 8, .5, output, output)["passed"]
    with pytest.raises(ValueError):
        reference.compare_outputs([0.] * 8, .5, output[:-1], output)


@pytest.mark.parametrize("excluded_corruption", [0.01, math.nan, math.inf])
def test_invalid_excluded_output_is_not_accepted_as_a_mask(excluded_corruption):
    original = [0.] * 8
    baseline = [0.] * 4 + [-math.inf] * 4
    patched = baseline.copy()
    patched[-1] = excluded_corruption
    result = reference.compare_outputs(original, .5, baseline, patched)
    assert not result["passed"] and result["invalid_output_counts"] == [0, 1]


def test_input_fingerprint_detects_mutation_and_declares_big_endian_order():
    logits = [0., 1., -2.]
    captured = reference.float32_sha256(logits)
    assert captured == hashlib.sha256(struct.pack(">fff", *logits)).hexdigest()
    logits[0] = -0.0
    assert reference.float32_sha256(logits) != captured
