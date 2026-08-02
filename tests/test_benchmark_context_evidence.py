"""Regression coverage for publication-safe per-context quality timing evidence."""

import json

import pytest

from anvil_serving import benchmark as bm


def test_bakeoff_context_row_retains_prefill_generation_and_decode(monkeypatch, tmp_path):
    monkeypatch.setattr(
        bm,
        "stream_chat",
        lambda *args, **kwargs: {
            "time_to_first_output": 0.1,
            "ttft": 0.4,
            "e2e": 0.6,
            "out_toks": 11,
            "output_token_source": "usage",
            "reasoning_chunks": 3,
            "content_chunks": 2,
            "usage": {"prompt_tokens": 200, "completion_tokens": 11},
        },
    )
    evidence_path = tmp_path / "context-evidence.json"

    rc = bm.main([
        "--bakeoff",
        "--base-url", "http://127.0.0.1:39010/v1",
        "--model", "reasoner",
        "--candidate-id", "reasoner",
        "--config-id", "reasoning-low",
        "--context-targets", "1024",
        "--suite", "context",
        "--max-model-len", "4096",
        "--evidence-out", str(evidence_path),
    ])

    assert rc == 0
    row = json.loads(evidence_path.read_text(encoding="utf-8"))["context"]["targets"][0]
    assert row["prompt_tokens"] == 200
    assert row["output_tokens"] == 11
    assert row["output_token_source"] == "usage"
    assert row["time_to_first_output_ms"] == pytest.approx(100)
    assert row["ttft_ms"] == pytest.approx(400)
    assert row["generation_ms"] == pytest.approx(500)
    assert row["visible_generation_ms"] == pytest.approx(200)
    assert row["e2e_ms"] == pytest.approx(600)
    assert row["effective_prefill_tok_s"] == pytest.approx(2000)
    assert row["decode_tok_s"] == pytest.approx(20)
    assert row["mean_inter_token_latency_ms"] == pytest.approx(50)
    assert row["reasoning_chunks"] == 3
    assert row["content_chunks"] == 2
