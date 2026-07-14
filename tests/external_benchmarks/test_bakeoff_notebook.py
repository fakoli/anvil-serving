"""Tests for the bakeoff notebook — persistence (store), scoring/verdict
(notebook), and the CLI sub-verb. Mirrors test_external_benchmarks._scratch."""
import atexit
import json
import shutil
from pathlib import Path

import pytest

from anvil_serving.external_benchmarks import cli, notebook, schema, store

SCRATCH = Path(__file__).resolve().parents[1] / ".scratch_bakeoff_notebook"
atexit.register(lambda: shutil.rmtree(SCRATCH, ignore_errors=True))


def _scratch(name):
    path = SCRATCH / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def _evidence(candidate, *, run_id, voice=800.0, ipr=1.0, tool=True,
              session=True, ctx=65536, failures=None):
    return {
        "schema": "anvil-serving.fast-tier-bakeoff/v1",
        "run_id": run_id,
        "identity": {
            "candidate_id": candidate, "config_id": "cfg1",
            "model": "m", "base_url": "http://x", "started_at": "2026-07-09T00:00:00Z",
        },
        "source_recipe": {"ref": "r", "serve_command": "serve"},
        "evaluation_protocol": {"version": 3, "repetitions": 3},
        "suites": {
            "ranking": {
                "status": "passed",
                "evidence_use": "ranking",
                "validator_strength": "exact_choice",
                "checks": [
                    {"status": "passed", "attempt_count": 3, "pass_count": 3}
                ],
            }
        },
        "score_inputs": {
            "voice_latency_ms": voice, "intelligence_pass_rate": ipr,
            "tool_call_passed": tool, "session_recall_passed": session,
            "usable_context_tokens": ctx, "ttft_p50_ms": 120.0, "e2e_p50_ms": 900.0,
            "thinking_mode": "default",
        },
        "failures": failures or [],
    }


# --- schema ---------------------------------------------------------------------

def test_v_tables_created():
    db = _scratch("schema") / "nb.sqlite"
    store.init_db(db)
    assert "bakeoff_runs" in schema.EXPECTED_TABLES
    assert "bakeoff_verdicts" in schema.EXPECTED_TABLES


# --- store: append + latest-per-candidate keying --------------------------------

def test_record_and_latest_per_key():
    db = _scratch("record") / "nb.sqlite"
    store.record_bakeoff_run(db, _evidence("cand-a", run_id="r1"),
                             task="fast", hardware="4090")
    # a SECOND run of the same candidate/config/task/hardware = newer row
    store.record_bakeoff_run(db, _evidence("cand-a", run_id="r2", voice=600.0),
                             task="fast", hardware="4090")
    store.record_bakeoff_run(db, _evidence("cand-b", run_id="r3"),
                             task="fast", hardware="4090")

    latest = store.list_bakeoff_runs(db, task="fast", hardware="4090")
    by = {r["candidate_id"]: r for r in latest}
    assert set(by) == {"cand-a", "cand-b"}
    assert by["cand-a"]["run_id"] == "r2"          # newest wins
    assert len(store.list_bakeoff_runs(db, latest_per_candidate=False)) == 3  # history kept


def test_task_hardware_filter_scopes():
    db = _scratch("scope") / "nb.sqlite"
    store.record_bakeoff_run(db, _evidence("a", run_id="r1"), task="fast", hardware="4090")
    store.record_bakeoff_run(db, _evidence("a", run_id="r2"), task="heavy", hardware="4090")
    assert len(store.list_bakeoff_runs(db, task="fast")) == 1
    assert len(store.list_bakeoff_runs(db, hardware="4090")) == 2


def test_record_rejects_evidence_without_identity():
    db = _scratch("bad") / "nb.sqlite"
    bad = _evidence("x", run_id="r1")
    bad["identity"].pop("candidate_id")
    with pytest.raises(ValueError, match="candidate_id"):
        store.record_bakeoff_run(db, bad, task="fast", hardware="4090")


@pytest.mark.parametrize("mutation", ["legacy", "diagnostic", "weak", "single"])
def test_record_rejects_non_ranking_protocol_evidence(mutation):
    db = _scratch("protocol-" + mutation) / "nb.sqlite"
    evidence = _evidence("x", run_id="r1")
    if mutation == "legacy":
        evidence.pop("evaluation_protocol")
    elif mutation == "diagnostic":
        evidence["suites"]["ranking"]["evidence_use"] = "diagnostic"
    elif mutation == "weak":
        evidence["suites"]["ranking"]["validator_strength"] = "deterministic_marker"
    else:
        evidence["suites"]["ranking"]["checks"][0]["attempt_count"] = 1
        evidence["suites"]["ranking"]["checks"][0]["pass_count"] = 1
    with pytest.raises(ValueError):
        store.record_bakeoff_run(db, evidence, task="fast", hardware="4090")


# --- notebook: rubric + verdict (also covered by _selfcheck) --------------------

def test_selfcheck_passes():
    assert notebook._selfcheck() == 0


