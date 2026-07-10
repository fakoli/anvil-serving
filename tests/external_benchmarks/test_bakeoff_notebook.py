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
