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
from anvil_serving.router.calibrate import Grade
from anvil_serving.router.config import Tier
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

    # Even past the endpoints+confirm guard, run_live still refuses (cleanly) when
    # it has no LOCAL tiers / prompts to measure — it raises rather than dialing a
    # tier. (The full measuring path is exercised hermetically below with fakes.)
    with pytest.raises(pb.LiveBootstrapNotConfigured):
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


# --- T005: run_live — guarded offline calibration batch (LOCAL tiers only) ------
#
# Every test here is HERMETIC: run_live's real seams (the REAL backend, and the
# grader that shells to the `claude` CLI) are INJECTED as fakes, so NO test makes a
# network / subprocess / LLM call. The default (uninjected) path is the real one,
# exercised only in production.

# Confirmation trio required to pass run_live's guard in every measuring test.
_CONFIRM = {"endpoints": {"fast-local": "http://127.0.0.1:30001/v1"},
            "confirm_calls_real_tiers": True}
_FIXED_NOW = "2026-07-03T00:00:00Z"


def _fast_local_tier(tid="fast-local", model="qwen3-32b-nvfp4", extra_body=None):
    """A LOCAL tier carrying thinking-off `extra_body` (the reasoning provenance)."""
    from types import MappingProxyType

    return Tier(
        id=tid,
        base_url="http://127.0.0.1:30001/v1",
        dialect="openai",
        context_limit=131072,
        privacy="local",
        tool_support=True,
        auth_env="LOCAL_TOKEN",
        model=model,
        extra_body=MappingProxyType(dict(extra_body)) if extra_body is not None else None,
    )


def _cloud_claude_tier(tid="cloud"):
    return Tier(
        id=tid,
        base_url="https://api.anthropic.com",
        dialect="anthropic",
        context_limit=200000,
        privacy="cloud",
        tool_support=True,
        auth_env="ANTHROPIC_API_KEY",
        model="claude-opus-4-20250514",
    )


