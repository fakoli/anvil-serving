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
EVAL_DATA = REPO_ROOT / "tests" / "fixtures" / "eval-data"

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
    # A canonical taxonomy token still resolves via the startswith() path even
    # without a date prefix ("review-eval" -> "review-" -> "review").
    assert pb.work_class_from_eval_dir("review-eval") == ("review", None)
    # Verbatim fallback: a slug matching NO work-class token has its trailing
    # descriptor (_SLUG_SUFFIXES) stripped and is returned as-is, with the date.
    assert pb.work_class_from_eval_dir("2026-06-28-something-capability") == (
        "something",
        "2026-06-28",
    )


def test_live_path_is_guarded_and_calls_no_tier():
    """run_live must refuse to run from CI (no endpoints / no confirmation)."""
    with pytest.raises(pb.LiveBootstrapNotConfigured):
        pb.run_live()
    with pytest.raises(pb.LiveBootstrapNotConfigured):
        pb.run_live(endpoints={"cloud": "http://example.invalid"})  # no confirm
    with pytest.raises(pb.LiveBootstrapNotConfigured):
        pb.run_live(confirm_calls_real_tiers=True)  # no endpoints

    # Even with the full confirmation trio, the body is NOT implemented (T016) —
    # it raises rather than dialing a tier.
    with pytest.raises(NotImplementedError):
        pb.run_live(
            endpoints={"cloud": "http://example.invalid"},
            confirm_calls_real_tiers=True,
        )

    # The CLI --live branch is also guarded (exit code 2, nothing dialed).
    rc = pb.main(["--live", "--endpoint", "cloud=http://example.invalid"])
    assert rc == 2


def test_live_cli_full_trio_exits_cleanly_no_network(monkeypatch):
    """The full --live trio exits 2 (clean message), never a crash, no socket."""

    # Hard guarantee: any attempt to open a socket during this path is a failure.
    import socket

    def _no_network(*args, **kwargs):  # pragma: no cover - must never fire
        raise AssertionError("live CLI path attempted a network connection")

    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)

    rc = pb.main(
        [
            "--live",
            "--endpoint",
            "cloud=http://example.invalid",
            "--i-understand-this-calls-real-tiers",
        ]
    )
    assert rc == 2  # clean exit, not a traceback / exit 1


# --- T001: v2 schema (fingerprint + reasoning) and merge-over-seed --------------


def test_profile_is_v2_with_fingerprint_and_reasoning(tmp_path):
    """The portable artifact is v2 and every row carries fingerprint + reasoning.

    A replay row grades pre-committed outputs (no live serve), so both are None.
    """
    out = tmp_path / "profile.json"
    pb.write_profile(EVAL_DATA, out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema"] == pb.SCHEMA == "anvil-serving.router.profile_bootstrap/v2"
    for e in doc["entries"]:
        assert "fingerprint" in e and "reasoning" in e
        assert e["fingerprint"] is None and e["reasoning"] is None


def test_v2_round_trips_fingerprint_and_reasoning():
    """A live-shaped entry round-trips fingerprint + reasoning through disk, and
    store_from_profile loads the fingerprint into the ProfileEntry."""
    entry = pb.BootstrapEntry(
        tier_id="heavy-local",
        work_class="chat",
        model_label="heavy",
        decision="allow",
        quality_score=0.82,
        sample_n=3,
        eval_total_avg=20.5,
        eval_max=25.0,
        source_evals=("live",),
        last_measured="2026-07-03",
        fingerprint="fp-abc123",
        reasoning={"enable_thinking": False},
    )
    doc = {"schema": pb.SCHEMA, "mode": "live", "eval_max": 25.0, "entries": [entry.to_dict()]}
    reloaded = json.loads(pb.serialize_profile(doc))
    row = reloaded["entries"][0]
    assert row["fingerprint"] == "fp-abc123"
    assert row["reasoning"] == {"enable_thinking": False}
    # store_from_profile loads the fingerprint onto the store entry.
    got = pb.store_from_profile(doc).entry("heavy-local", "chat")
    assert got is not None
    assert got.fingerprint == "fp-abc123"
    assert got.decision == "allow"
    assert got.last_measured == "2026-07-03"


def test_load_merges_measured_over_seed():
    """A planning-only replay keeps SEED verdicts for unmeasured classes and
    overlays the measured planning rows over their seeds."""
    from anvil_serving.router.profile_store import _SEED_VERDICTS

    store = pb.bootstrap_store(EVAL_DATA)  # merge_over_seed=True by default
    # An unmeasured class keeps its seed verdict, not the fail-closed default:
    # review/heavy-local seeds to "allow" (the store default would be
    # "allow-with-verify"), so a stored "allow" entry proves the seed survived.
    assert _SEED_VERDICTS["review"]["heavy-local"] == "allow"
    review = store.entry("heavy-local", "review")
    assert review is not None and review.decision == "allow"
    # The measured planning row OVERLAYS its seed: measured sample_n (4), not seed (1).
    planning = store.entry("heavy-local", "planning")
    assert planning is not None and planning.decision == "deny"
    assert planning.sample_n == 4


def test_merge_over_seed_false_is_measured_only():
    """merge_over_seed=False restores the raw measured-only table (pre-T001)."""
    profile = pb.build_profile(EVAL_DATA)
    store = pb.store_from_profile(profile, merge_over_seed=False)
    assert store.entry("heavy-local", "planning") is not None  # measured
    assert store.entry("heavy-local", "review") is None  # unmeasured, no seed


def test_v1_profile_still_loads():
    """A pre-T001 v1 document still loads (rows carry no fingerprint/reasoning)."""
    doc = {
        "schema": pb.SCHEMA_V1,
        "mode": "replay",
        "eval_max": 25.0,
        "entries": [
            {
                "tier_id": "fast-local",
                "work_class": "chat",
                "model_label": "fast",
                "decision": "allow",
                "quality_score": 0.7,
                "sample_n": 2,
                "eval_total_avg": 17.5,
                "eval_max": 25.0,
                "last_measured": None,
                "source_evals": [],
            }
        ],
    }
    got = pb.store_from_profile(doc).entry("fast-local", "chat")
    assert got is not None and got.decision == "allow" and got.fingerprint is None


def test_unknown_schema_still_rejected():
    """A wholly unknown schema tag still fails loudly."""
    with pytest.raises(ValueError, match="schema mismatch"):
        pb.store_from_profile({"schema": "bogus/v9", "entries": []})
