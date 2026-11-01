"""Independent, CPU-only fixtures for the predeclared sampling reference gate."""

import hashlib
import importlib.util
from pathlib import Path
import struct

import pytest

DIRECTORY = Path(__file__).resolve().parents[1] / "configs/runtime-patches/vllm/8244d1c4-split-topp"
specification = importlib.util.spec_from_file_location("sampling_reference", DIRECTORY / "reference.py")
reference = importlib.util.module_from_spec(specification)
specification.loader.exec_module(reference)


def mask(size, *tokens):
    return [i in tokens for i in range(size)]


@pytest.mark.parametrize("logits,top_p", [([], .95), ([float("nan")], .95), ([float("inf")], .95), ([float("-inf")], .95), ([0.], 0.), ([0.], 1.1)])
def test_invalid_reference_inputs_fail_closed(logits, top_p):
    with pytest.raises(ValueError):
        reference.stable_top_p_reference(logits, top_p)


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


def test_input_fingerprint_detects_mutation_and_declares_big_endian_order():
    logits = [0., 1., -2.]
    captured = reference.float32_sha256(logits)
    assert captured == hashlib.sha256(struct.pack(">fff", *logits)).hexdigest()
    logits[0] = -0.0
    assert reference.float32_sha256(logits) != captured


def test_native_comparator_is_exact_pinned_function_without_dispatcher():
    import ast
    source = (DIRECTORY / "native_comparator.py").read_text()
    parsed = ast.parse(source)
    functions = {node.name: ast.get_source_segment(source, node) for node in parsed.body
                 if isinstance(node, ast.FunctionDef)}
    expected = {
        "apply_top_k_top_p_pytorch": "275eb41831b2e43964366495abfc8d222aac121da40245760d36537ac5449f40",
        "apply_top_k_only": "2d58824a27f72f0a9f8f70b2d80fc212604e4d6177694209efce7aca8d05d57b",
    }
    assert set(functions) == set(expected)
    for name, digest in expected.items():
        assert hashlib.sha256(functions[name].encode()).hexdigest() == digest
    assert [alias.name for node in parsed.body if isinstance(node, ast.Import)
            for alias in node.names] == ["torch"]
    assert not any(isinstance(node, ast.ImportFrom) for node in parsed.body)
