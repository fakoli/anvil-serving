"""Tests for intent resolution: presets, classifier, override (harness-router:T003).

Proves the three acceptance criteria against the real ``configs/example.toml``:
  AC1 - "planning" and "anvil/planning" resolve to the SAME intent.
  AC2 - an unknown/empty model is classified, never errors.
  AC3 - ambiguous inputs resolve to the configured safer (cloud) tier, logged.
Plus the pin override escape hatch and prefix/case normalization.
"""
from __future__ import annotations

import pathlib
from types import MappingProxyType

from anvil_serving.router import classify as classify_mod
from anvil_serving.router.config import RouterConfig, Tier, load
from anvil_serving.router.intent import (
    PRESET_TO_WORK_CLASS,
    Intent,
    parse_model,
    resolve,
)
from anvil_serving.router.internal import InternalRequest, Message

# CWD-independent: example.toml at <repo>/configs/example.toml; this file is at
# <repo>/tests/router/test_intent.py (parents[2] == repo root).
EXAMPLE = pathlib.Path(__file__).resolve().parents[2] / "configs" / "example.toml"
CONFIG = load(str(EXAMPLE))


def _req(model, text="hello there", *, system=None, raw=None):
    return InternalRequest(
        model=model,
        messages=[Message("user", text)],
        system=system,
        raw=raw if raw is not None else {},
    )


# ── AC1: preset + anvil-namespaced preset resolve equal ──────────────────────
def test_ac1_planning_alias_equal():
    a = resolve(_req("planning"), CONFIG)
    b = resolve(_req("anvil/planning"), CONFIG)
    assert a == b  # decision is excluded from equality
    for intent in (a, b):
        assert intent.preset == "planning"
        assert intent.work_class == "planning"
        assert intent.candidate_tiers == ("cloud",)
        assert intent.source == "declared-preset"
        assert intent.ambiguous is False


def test_ac1_decision_logs_differ_even_though_equal():
    a = resolve(_req("planning"), CONFIG)
    b = resolve(_req("anvil/planning"), CONFIG)
    # Equal intents, but the audit record preserves the distinct raw inputs.
    assert a == b
    assert a.decision["model_in"] == "planning"
    assert b.decision["model_in"] == "anvil/planning"
    assert a.decision["normalized"] == b.decision["normalized"] == "planning"


# ── AC2: unknown/empty model never errors, always classified ─────────────────
def test_ac2_empty_model_inferred():
    intent = resolve(_req(""), CONFIG)
    assert isinstance(intent, Intent)
    assert intent.work_class in classify_mod.WORK_CLASSES
    assert intent.source == "inferred"


def test_ac2_unknown_model_inferred():
    intent = resolve(_req("totally-unknown-xyz"), CONFIG)
    assert isinstance(intent, Intent)
    assert intent.work_class in classify_mod.WORK_CLASSES
    assert intent.source == "inferred"


def test_ac2_none_model_does_not_raise():
    intent = resolve(_req(None), CONFIG)
    assert intent.work_class in classify_mod.WORK_CLASSES
    assert intent.source == "inferred"


# ── AC3: ambiguous -> safer (cloud) tier, recorded ───────────────────────────
def test_ac3_ambiguous_routes_to_safer_tier():
    intent = resolve(_req("", "hello there"), CONFIG)
    assert intent.ambiguous is True
    assert intent.candidate_tiers == ("cloud",)
    assert intent.decision["ambiguous"] is True
    assert intent.decision["safer_tier"] == "cloud"


# ── pin override: model is a concrete tier id ────────────────────────────────
def test_pin_override_to_tier_id():
    intent = resolve(_req("heavy-local"), CONFIG)
    assert intent.source == "pinned"
    assert intent.candidate_tiers == ("heavy-local",)
    assert intent.preset is None
    assert intent.ambiguous is False


# ── prefix / case normalization ──────────────────────────────────────────────
def test_parse_model_normalization():
    assert parse_model("anvil/planning") == "planning"
    assert parse_model("ANVIL/planning") == "planning"
    assert parse_model(" planning ") == "planning"
    assert parse_model("anvil:planning") == "planning"
    assert parse_model(None) == ""
    assert parse_model("") == ""


