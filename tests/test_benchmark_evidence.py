from __future__ import annotations

import json
from pathlib import Path

import pytest

from anvil_serving import benchmark_evidence, cli


ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_summarize_capacity_artifact_is_prompt_free(tmp_path: Path) -> None:
    artifact = _write(tmp_path / "capacity.json", {
        "schema": "anvil-serving.benchmark/v1",
        "run_id": "run-1",
        "model": "local-model",
        "requests": 5,
        "completed": 5,
        "concurrency": 5,
        "context_tokens": 8192,
        "max_tokens": 256,
        "max_context_tokens": 262144,
        "metrics": {
            "ttft_p50_ms": 123.4,
            "e2e_p50_ms": 456.7,
            "output_tokens": 40,
            "throughput_tok_s": 12.5,
        },
        "requests_raw": [{"prompt": "secret prompt", "content": "secret output"}],
    })

    summary = benchmark_evidence.summarize_artifact(artifact)

    assert summary["kind"] == "capacity"
    assert summary["model"] == "local-model"
    assert summary["capacity"] == {
        "requests": 5,
        "completed": 5,
        "concurrency": 5,
        "context_tokens": 8192,
        "max_tokens": 256,
        "served_context_tokens": 262144,
        "ttft_p50_ms": 123.4,
        "e2e_p50_ms": 456.7,
        "output_tokens": 40,
        "aggregate_output_tok_s": 12.5,
        "wall_ms": None,
    }
    assert "secret" not in json.dumps(summary)


def test_summarize_protocol_v2_quality_counts_stability_and_budget_failures(
    tmp_path: Path,
) -> None:
    artifact = _write(tmp_path / "quality.json", {
        "schema": "anvil-serving.benchmark/v1",
        "identity": {"model": "thinking-model", "candidate_id": "candidate"},
        "evaluation_protocol": {
            "version": 2,
            "repetitions": 3,
            "visible_answer_tokens": 256,
            "reasoning_headroom_tokens": 4096,
        },
        "thinking": {"mode": "enabled", "control_status": "requested_unverified"},
        "timing": {"wall_ms": 1200.0},
        "suites": {
            "quality-suite": {
                "status": "failed",
                "checks": [
                    {
                        "status": "passed",
                        "attempt_count": 3,
                        "pass_count": 3,
                        "attempts": [{"content": "do not retain"}] * 3,
                    },
                    {
                        "status": "failed",
                        "attempt_count": 3,
                        "pass_count": 1,
                        "attempts": [
                            {"failure_class": "budget_exhausted"},
                            {"failure_class": "budget_exhausted"},
                            {"failure_class": None},
                        ],
                    },
                ],
            }
        },
    })

    summary = benchmark_evidence.summarize_artifact(artifact)
    suite = summary["quality"]["suites"][0]

    assert summary["kind"] == "quality"
    assert summary["protocol"]["visible_answer_tokens"] == 256
    assert summary["protocol"]["reasoning_headroom_tokens"] == 4096
    assert suite["fully_correct_items"] == 1
    assert suite["threshold_passed_items"] == 1
    assert suite["items"] == 2
    assert suite["attempts"] == 6
    assert suite["passed_attempts"] == 4
    assert suite["failure_classes"] == {"budget_exhausted": 2}
    assert len(summary["warnings"]) == 2
    assert "do not retain" not in json.dumps(summary)


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


def test_compare_flags_material_workload_differences(tmp_path: Path) -> None:
    first = _write(tmp_path / "first.json", {
        "schema": "anvil-serving.benchmark/v1",
        "model": "one",
        "concurrency": 1,
        "context_tokens": 8192,
        "max_tokens": 64,
        "metrics": {},
    })
    second = _write(tmp_path / "second.json", {
        "schema": "anvil-serving.benchmark/v1",
        "model": "two",
        "concurrency": 5,
        "context_tokens": 8192,
        "max_tokens": 64,
        "metrics": {},
    })

    result = benchmark_evidence.compare_artifacts([first, second])

    assert result["comparable"] is False
    assert result["differences"] == {"concurrency": [1, 5]}


