"""Hermetic tests for the independent Agent-SDK quality grader (flexibility:T004).

Mirrors the discipline of ``test_calibrate.py``: a FAKE judge is injected into
every test, so CI makes ZERO network / LLM / subprocess calls. The one test that
touches the real ``claude`` CLI seam monkeypatches ``subprocess.run`` — it proves
the seam shells out to the Agent-SDK CLI and scrubs ``ANTHROPIC_API_KEY`` WITHOUT
ever spawning a process or hitting ``api.anthropic.com``.

Load-bearing properties proven here:

1. A valid scored-rubric reply -> a normalized ``[0, 1]`` :class:`Grade`
   (``decision`` left ``None``), via the injectable judge seam.
2. NO SELF-VERIFICATION: grading a Claude/cloud tier RAISES before the judge is
   ever called; the guard is unbypassable and also fail-closed on an
   unidentifiable tier.
3. The judge JSON is validated (``total`` == sum of dims; range; completeness).
4. It drops straight into a real :class:`Calibrator` as the ``grader=``.
"""
from __future__ import annotations

import json

import pytest

from anvil_serving.router.calibrate import Calibrator, Grade
from anvil_serving.router.config import Tier
from anvil_serving.router.profile_bootstrap import DIMS, EVAL_MAX
from anvil_serving.router.profile_store import default_profile
from anvil_serving.router import grader_agentsdk as ga
from anvil_serving.router.grader_agentsdk import (
    AgentSDKGrader,
    JudgeProtocolError,
    SelfVerificationError,
)


# ── helpers ───────────────────────────────────────────────────────────────────
def _scored_json(per_dim=4, notes="ok", total=None):
    """A canned, well-formed judge reply: every dim = ``per_dim`` (total = 5*dim)."""
    scores = {d: per_dim for d in DIMS}
    total = sum(scores.values()) if total is None else total
    return json.dumps({"scores": scores, "total": total, "notes": notes})


