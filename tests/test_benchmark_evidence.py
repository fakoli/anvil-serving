from __future__ import annotations

import json
from pathlib import Path

import pytest

from anvil_serving import benchmark_evidence


ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_discover_filters_and_skips_non_benchmark_json(tmp_path: Path) -> None:
    _write(tmp_path / "one.json", {
        "schema": "anvil-serving.benchmark/v1",
        "model": "alpha-model",
        "metrics": {"ttft_p50_ms": 10},
    })
    _write(tmp_path / "two.json", {
        "schema": "anvil-serving.benchmark/v1",
        "model": "beta-model",
        "metrics": {"ttft_p50_ms": 20},
    })
    _write(tmp_path / "source-registry.json", {"sources": []})
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")

    result = benchmark_evidence.discover_artifacts(tmp_path, model="alpha", limit=10)

    assert result["matched"] == 1
    assert result["artifacts"][0]["model"] == "alpha-model"
    assert result["skipped"] == {"unrecognized": 1, "unreadable": 1, "unsafe": 0}


def _complete_capacity(model: str, *, concurrency: int, no_thinking: bool) -> dict:
    return {
        "schema": "anvil-serving.benchmark/v1",
        "model": model,
        "engine": "vllm-pinned",
        "gpu": "RTX PRO 6000",
        "prompt_set_id": "independent-8k-v1",
        "cache_policy": "disabled",
        "requests": 5,
        "completed": 5,
        "concurrency": concurrency,
        "context_tokens": 8192,
        "max_tokens": 256,
        "serve_flags": {
            "no_thinking": no_thinking,
            "thinking_mode": "disabled" if no_thinking else "unsupported",
            "shared_prefix_burst": False,
        },
        "sampling": {
            "temperature": {
                "requested": None,
                "effective_request": 0.0,
                "sent": True,
            },
            "top_p": {
                "requested": None,
                "effective_request": None,
                "sent": False,
            },
        },
        "metrics": {"ttft_p50_ms": 10.0},
    }


def _capacity_workload_identity(response_words: int = 0) -> dict:
    return {
        "prompt_contract_revision": "capacity-prompt-v1",
        "workload_kind": (
            "synthetic-repeated-code" if response_words else "synthetic-summary"
        ),
    }


def test_compare_capacity_workload_identity_is_backward_compatible_and_fail_closed(
    tmp_path: Path,
) -> None:
    legacy_first = _complete_capacity("one", concurrency=5, no_thinking=True)
    legacy_second = _complete_capacity("two", concurrency=5, no_thinking=True)
    first = _write(tmp_path / "first.json", legacy_first)
    second = _write(tmp_path / "second.json", legacy_second)
    assert benchmark_evidence.compare_artifacts([first, second])["comparable"] is True

    modern_first = {**legacy_first, "response_words": 0,
                    "workload_identity": _capacity_workload_identity()}
    modern_second = {**legacy_second, "response_words": 0,
                     "workload_identity": _capacity_workload_identity()}
    first = _write(tmp_path / "modern-first.json", modern_first)
    second = _write(tmp_path / "modern-second.json", modern_second)
    assert benchmark_evidence.compare_artifacts([first, second])["comparable"] is True

    mixed = benchmark_evidence.compare_artifacts([
        _write(tmp_path / "legacy.json", legacy_first), first,
    ])
    assert mixed["comparable"] is False
    assert mixed["differences"]["capacity_workload_identity"] == [
        None, _capacity_workload_identity(),
    ]

    malformed = _write(tmp_path / "malformed.json", {
        **modern_second,
        "workload_identity": {"prompt_contract_revision": "capacity-prompt-v1"},
    })
    invalid = benchmark_evidence.compare_artifacts([first, malformed])
    assert invalid["comparable"] is False
    assert any(
        "workload_identity must declare" in error
        for error in invalid["invalid_artifacts"][malformed.as_posix()]
    )

    explicit_null = _write(tmp_path / "explicit-null.json", {
        **modern_second, "workload_identity": None,
    })
    null_result = benchmark_evidence.compare_artifacts([first, explicit_null])
    assert any(
        "workload_identity must declare" in error
        for error in null_result["invalid_artifacts"][explicit_null.as_posix()]
    )