def test_cli_evidence_list_emits_structured_json(tmp_path: Path, capsys) -> None:
    _write(tmp_path / "run.json", {
        "schema": "anvil-serving.benchmark/v1",
        "model": "cli-model",
        "metrics": {"throughput_tok_s": 9.5},
    })

    assert cli.main([
        "eval", "benchmark", "evidence", "list",
        "--root", str(tmp_path), "--format", "json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["matched"] == 1
    assert payload["artifacts"][0]["model"] == "cli-model"


def test_compare_requires_two_artifacts() -> None:
    with pytest.raises(benchmark_evidence.EvidenceError, match="between 2 and"):
        benchmark_evidence.compare_artifacts(["one.json"])


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
        "metrics": {"ttft_p50_ms": 10.0},
    }


def test_compare_fails_closed_for_missing_material_fields(tmp_path: Path) -> None:
    first = _write(tmp_path / "first.json", {
        "schema": "anvil-serving.benchmark/v1", "metrics": {},
    })
    second = _write(tmp_path / "second.json", {
        "schema": "anvil-serving.benchmark/v1", "metrics": {},
    })

    result = benchmark_evidence.compare_artifacts([first, second])

    assert result["comparable"] is False
    assert "model" in result["unknown_fields"]
    assert "workload.prompt_set_id" in result["unknown_fields"]
    assert "provenance.gpu" in result["unknown_fields"]


def test_compare_detects_thinking_control_difference(tmp_path: Path) -> None:
    first = _write(
        tmp_path / "first.json",
        _complete_capacity("one", concurrency=5, no_thinking=False),
    )
    second = _write(
        tmp_path / "second.json",
        _complete_capacity("two", concurrency=5, no_thinking=True),
    )

    result = benchmark_evidence.compare_artifacts([first, second])

    assert result["comparable"] is False
    assert result["differences"]["no_thinking"] == [False, True]
    assert result["differences"]["thinking_mode"] == ["unsupported", "disabled"]
    assert result["unknown_fields"] == {}


def test_legacy_quality_status_counts_as_one_attempt(tmp_path: Path) -> None:
    artifact = _write(tmp_path / "legacy.json", {
        "schema": "anvil-serving.benchmark/v1",
        "suites": {
            "legacy": {
                "checks": [
                    {"status": "passed"},
                    {"status": "failed"},
                    {"status": "failed"},
                ]
            }
        },
    })

    suite = benchmark_evidence.summarize_artifact(artifact)["quality"]["suites"][0]

    assert suite["fully_correct_items"] == 1
    assert suite["attempts"] == 3
    assert suite["passed_attempts"] == 1


def test_budget_exhaustion_variants_raise_warning(tmp_path: Path) -> None:
    artifact = _write(tmp_path / "budget.json", {
        "schema": "anvil-serving.benchmark/v1",
        "evaluation_protocol": {"version": 2},
        "suites": {
            "quality": {
                "checks": [{
                    "status": "failed",
                    "attempts": [
                        {"failure_class": "reasoning_budget_exhausted"},
                        {"failure_class": "visible_answer_budget_exhausted"},
                    ],
                }]
            }
        },
    })

    summary = benchmark_evidence.summarize_artifact(artifact)

    assert "one or more attempts exhausted the completion budget" in summary["warnings"]


def test_discovery_scan_cap_is_applied_before_collecting_entire_tree(
    tmp_path: Path, monkeypatch,
) -> None:
    for index in range(5):
        _write(tmp_path / f"{index}.json", {
            "schema": "anvil-serving.benchmark/v1",
            "model": f"model-{index}",
            "metrics": {},
        })
    monkeypatch.setattr(benchmark_evidence, "MAX_SCAN_ENTRIES", 2)

    result = benchmark_evidence.discover_artifacts(tmp_path, limit=10)

    assert result["scanned_entries"] == 2
    assert result["scanned_json_files"] <= 2
    assert result["matched"] <= 2
    assert result["truncated"] is True


def test_artifact_read_is_bounded(tmp_path: Path, monkeypatch) -> None:
    artifact = tmp_path / "large.json"
    artifact.write_bytes(b'{"schema":"anvil-serving.benchmark/v1","metrics":{},"pad":"xxxx"}')
    monkeypatch.setattr(benchmark_evidence, "MAX_ARTIFACT_BYTES", 16)

    with pytest.raises(benchmark_evidence.EvidenceError, match="read limit"):
        benchmark_evidence.summarize_artifact(artifact)


def test_compare_cli_fails_on_mismatch_unless_acknowledged(tmp_path: Path, capsys) -> None:
    first = _write(
        tmp_path / "first.json",
        _complete_capacity("one", concurrency=1, no_thinking=True),
    )
    second = _write(
        tmp_path / "second.json",
        _complete_capacity("two", concurrency=5, no_thinking=True),
    )

    args = ["compare", str(first), str(second), "--format", "json"]
    assert benchmark_evidence.main(args) == 1
    assert json.loads(capsys.readouterr().out)["comparable"] is False
    assert benchmark_evidence.main([*args, "--allow-mismatch"]) == 0
    assert json.loads(capsys.readouterr().out)["comparable"] is False


def test_compact_render_preserves_explicit_zero() -> None:
    row = benchmark_evidence._compact_row({
        "kind": "capacity",
        "model": "zero-model",
        "path": "zero.json",
        "capacity": {
            "concurrency": 0,
            "ttft_p50_ms": 0,
            "aggregate_output_tok_s": 0,
        },
        "protocol": {"repetitions": 0, "reasoning_headroom_tokens": 0},
        "quality": {"suites": []},
    })

    assert row[2:5] == ["0", "0", "0"]
    assert row[6:8] == ["0", "0"]


def test_summary_text_is_single_line_and_bounded(tmp_path: Path) -> None:
    artifact = _write(tmp_path / "text.json", {
        "schema": "anvil-serving.benchmark/v1",
        "model": "line one\n" + ("x" * 2_000),
        "metrics": {},
    })

    model = benchmark_evidence.summarize_artifact(artifact)["model"]

    assert "\n" not in model
    assert len(model) == benchmark_evidence.MAX_TEXT_CHARS
    assert model.endswith("...")


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
            },
            "evaluation_protocol": {
                "version": 2,
                "repetitions": 3,
                "visible_answer_tokens": 256,
                "reasoning_headroom_tokens": 1024,
            },
            "suites": {
                "quality": {
                    "source_sha256": source_hash,
                    "checks": [{"status": "passed", "attempt_count": 3, "pass_count": 3}],
                }
            },
        }

    first = _write(tmp_path / "first.json", quality("a" * 64))
    second = _write(tmp_path / "second.json", quality("b" * 64))

    result = benchmark_evidence.compare_artifacts([first, second])

    assert result["comparable"] is False
    assert "suites" in result["differences"]
    assert result["unknown_fields"] == {}