class _FakeBackend:
    """A canned in-process backend: records the request it saw, yields fixed text."""

    def __init__(self, output="1. T001 plan ... 2. T002 plan ..."):
        self.output = output
        self.seen = []

    def generate(self, request):
        self.seen.append(request)
        # Yield in two deltas to prove run_live joins the stream.
        yield self.output[: len(self.output) // 2]
        yield self.output[len(self.output) // 2:]


def _fake_judge(score_per_dim=4, notes="ok"):
    """A fake Agent-SDK judge: returns a well-formed scored-rubric JSON reply."""
    calls = []

    def judge(prompt):
        calls.append(prompt)
        scores = {d: score_per_dim for d in pb.DIMS}
        return json.dumps({"scores": scores, "total": sum(scores.values()), "notes": notes})

    judge.calls = calls
    return judge


def _no_network(monkeypatch):
    """Fail hard if any socket is opened during a supposedly-hermetic run_live."""
    import socket

    def boom(*a, **k):  # pragma: no cover - must never fire
        raise AssertionError("run_live attempted a network connection")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)


def test_run_live_measures_local_tier_and_writes_v2_candidate(tmp_path, monkeypatch):
    """AC1/AC2: run_live measures a LOCAL tier through its (injected) real backend,
    grades it via a default AgentSDKGrader built around a FAKE judge, and writes a
    v2 candidate profile.json with fingerprint + last_measured set on the row."""
    _no_network(monkeypatch)
    from anvil_serving.router.fingerprint import serve_fingerprint

    tier = _fast_local_tier(extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    backends = {}

    def backend_factory(t):
        b = _FakeBackend()
        backends[t.id] = b
        return b

    judge = _fake_judge(score_per_dim=4)  # 20/25 -> 0.8
    out = tmp_path / "candidate.json"

    store = pb.run_live(
        tiers=[tier],
        prompts={"planning": ["Plan the auth refactor"]},
        out_path=out,
        backend_factory=backend_factory,
        judge=judge,          # default AgentSDKGrader built around this fake judge
        now=lambda: _FIXED_NOW,
        **_CONFIRM,
    )

    # The real backend was actually driven (the tier's request reached it).
    assert "fast-local" in backends and backends["fast-local"].seen
    # The judge saw the generated output (proves generate -> grade wiring).
    assert judge.calls and "Plan the auth refactor" in judge.calls[0]

    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema"] == pb.SCHEMA == "anvil-serving.router.profile_bootstrap/v2"
    assert doc["mode"] == "live"
    rows = {(e["tier_id"], e["work_class"]): e for e in doc["entries"]}
    assert set(rows) == {("fast-local", "planning")}
    row = rows[("fast-local", "planning")]
    # AC1: fingerprint + last_measured set on the measured local row.
    assert row["fingerprint"] == serve_fingerprint(tier)
    assert row["last_measured"] == _FIXED_NOW
    # decision is EXPLICIT via decision_for_score(score); 0.8 -> allow-with-verify.
    assert row["quality_score"] == pytest.approx(0.8)
    assert row["decision"] == pb.decision_for_score(0.8) == "allow-with-verify"
    # reasoning provenance captured the tier's thinking-off extra_body.
    assert row["reasoning"] == {"chat_template_kwargs": {"enable_thinking": False}}

    # The returned store is routable and carries the measured row (merged over seed).
    assert isinstance(store, ProfileStore)
    entry = store.entry("fast-local", "planning")
    assert entry is not None and entry.decision == "allow-with-verify"
    assert entry.fingerprint == serve_fingerprint(tier)


def test_run_live_refuses_cloud_tier_never_grades_it(tmp_path, monkeypatch):
    """AC2: a cloud/Claude tier is structurally REFUSED — filtered out, never graded
    (no self-verification), while the local tier alongside it is measured."""
    _no_network(monkeypatch)
    graded_tiers = []

    def spy_grader(sample):
        graded_tiers.append(sample["tier_id"])
        return Grade(score=0.9)

    out = tmp_path / "candidate.json"
    store = pb.run_live(
        tiers=[_cloud_claude_tier("cloud"), _fast_local_tier("fast-local")],
        prompts={"planning": ["Plan it"]},
        out_path=out,
        backend_factory=lambda t: _FakeBackend(),
        grader=spy_grader,
        now=lambda: _FIXED_NOW,
        **_CONFIRM,
    )

    # The cloud tier was never handed to the grader; only the local tier was.
    assert graded_tiers == ["fast-local"]
    doc = json.loads(out.read_text(encoding="utf-8"))
    tiers_in_doc = {e["tier_id"] for e in doc["entries"]}
    assert tiers_in_doc == {"fast-local"}  # no cloud row
    assert store.entry("cloud", "planning") is None or \
        store.entry("cloud", "planning").last_measured is None  # cloud unmeasured


def test_run_live_defense_in_depth_mislabeled_claude_is_refused(tmp_path, monkeypatch):
    """AC2 (defense-in-depth): a tier flagged privacy=local but serving a CLAUDE
    model slips the privacy filter, reaches the REAL grader, and is REFUSED
    (SelfVerificationError) — surfaced + skipped cleanly, the judge never called."""
    _no_network(monkeypatch)
    # privacy="local" (so the privacy filter keeps it) but model is Claude.
    liar = _fast_local_tier(tid="liar", model="claude-3-5-haiku")
    judge = _fake_judge()  # a real AgentSDKGrader is built around this; must NOT be called

    out = tmp_path / "candidate.json"
    store = pb.run_live(
        tiers=[liar],
        prompts={"planning": ["Plan it"]},
        out_path=out,
        backend_factory=lambda t: _FakeBackend(),
        judge=judge,
        now=lambda: _FIXED_NOW,
        **_CONFIRM,
    )

    assert judge.calls == []  # the judge never graded the Claude-family tier
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["entries"] == []  # nothing measured; no crash
    assert isinstance(store, ProfileStore)


def test_run_live_averages_multiple_prompts_and_folds_via_record_grade(monkeypatch):
    """Multiple prompts for one class fold into a running mean via record_grade;
    sample_n counts the observations and the score is their average."""
    _no_network(monkeypatch)
    scores = iter([1.0, 0.0])  # two prompts -> mean 0.5

    def grader(sample):
        return Grade(score=next(scores))

    store = pb.run_live(
        tiers=[_fast_local_tier()],
        prompts={"planning": ["p1", "p2"]},
        backend_factory=lambda t: _FakeBackend(),
        grader=grader,
        now=lambda: _FIXED_NOW,
        **_CONFIRM,
    )
    entry = store.entry("fast-local", "planning")
    assert entry is not None
    assert entry.sample_n == 2
    assert entry.quality_score == pytest.approx(0.5)


def test_run_live_reads_committed_prompts_when_not_injected(tmp_path, monkeypatch):
    """When `prompts` is omitted, run_live reads the COMMITTED fixture prompts under
    eval_data_root (hermetic file read) and measures the derived work-class."""
    _no_network(monkeypatch)
    out = tmp_path / "candidate.json"
    store = pb.run_live(
        tiers=[_fast_local_tier()],
        eval_data_root=EVAL_DATA,   # committed prompts: 2026-06-28-planning-capability
        out_path=out,
        backend_factory=lambda t: _FakeBackend(),
        grader=lambda s: Grade(score=0.7),
        now=lambda: _FIXED_NOW,
        **_CONFIRM,
    )
    entry = store.entry("fast-local", "planning")
    assert entry is not None and entry.quality_score == pytest.approx(0.7)
    # Two committed planning prompts were read + graded.
    assert entry.sample_n == 2


def test_load_live_prompts_reads_committed_fixtures():
    """The committed prompt loader returns the planning prompt set (no network)."""
    prompts = pb.load_live_prompts(EVAL_DATA)
    assert set(prompts) == {"planning"}
    assert len(prompts["planning"]) == 2
    assert all(isinstance(p, str) and p.strip() for p in prompts["planning"])


def test_run_live_backend_factory_receives_real_tier_with_extra_body(monkeypatch):
    """AC2: run_live hands the REAL Tier (carrying extra_body) to the backend
    builder, so the default path applies extra_body byte-identically to prod."""
    _no_network(monkeypatch)
    seen_tiers = []
    tier = _fast_local_tier(extra_body={"chat_template_kwargs": {"enable_thinking": False}})

    def backend_factory(t):
        seen_tiers.append(t)
        return _FakeBackend()

    pb.run_live(
        tiers=[tier],
        prompts={"planning": ["p"]},
        backend_factory=backend_factory,
        grader=lambda s: Grade(score=0.6),
        now=lambda: _FIXED_NOW,
        **_CONFIRM,
    )
    assert seen_tiers == [tier]  # the exact Tier object, extra_body intact
    assert seen_tiers[0].extra_body["chat_template_kwargs"] == {"enable_thinking": False}


def test_run_live_does_not_touch_live_routing_state(monkeypatch):
    """AC3: run_live builds/returns a FRESH candidate store and writes a file; it
    never mutates a shared/seed ProfileStore (nothing is auto-promoted)."""
    _no_network(monkeypatch)
    from anvil_serving.router.profile_store import default_profile

    live_seed = default_profile()
    before = live_seed.entry("fast-local", "planning")
    before_snapshot = (before.decision, before.quality_score, before.sample_n)

    candidate = pb.run_live(
        tiers=[_fast_local_tier()],
        prompts={"planning": ["p"]},
        backend_factory=lambda t: _FakeBackend(),
        grader=lambda s: Grade(score=0.95),  # would be "allow" — a big change
        now=lambda: _FIXED_NOW,
        **_CONFIRM,
    )
    # The candidate reflects the measurement...
    assert candidate.entry("fast-local", "planning").decision == "allow"
    # ...but the untouched live/seed store is byte-for-byte unchanged.
    after = live_seed.entry("fast-local", "planning")
    assert (after.decision, after.quality_score, after.sample_n) == before_snapshot
    assert candidate is not live_seed


def test_run_live_out_path_is_byte_stable_and_v2(tmp_path, monkeypatch):
    """The written candidate is deterministic v2 JSON and round-trips through the
    store loader (fingerprint carried onto the ProfileEntry)."""
    _no_network(monkeypatch)
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    kwargs = dict(
        tiers=[_fast_local_tier()],
        prompts={"planning": ["p"]},
        backend_factory=lambda t: _FakeBackend(),
        grader=lambda s: Grade(score=0.82),
        now=lambda: _FIXED_NOW,
        **_CONFIRM,
    )
    pb.run_live(out_path=a, **kwargs)
    pb.run_live(out_path=b, **kwargs)
    assert a.read_bytes() == b.read_bytes()
    reloaded = pb.load_profile_store(a)
    entry = reloaded.entry("fast-local", "planning")
    assert entry is not None and entry.fingerprint is not None
