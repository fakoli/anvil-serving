"""Hermetic tests for the T015 quality-profile bootstrap (replay path only).

These exercise ``--replay`` over the COMMITTED eval fixtures under
``docs/findings/eval-data/``. No network, no real tier, no clock dependence. The
live integration path (:func:`run_live`) is asserted to be guarded — it must NOT
reach a tier from CI.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from anvil_serving.router import profile_bootstrap as pb
from anvil_serving.router.profile_store import ProfileEntry, ProfileStore

# Repo root: tests/router/this_file -> parents[2].
REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DATA = REPO_ROOT / "docs" / "findings" / "eval-data"

# The committed planning eval normalizes to these per-tier scores (known
# aggregates: frontier 24.75/25, fast 16.0/25, heavy 13.25/25). All three pairs
# are the single work-class "planning".
EXPECTED = {
    ("cloud", "planning"): {"score": 0.99, "decision": "allow", "model": "frontier"},
    ("fast-local", "planning"): {"score": 0.64, "decision": "deny", "model": "fast"},
    ("heavy-local", "planning"): {"score": 0.53, "decision": "deny", "model": "heavy"},
}


def test_eval_fixtures_present():
    """Guard: the committed fixtures the replay reads must exist."""
    eval_dirs = pb.discover_eval_dirs(EVAL_DATA)
    assert eval_dirs, f"no committed eval dirs found under {EVAL_DATA}"
    assert any(d.name == "2026-06-28-planning-capability" for d in eval_dirs)


def test_replay_entry_per_model_work_class():
    """One entry per (tier, work-class), each with a valid score + positive n."""
    entries = pb.build_entries(EVAL_DATA)
    keys = {(e.tier_id, e.work_class) for e in entries}
    assert keys == set(EXPECTED)

    for e in entries:
        assert 0.0 <= e.quality_score <= 1.0, e
        assert e.sample_n > 0, e
        assert e.work_class in {"planning"}  # the committed eval's only class
        assert e.decision in ("allow", "allow-with-verify", "deny")


def test_scores_track_known_aggregates():
    """Replay must reproduce the documented frontier/fast/heavy aggregates."""
    by_key = {(e.tier_id, e.work_class): e for e in pb.build_entries(EVAL_DATA)}
    for key, want in EXPECTED.items():
        got = by_key[key]
        assert got.model_label == want["model"]
        assert got.quality_score == pytest.approx(want["score"], abs=1e-9)
        assert got.decision == want["decision"]
        # 4 judge observations per model: 2 PRDs x 2 judges.
        assert got.sample_n == 4
        # Normalized score == eval avg / 25.
        assert got.quality_score == pytest.approx(got.eval_total_avg / 25.0, abs=1e-9)


def test_decisions_match_seed_verdicts():
    """Derived decisions for planning reproduce the T005 hand-authored seed."""
    from anvil_serving.router.profile_store import _SEED_VERDICTS

    by_key = {(e.tier_id, e.work_class): e for e in pb.build_entries(EVAL_DATA)}
    for tier_id, decision in _SEED_VERDICTS["planning"].items():
        assert by_key[(tier_id, "planning")].decision == decision


def test_loads_into_profile_store():
    """The bootstrap populates a real ProfileStore; entries are retrievable."""
    store = pb.bootstrap_store(EVAL_DATA)
    assert isinstance(store, ProfileStore)

    for (tier_id, work_class), want in EXPECTED.items():
        entry = store.entry(tier_id, work_class)
        assert isinstance(entry, ProfileEntry)
        assert entry.decision == want["decision"]
        assert store.score(tier_id, work_class) == pytest.approx(want["score"], abs=1e-9)
        assert store.decision(tier_id, work_class) == want["decision"]
        assert entry.sample_n == 4


def test_store_round_trips_through_disk(tmp_path):
    """A written profile.json loads back into an equivalent ProfileStore."""
    out = tmp_path / "profile.json"
    pb.write_profile(EVAL_DATA, out)
    store = pb.load_profile_store(out)
    for (tier_id, work_class), want in EXPECTED.items():
        assert store.decision(tier_id, work_class) == want["decision"]
        assert store.score(tier_id, work_class) == pytest.approx(want["score"], abs=1e-9)


def test_profile_json_is_byte_stable(tmp_path):
    """Two replays produce identical bytes (deterministic, no timestamps)."""
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    assert pb.main(["--replay", str(EVAL_DATA), "--out", str(a)]) == 0
    assert pb.main(["--replay", str(EVAL_DATA), "--out", str(b)]) == 0
    assert a.read_bytes() == b.read_bytes()
    # No wall-clock leakage: the only date is the committed eval's own date.
    text = a.read_text(encoding="utf-8")
    assert "2026-06-28" in text


def test_profile_json_shape(tmp_path):
    """The portable artifact has the documented schema + sorted entries."""
    out = tmp_path / "profile.json"
    pb.write_profile(EVAL_DATA, out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema"] == pb.SCHEMA
    assert doc["mode"] == "replay"
    assert doc["eval_max"] == 25.0
    keys = [(e["tier_id"], e["work_class"]) for e in doc["entries"]]
    assert keys == sorted(keys)
    for e in doc["entries"]:
        assert set(e) >= {
            "tier_id",
            "work_class",
            "model_label",
            "decision",
            "quality_score",
            "sample_n",
            "eval_total_avg",
            "source_evals",
            "last_measured",
        }


def test_work_class_from_eval_dir():
    """Work-class + date are derived from the eval directory name."""
    assert pb.work_class_from_eval_dir("2026-06-28-planning-capability") == (
        "planning",
        "2026-06-28",
    )
    # Longest taxonomy token wins; trailing descriptor stripped.
    assert pb.work_class_from_eval_dir("2026-07-01-multi-file-refactor-capability") == (
        "multi-file-refactor",
        "2026-07-01",
    )
    # No date prefix, unknown slug -> verbatim (deterministic), no date.
    assert pb.work_class_from_eval_dir("review-eval") == ("review", None)


def test_live_path_is_guarded_and_calls_no_tier():
    """run_live must refuse to run from CI (no endpoints / no confirmation)."""
    with pytest.raises(pb.LiveBootstrapNotConfigured):
        pb.run_live()
    with pytest.raises(pb.LiveBootstrapNotConfigured):
        pb.run_live(endpoints={"cloud": "http://example.invalid"})  # no confirm
    with pytest.raises(pb.LiveBootstrapNotConfigured):
        pb.run_live(confirm_calls_real_tiers=True)  # no endpoints

    # The CLI --live branch is also guarded (exit code 2, nothing dialed).
    rc = pb.main(["--live", "--endpoint", "cloud=http://example.invalid"])
    assert rc == 2