def test_verdict_hard_gate_beats_score():
    # a high-scoring candidate that fails a hard gate still LOSES
    gated = _evidence("g", run_id="r")["score_inputs"]
    row = {"candidate_id": "g", "config_id": "c", "task": "t", "hardware": "h",
           **gated, "tool_call_passed": False, "failures_json": "[]"}
    v = notebook.verdict(row)
    assert v["result"] == "lose" and "tool_call_passed" in v["reason"]


def test_score_run_is_total_on_bad_input():
    # zero/negative budgets and stringly-typed metrics must score 0, not raise
    row = {"candidate_id": "x", "config_id": "c", "task": "t", "hardware": "h",
           "voice_latency_ms": "high", "intelligence_pass_rate": None,
           "usable_context_tokens": "lots", "failures_json": "not-json"}
    s = notebook.score_run(row, targets={"voice_budget_ms": 0, "context_target_tokens": 0})
    assert s["voice"] == 0.0 and s["context"] == 0.0 and s["intelligence_tool"] == 0.0
    # and a valid metric with a zero budget also just scores 0 (no ZeroDivision)
    ok = {**row, "voice_latency_ms": 500.0, "usable_context_tokens": 40000}
    s2 = notebook.score_run(ok, targets={"voice_budget_ms": 0, "context_target_tokens": 0})
    assert s2["voice"] == 0.0 and s2["context"] == 0.0


def test_pass_score_is_overridable():
    # a gates-passing candidate at 75 wins by default but holds if pass_score=90
    row = {"candidate_id": "x", "config_id": "c", "task": "t", "hardware": "h",
           "voice_latency_ms": 900.0, "intelligence_pass_rate": 0.9,
           "tool_call_passed": True, "session_recall_passed": True,
           "usable_context_tokens": 65536, "failures_json": "[]"}
    assert notebook.verdict(row)["result"] == "win"
    assert notebook.verdict(row, targets={"pass_score": 99.0})["result"] == "hold"


def test_latest_keys_on_recording_order_not_started_at():
    # a run recorded LATER but with an EARLIER/absent started_at is still latest
    db = _scratch("recency") / "nb.sqlite"
    early = _evidence("a", run_id="r-old")
    early["identity"]["started_at"] = "2026-01-01T00:00:00Z"
    store.record_bakeoff_run(db, early, task="fast", hardware="h")
    newer = _evidence("a", run_id="r-new")
    newer["identity"].pop("started_at")  # no started_at, but recorded second
    store.record_bakeoff_run(db, newer, task="fast", hardware="h")
    latest = store.list_bakeoff_runs(db, task="fast", hardware="h")
    assert len(latest) == 1 and latest[0]["run_id"] == "r-new"


def test_no_fingerprint_pollution():
    db = _scratch("nofp") / "nb.sqlite"
    store.record_bakeoff_run(db, _evidence("a", run_id="r1"), task="fast", hardware="h")
    import sqlite3
    with sqlite3.connect(db) as conn:
        n_fp = conn.execute("SELECT COUNT(*) FROM serve_fingerprints").fetchone()[0]
        fp_id = conn.execute("SELECT serve_fingerprint_id FROM bakeoff_runs").fetchone()[0]
    assert n_fp == 0 and fp_id is None


def test_verdict_vs_baseline():
    strong = {"candidate_id": "s", "config_id": "c", "task": "t", "hardware": "h",
              "voice_latency_ms": 700.0, "intelligence_pass_rate": 1.0,
              "tool_call_passed": True, "session_recall_passed": True,
              "usable_context_tokens": 65536, "failures_json": "[]"}
    weak = {**strong, "candidate_id": "w", "voice_latency_ms": 2000.0,
            "intelligence_pass_rate": 0.6, "usable_context_tokens": 20000}
    assert notebook.verdict(strong, weak)["result"] == "win"
    assert notebook.verdict(weak, strong)["result"] == "lose"


# --- CLI ------------------------------------------------------------------------

def test_cli_add_list_render(capsys):
    db = _scratch("cli") / "nb.sqlite"
    ev_dir = _scratch("cli-ev")
    a = ev_dir / "a.json"
    a.write_text(json.dumps(_evidence("cand-a", run_id="r1")), encoding="utf-8")
    b = ev_dir / "b.json"
    b.write_text(json.dumps(_evidence("cand-b", run_id="r2", voice=2200.0,
                                      ipr=0.5, ctx=16384)), encoding="utf-8")

    assert cli.main(["notebook", "add", "--evidence", str(a),
                     "--task", "fast", "--hardware", "4090", "--db", str(db)]) == 0
    assert cli.main(["notebook", "add", "--evidence", str(b),
                     "--task", "fast", "--hardware", "4090", "--db", str(db)]) == 0

    assert cli.main(["notebook", "list", "--task", "fast", "--db", str(db)]) == 0
    listing = capsys.readouterr().out
    assert "cand-a" in listing and "cand-b" in listing

    assert cli.main(["notebook", "render", "--task", "fast",
                     "--baseline", "cand-b", "--db", str(db)]) == 0
    md = capsys.readouterr().out
    assert "Candidate matrix" in md and "Determination" in md
    assert "cand-a" in md and "WIN" in md.upper()