def _local_tier(tid="fast-local", model="qwen3-32b-nvfp4"):
    return Tier(
        id=tid,
        base_url="http://127.0.0.1:30001/v1",
        dialect="openai",
        context_limit=131072,
        privacy="local",
        tool_support=True,
        auth_env="LOCAL_TOKEN",
        model=model,
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


def _sample(tier_id="fast-local", work_class="review"):
    return {
        "tier_id": tier_id,
        "work_class": work_class,
        "request": {"messages": [{"role": "user", "content": "Plan the auth refactor"}]},
        "response": {"content": "1. T001 ... 2. T002 ..."},
    }


# ── 1. happy path: a local tier is graded to a normalized score ───────────────
def test_grades_local_tier_returns_normalized_score():
    seen = {}

    def judge(prompt):
        seen["prompt"] = prompt
        return _scored_json(per_dim=4)  # 20/25 -> 0.8

    grader = AgentSDKGrader(tiers=[_local_tier()], judge=judge)
    grade = grader(_sample())

    assert isinstance(grade, Grade)
    assert grade.score == pytest.approx(20.0 / EVAL_MAX)  # 0.8
    assert grade.decision is None                          # never flips the deny gate
    assert grade.notes == "ok"
    # The rubric + the response-to-grade actually reached the judge.
    assert "Plan the auth refactor" in seen["prompt"]
    for d in DIMS:
        assert d in seen["prompt"]


def test_score_is_clamped_into_unit_interval():
    grader = AgentSDKGrader(tiers=[_local_tier()], judge=lambda p: _scored_json(per_dim=5))
    assert grader(_sample()).score == pytest.approx(1.0)  # 25/25
    grader0 = AgentSDKGrader(tiers=[_local_tier()], judge=lambda p: _scored_json(per_dim=0))
    assert grader0(_sample()).score == pytest.approx(0.0)  # 0/25


def test_decision_for_convenience_uses_shared_thresholds():
    # 25/25 -> 1.0 -> allow (decision_for_score band).
    grader = AgentSDKGrader(tiers=[_local_tier()], judge=lambda p: _scored_json(per_dim=5))
    assert grader.decision_for(_sample()) == "allow"


# ── 2. NO SELF-VERIFICATION ───────────────────────────────────────────────────
def test_refuses_claude_cloud_tier_before_calling_judge():
    def judge(prompt):  # pragma: no cover - must never run
        raise AssertionError("judge was called on a Claude tier — self-verification!")

    grader = AgentSDKGrader(tiers=[_cloud_claude_tier()], judge=judge)
    with pytest.raises(SelfVerificationError) as exc:
        grader(_sample(tier_id="cloud"))
    assert "claude" in str(exc.value).lower()


def test_refuses_claude_by_model_id_even_if_privacy_local():
    """A tier flagged local but serving a Claude model id is still refused."""
    weird = _local_tier(tid="oops", model="claude-3-5-haiku")
    grader = AgentSDKGrader(tiers=[weird], judge=lambda p: _scored_json())
    with pytest.raises(SelfVerificationError):
        grader(_sample(tier_id="oops"))


def test_refuses_anthropic_dialect_tier():
    tier = Tier(
        id="anthropic-ish",
        base_url="https://example.test",
        dialect="anthropic",
        context_limit=100000,
        privacy="cloud",
        tool_support=True,
        auth_env="SOME_TOKEN",
        model=None,  # no model id, but the anthropic dialect gives it away
    )
    grader = AgentSDKGrader(tiers=[tier], judge=lambda p: _scored_json())
    with pytest.raises(SelfVerificationError):
        grader(_sample(tier_id="anthropic-ish"))


def test_fail_closed_when_tier_family_unknown():
    """An unregistered tier (family unprovable) is refused, not silently graded."""
    def judge(prompt):  # pragma: no cover - must never run
        raise AssertionError("judge called on an unidentifiable tier")

    grader = AgentSDKGrader(tiers=[_local_tier()], judge=judge)  # 'mystery' not registered
    with pytest.raises(SelfVerificationError):
        grader(_sample(tier_id="mystery"))


def test_contradictory_family_label_cannot_wave_claude_through():
    """A tier that LIES about its family (a Claude model id under a non-Claude
    `family` label) is still refused: a positive Claude signal vetoes the explicit
    label, so a mislabeled Claude tier can never reach the Claude judge."""
    def judge(prompt):  # pragma: no cover - must never run
        raise AssertionError("judge called on a mislabeled Claude tier — self-verification!")

    grader = AgentSDKGrader(judge=judge)
    # Inline tier metadata (the only path with a `family` field) that positively
    # lies: declares family 'local' but serves a Claude model.
    sample = {
        **_sample(tier_id="liar"),
        "tier": {"id": "liar", "family": "local", "model": "claude-opus-4"},
    }
    with pytest.raises(SelfVerificationError):
        grader(sample)


def test_independent_non_claude_cloud_tier_is_allowed():
    """A cloud OpenAI/GPT tier is genuinely independent of a Claude judge -> allowed."""
    gpt = Tier(
        id="gpt-cloud",
        base_url="https://api.openai.com/v1",
        dialect="openai",
        context_limit=128000,
        privacy="cloud",
        tool_support=True,
        auth_env="OPENAI_API_KEY",
        model="gpt-4o",
    )
    grader = AgentSDKGrader(tiers=[gpt], judge=lambda p: _scored_json(per_dim=3))
    assert grader(_sample(tier_id="gpt-cloud")).score == pytest.approx(15.0 / EVAL_MAX)


def test_custom_judge_family_refuses_matching_tier():
    """The guard tracks the configured judge family, not a hardcoded 'claude'."""
    gpt = Tier(
        id="gpt-cloud", base_url="https://api.openai.com/v1", dialect="openai",
        context_limit=128000, privacy="cloud", tool_support=True,
        auth_env="OPENAI_API_KEY", model="gpt-4o",
    )
    grader = AgentSDKGrader(tiers=[gpt], judge_family="openai", judge=lambda p: _scored_json())
    with pytest.raises(SelfVerificationError):
        grader(_sample(tier_id="gpt-cloud"))


def test_inline_tier_metadata_is_used_over_registry():
    """A sample carrying its own tier metadata is guarded on that metadata."""
    grader = AgentSDKGrader(judge=lambda p: _scored_json())  # empty registry
    sample = _sample(tier_id="whatever")
    sample["tier"] = {"id": "whatever", "model": "claude-opus-4", "privacy": "cloud"}
    with pytest.raises(SelfVerificationError):
        grader(sample)


# ── 3. judge-reply validation ─────────────────────────────────────────────────
def test_rejects_total_not_equal_to_sum():
    bad = json.dumps({"scores": {d: 4 for d in DIMS}, "total": 25, "notes": "x"})  # 20 != 25
    grader = AgentSDKGrader(tiers=[_local_tier()], judge=lambda p: bad)
    with pytest.raises(JudgeProtocolError) as exc:
        grader(_sample())
    assert "sum" in str(exc.value).lower()


def test_rejects_out_of_range_dimension():
    bad = json.dumps({"scores": {**{d: 4 for d in DIMS}, DIMS[0]: 7}, "total": 23})
    grader = AgentSDKGrader(tiers=[_local_tier()], judge=lambda p: bad)
    with pytest.raises(JudgeProtocolError):
        grader(_sample())


def test_rejects_missing_dimension():
    scores = {d: 4 for d in DIMS}
    del scores[DIMS[0]]
    bad = json.dumps({"scores": scores, "total": sum(scores.values())})
    grader = AgentSDKGrader(tiers=[_local_tier()], judge=lambda p: bad)
    with pytest.raises(JudgeProtocolError):
        grader(_sample())


def test_rejects_unknown_dimension():
    scores = {**{d: 4 for d in DIMS}, "bogus_dim": 4}
    bad = json.dumps({"scores": scores, "total": 20})
    grader = AgentSDKGrader(tiers=[_local_tier()], judge=lambda p: bad)
    with pytest.raises(JudgeProtocolError):
        grader(_sample())


def test_rejects_non_json_reply():
    grader = AgentSDKGrader(tiers=[_local_tier()], judge=lambda p: "sorry, I can't do that")
    with pytest.raises(JudgeProtocolError):
        grader(_sample())


def test_parses_json_wrapped_in_code_fence():
    fenced = "```json\n" + _scored_json(per_dim=4) + "\n```"
    grader = AgentSDKGrader(tiers=[_local_tier()], judge=lambda p: fenced)
    assert grader(_sample()).score == pytest.approx(0.8)


def test_parses_json_embedded_in_prose():
    noisy = "Here is my assessment:\n" + _scored_json(per_dim=2) + "\nHope that helps!"
    grader = AgentSDKGrader(tiers=[_local_tier()], judge=lambda p: noisy)
    assert grader(_sample()).score == pytest.approx(10.0 / EVAL_MAX)


# ── 4. drops into the real Calibrator as the injected grader ──────────────────
def test_wires_into_calibrator_and_folds_grade():
    store = default_profile()
    before = store.entry("fast-local", "review")

    grader = AgentSDKGrader(tiers=[_local_tier("fast-local")], judge=lambda p: _scored_json(per_dim=4))
    cal = Calibrator(
        store, grader=grader, enabled=True, sample_rate=1.0,
        now=lambda: "2026-07-03T00:00:00Z",
    )
    try:
        cal.observe(
            {"messages": [{"role": "user", "content": "hi"}]},
            {"content": "a plan"},
            "review",
            "fast-local",
        )
        assert cal.drain(timeout=5)
        assert cal.errors == 0
    finally:
        cal.close()

    after = store.entry("fast-local", "review")
    assert after.sample_n == before.sample_n + 1
    assert after.quality_score != before.quality_score  # the 0.8 grade folded in
    assert after.last_measured == "2026-07-03T00:00:00Z"


def test_calibrator_swallows_self_verification_on_cloud_tier():
    """Wired into the Calibrator, a refusal is swallowed+counted, never crashes serving."""
    store = default_profile()
    grader = AgentSDKGrader(tiers=[_cloud_claude_tier("cloud")], judge=lambda p: _scored_json())
    cal = Calibrator(store, grader=grader, enabled=True, sample_rate=1.0)
    try:
        cal.observe({"m": "x"}, {"c": "y"}, "review", "cloud")
        assert cal.drain(timeout=5)
        assert cal.errors == 1
        assert cal.last_error == "SelfVerificationError"
    finally:
        cal.close()


# ── the real Agent-SDK seam: CLI subprocess, key-scrub, NO raw API ────────────
def test_real_seam_uses_claude_cli_and_scrubs_api_key(monkeypatch):
    """Proves the DEFAULT judge shells out to the `claude` CLI (Agent-SDK path)
    and scrubs ANTHROPIC_API_KEY — WITHOUT spawning a real process."""
    import subprocess

    captured = {}

    class _FakeProc:
        returncode = 0
        stdout = json.dumps({"result": _scored_json(per_dim=4)})
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs.get("env")
        captured["input"] = kwargs.get("input")
        return _FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SHOULD-BE-SCRUBBED")

    text = ga._claude_cli_judge("grade this please")

    assert captured["argv"][0] == "claude"          # the Agent-SDK CLI, not raw API
    assert "-p" in captured["argv"]                  # headless print mode
    assert "--bare" not in captured["argv"]          # ADR-0007: never --bare
    assert "ANTHROPIC_API_KEY" not in captured["env"]  # scrubbed: subscription OAuth only
    assert "grade this please" in captured["input"]
    # The seam unwraps the CLI's {"result": ...} envelope back to the scored JSON.
    assert json.loads(text)["total"] == 20


def test_grader_module_imports_no_anthropic_sdk():
    """Importing the grader must not pull in the raw `anthropic` SDK."""
    import importlib
    import sys

    sys.modules.pop("anthropic", None)
    importlib.reload(ga)
    assert "anthropic" not in sys.modules