def test_non_string_model_never_raises():
    # InternalRequest.model is typed str, but resolve must never raise (AC2)
    # even if a caller constructs one with a contract-violating value.
    for bad in (123, ["x"], {"a": 1}):
        assert parse_model(bad) == str(bad).strip().lower()
        intent = resolve(_req(bad), CONFIG)
        assert intent.source == "inferred"


def test_prefix_case_variants_all_resolve_planning():
    for model in ("anvil/planning", "ANVIL/planning", " planning ", "anvil:planning"):
        intent = resolve(_req(model), CONFIG)
        assert intent.preset == "planning", model
        assert intent.source == "declared-preset", model


def _cloud_tier(tid="cloud"):
    """A minimal valid cloud Tier for directly-constructed RouterConfigs."""
    return Tier(
        id=tid,
        base_url="https://api.example/v1",
        dialect="anthropic",
        context_limit=200000,
        privacy="cloud",
        tool_support=True,
        auth_env="ANTHROPIC_API_KEY",
    )


# ── resolve() never raises on adversarial configs / models (AC2) ─────────────
def test_empty_tiers_config_does_not_raise():
    # A directly-constructed empty-tiers config: _safer_tier would IndexError on
    # config.tiers[-1] if it were not guarded. resolve must still not raise.
    cfg = RouterConfig(tiers=(), presets=MappingProxyType({}), mapping_version="v")
    intent = resolve(_req(""), cfg)
    assert isinstance(intent, Intent)
    assert intent.source == "inferred"
    assert intent.decision["safer_tier"] == ""


def test_model_whose_str_raises_does_not_raise():
    class Hostile:
        def __str__(self):
            raise RuntimeError("boom")

    intent = resolve(_req(Hostile()), CONFIG)  # must degrade, never raise
    assert isinstance(intent, Intent)
    assert intent.source == "inferred"
    assert intent.decision["normalized"] == ""


# ── case-insensitive config matching ─────────────────────────────────────────
def test_mixed_case_preset_resolves_declared():
    # Config preset key is "Planning"; parse_model lower-cases the wire token, so
    # both "planning" and "Planning" must reach the declared-preset branch.
    cfg = RouterConfig(
        tiers=(_cloud_tier(),),
        presets=MappingProxyType({"Planning": ("cloud",)}),
        mapping_version="v",
    )
    for caller in ("planning", "Planning", "anvil/PLANNING"):
        intent = resolve(_req(caller), cfg)
        assert intent.source == "declared-preset", caller
        assert intent.preset == "Planning", caller  # actual-cased config key
        assert intent.candidate_tiers == ("cloud",), caller


def test_custom_preset_outside_taxonomy_has_none_work_class():
    # A configured preset with no PRESET_TO_WORK_CLASS mapping resolves as a
    # declared preset but with work_class=None (routing uses preset/tiers).
    assert "yolo" not in PRESET_TO_WORK_CLASS
    cfg = RouterConfig(
        tiers=(_cloud_tier(),),
        presets=MappingProxyType({"yolo": ("cloud",)}),
        mapping_version="v",
    )
    intent = resolve(_req("yolo"), cfg)
    assert intent.source == "declared-preset"
    assert intent.work_class is None
    assert intent.preset == "yolo"
    assert intent.candidate_tiers == ("cloud",)


# ── conflicting-keyword inference is ambiguous -> safer tier (AC3) ────────────
def test_conflicting_keywords_route_to_safer_tier():
    # "review" + "implement" name two classes -> classifier not confident ->
    # ambiguous -> collapse to the safer (cloud) tier.
    intent = resolve(_req("", "review and implement the fix"), CONFIG)
    assert intent.source == "inferred"
    assert intent.ambiguous is True
    assert intent.candidate_tiers == ("cloud",)
    assert intent.decision["safer_tier"] == "cloud"


# ── a confident inferred class expands to its preset pool ────────────────────
def test_confident_inference_uses_preset_pool():
    # A single-class match -> review work class -> review preset -> the pool.
    # (Phrase avoids a second keyword: "design"/"plan" would make it ambiguous.)
    intent = resolve(_req("", "please review this pull request"), CONFIG)
    assert intent.source == "inferred"
    assert intent.ambiguous is False
    assert intent.work_class == "review"
    assert intent.candidate_tiers == ("heavy-local", "cloud")
