"""Offline validation for the frozen computer-use vision diagnostic corpus."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from anvil_serving.benchmarking import multimodal


CORPUS = Path(__file__).with_name("corpus.json")
MODEL_REVISION = "a" * 40
ENGINE_REVISION = "b" * 40
EXPECTED_CASES = {
    "small-text": {"small-text-1", "small-text-2"},
    "icons": {"icons-1", "icons-2"},
    "disabled": {"disabled-1", "disabled-2"},
    "charts": {"charts-1", "charts-2"},
    "missing": {"missing-1", "missing-2"},
    "adversarial": {"adversarial-1", "adversarial-2"},
}
INSTRUCTION_SENTINELS = {"adversarial-1": "banana", "adversarial-2": "orange"}


def _argv(corpus: Path, output: Path) -> list[str]:
    return [
        "--base-url", "http://127.0.0.1:30000/v1",
        "--model", "vision-diagnostic-candidate",
        "--model-revision", MODEL_REVISION,
        "--runtime-image", "synthetic/fixture:offline",
        "--engine-revision", ENGINE_REVISION,
        "--hardware", "synthetic offline fixture",
        "--corpus", str(corpus),
        "--output", str(output),
        "--thinking-mode", "disabled",
        "--dry-run",
    ]


def test_frozen_corpus_has_two_hash_bound_cases_per_category():
    loaded = multimodal.load_corpus(str(CORPUS))

    assert loaded["schema"] == "multimodal-corpus/v1"
    assert len(loaded["cases"]) == 12
    assert {case["id"].rsplit("-", 1)[0] for case in loaded["cases"]} == set(
        EXPECTED_CASES
    )
    for category, expected_ids in EXPECTED_CASES.items():
        cases = [case for case in loaded["cases"] if case["id"].startswith(category)]
        assert {case["id"] for case in cases} == expected_ids
        assert all(case["modality"] == "image" for case in cases)
        assert all(len(case["media"]) == 1 for case in cases)
        assert all(case["assertions"] for case in cases)
        assert all(len(case["media"][0]["sha256"]) == 64 for case in cases)
    for case in loaded["cases"]:
        sentinel = INSTRUCTION_SENTINELS.get(case["id"])
        if sentinel:
            assert "untrusted content" in case["prompt"]
            assert all(
                sentinel not in assertion.get("value", "").casefold()
                for assertion in case["assertions"]
            )


def test_dry_run_accepts_frozen_corpus_without_endpoint_or_artifact(
    monkeypatch, tmp_path, capsys
):
    def outbound_called(*_args, **_kwargs):
        raise AssertionError("dry run attempted an outbound request")

    monkeypatch.setattr(multimodal, "_endpoint_models", outbound_called)

    output = tmp_path / "evidence.json"
    assert multimodal.main(_argv(CORPUS, output), chat_request=outbound_called) == 0

    plan = json.loads(capsys.readouterr().out)
    assert plan["workload"] == "multimodal"
    assert plan["corpus"]["schema"] == "multimodal-corpus/v1"
    assert len(plan["corpus"]["cases"]) == 12
    assert not output.exists()


def test_dry_run_rejects_invalid_temporary_variant_before_endpoint(
    monkeypatch, tmp_path, capsys
):
    variant = json.loads(CORPUS.read_text(encoding="utf-8"))
    variant["cases"][0]["media"][0]["sha256"] = "0" * 64
    shutil.copytree(CORPUS.parent / "assets", tmp_path / "assets")
    invalid = tmp_path / "corpus.json"
    invalid.write_text(json.dumps(variant), encoding="utf-8")

    monkeypatch.setattr(
        multimodal,
        "_endpoint_models",
        lambda *_args, **_kwargs: pytest.fail("invalid dry run attempted discovery"),
    )

    with pytest.raises(SystemExit) as exc_info:
        multimodal.main(
            _argv(invalid, tmp_path / "evidence.json"),
            chat_request=lambda *_args, **_kwargs: pytest.fail(
                "invalid dry run attempted a model request"
            ),
        )

    assert exc_info.value.code == 2
    assert "media hash mismatch" in capsys.readouterr().err