def test_quality_compare_requires_immutable_suite_hash(tmp_path: Path) -> None:
    def quality() -> dict:
        return {
            "schema": "anvil-serving.fast-tier-bakeoff/v1",
            "identity": {"model": "quality-model", "engine": "vllm", "gpu": "gpu"},
            "source_recipe": {"ref": "recipe#quality"},
            "thinking": {
                "mode": "enabled",
                "control_mechanism": "chat_template_kwargs",
                "control_status": "verified",
            },
            "evaluation_protocol": {
                "version": 2,
                "repetitions": 3,
                "visible_answer_tokens": 256,
                "reasoning_headroom_tokens": 1024,
            },
            "suites": {
                "quality": {
                    "source": "mutable/path.json",
                    "checks": [{"status": "passed", "attempt_count": 3, "pass_count": 3}],
                }
            },
        }

    first = _write(tmp_path / "first.json", quality())
    second = _write(tmp_path / "second.json", quality())

    result = benchmark_evidence.compare_artifacts([first, second])

    assert result["comparable"] is False
    assert result["unknown_fields"]["suites.source_sha256"] == [
        first.as_posix(), second.as_posix(),
    ]


def test_invalid_numeric_and_control_values_never_compare_equal(tmp_path: Path) -> None:
    raw = _complete_capacity("bad", concurrency=-1, no_thinking=True)
    raw["max_tokens"] = float("nan")
    raw["serve_flags"]["thinking_mode"] = "enabled"
    first = _write(tmp_path / "first.json", raw)
    second = _write(tmp_path / "second.json", raw)

    result = benchmark_evidence.compare_artifacts([first, second])

    assert result["comparable"] is False
    assert "capacity.max_tokens" in result["unknown_fields"]
    for errors in result["invalid_artifacts"].values():
        assert "capacity.concurrency must be a positive integer" in errors
        assert "protocol.no_thinking=true conflicts with thinking_mode" in errors


def test_provenance_difference_is_reported_without_changing_workload_match(
    tmp_path: Path,
) -> None:
    first_raw = _complete_capacity("one", concurrency=5, no_thinking=True)
    second_raw = _complete_capacity("two", concurrency=5, no_thinking=True)
    second_raw["engine"] = "sglang-pinned"
    first = _write(tmp_path / "first.json", first_raw)
    second = _write(tmp_path / "second.json", second_raw)

    result = benchmark_evidence.compare_artifacts([first, second])

    assert result["comparable"] is True
    assert result["differences"] == {}
    assert result["unknown_fields"] == {}
    assert result["provenance_differences"] == {
        "engine": ["vllm-pinned", "sglang-pinned"]
    }


def test_speculative_method_difference_blocks_comparability(tmp_path: Path) -> None:
    def speculative(model: str, method: str) -> dict:
        return {
            "schema": "anvil-serving.mtp-ab-probe/v1",
            "model": model,
            "engine": "vllm-pinned",
            "gpu": "RTX PRO 6000",
            "method": method,
            "mtp_off": {"mean_tok_s": 70.0},
            "mtp_on": {"mean_tok_s": 95.0},
            "speedup": 1.36,
        }

    first = _write(tmp_path / "first.json", speculative("one", "prompt A, 1024 tokens"))
    second = _write(tmp_path / "second.json", speculative("two", "prompt B, 2048 tokens"))

    result = benchmark_evidence.compare_artifacts([first, second])

    assert result["comparable"] is False
    assert "method_sha256" in result["differences"]
    assert result["unknown_fields"] == {}


def test_unknown_thinking_mode_and_malformed_optional_metric_fail_closed(
    tmp_path: Path,
) -> None:
    raw = _complete_capacity("bad", concurrency=5, no_thinking=False)
    raw["serve_flags"]["thinking_mode"] = "banana"
    raw["metrics"]["throughput_tok_s"] = "fast"
    first = _write(tmp_path / "first.json", raw)
    second = _write(tmp_path / "second.json", raw)

    result = benchmark_evidence.compare_artifacts([first, second])

    assert result["comparable"] is False
    for errors in result["invalid_artifacts"].values():
        assert "protocol.thinking_mode is unsupported: 'banana'" in errors
        assert "metrics.throughput_tok_s must be a finite number" in errors


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