def test_compare_capacity_unique_prefix_namespace_keeps_declared_and_legacy_distinct(
    tmp_path: Path,
) -> None:
    legacy_first = _complete_capacity("one", concurrency=5, no_thinking=True)
    legacy_second = _complete_capacity("two", concurrency=5, no_thinking=True)
    first = _write(tmp_path / "legacy-first.json", legacy_first)
    second = _write(tmp_path / "legacy-second.json", legacy_second)
    assert benchmark_evidence.compare_artifacts([first, second])["comparable"] is True

    unique_first = {
        **legacy_first,
        "prompt_cache_mode": "unique",
        "unique_prefix_namespace": "capacity-v1:seed-request-index",
    }
    unique_second = {
        **legacy_second,
        "prompt_cache_mode": "unique",
        "unique_prefix_namespace": "capacity-v1:seed-request-index",
    }
    first = _write(tmp_path / "unique-first.json", unique_first)
    second = _write(tmp_path / "unique-second.json", unique_second)
    assert benchmark_evidence.compare_artifacts([first, second])["comparable"] is True

    mixed = benchmark_evidence.compare_artifacts([
        _write(tmp_path / "legacy.json", legacy_first), first,
    ])
    assert mixed["comparable"] is False
    assert mixed["differences"]["unique_prefix_namespace_declared"] == [False, True]

    shared_first = {
        **legacy_first,
        "prompt_cache_mode": "shared",
        "unique_prefix_namespace": None,
    }
    shared_second = {
        **legacy_second,
        "prompt_cache_mode": "shared",
        "unique_prefix_namespace": None,
    }
    first = _write(tmp_path / "shared-first.json", shared_first)
    second = _write(tmp_path / "shared-second.json", shared_second)
    assert benchmark_evidence.compare_artifacts([first, second])["comparable"] is True

    shared_legacy = {**legacy_second, "prompt_cache_mode": "shared"}
    second = _write(tmp_path / "shared-legacy.json", shared_legacy)
    assert benchmark_evidence.compare_artifacts([first, second])["comparable"] is True

    malformed = _write(tmp_path / "malformed-namespace.json", {
        **unique_second, "unique_prefix_namespace": "seed-7",
    })
    invalid = benchmark_evidence.compare_artifacts([first, malformed])
    assert invalid["comparable"] is False
    assert any(
        "unique prompt_cache_mode requires" in error
        for error in invalid["invalid_artifacts"][malformed.as_posix()]
    )


def test_compare_detects_quality_suite_identity_difference(tmp_path: Path) -> None:
    def quality(source_hash: str) -> dict:
        return {
            "schema": "anvil-serving.fast-tier-bakeoff/v1",
            "identity": {"model": "quality-model", "engine": "vllm", "gpu": "gpu"},
            "source_recipe": {"ref": "recipe#quality"},
            "thinking": {
                "mode": "enabled",
                "control_mechanism": "chat_template_kwargs",
                "control_status": "verified",
                "control_evidence": "docs/control-proof.json",
                "control_evidence_sha256": "c" * 64,
            },
            "evaluation_protocol": {
                "version": 3,
                "repetitions": 3,
                "visible_answer_tokens": 256,
                "reasoning_headroom_tokens": 1024,
                "minimum_pass_rate": 1.0,
            },
            "suites": {
                "quality": {
                    "source_sha256": source_hash,
                    "evidence_use": "ranking",
                    "validator_strength": "exact_choice",
                    "checks": [{"status": "passed", "attempt_count": 3, "pass_count": 3}],
                }
            },
        }

    first = _write(tmp_path / "first.json", quality("a" * 64))
    second = _write(tmp_path / "second.json", quality("b" * 64))

    result = benchmark_evidence.compare_artifacts([first, second])

    assert result["comparable"] is False
    assert "suites" in result["differences"]
    assert "protocol.sampling.recorded" in result["unknown_fields"]


def test_retained_protocol_v2_artifact_normalizes_without_custom_extraction() -> None:
    artifact = (
        ROOT
        / "docs/findings/2026-07-12-qwen36-protocol-v2-evidence"
        / "thinkingcap-mmlu-thinking-headroom4096.json"
    )

    summary = benchmark_evidence.summarize_artifact(artifact)
    suite = summary["quality"]["suites"][0]

    assert summary["model"] == "thinkingcap-qwen36-27b-fp8"
    assert summary["protocol"]["repetitions"] == 3
    assert summary["protocol"]["visible_answer_tokens"] == 256
    assert summary["protocol"]["reasoning_headroom_tokens"] == 4096
    assert summary["capacity"]["wall_ms"] == pytest.approx(458778.985)
    assert suite["fully_correct_items"] == 9
    assert suite["passed_attempts"] == 27
