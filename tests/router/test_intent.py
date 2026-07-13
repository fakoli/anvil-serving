"""Tests for intent resolution: presets, classifier, override (harness-router:T003).

Proves the three acceptance criteria against ``configs/example-with-cloud.toml``
(the opt-in cloud config) because these tests exercise cloud intent routing — safer
tier = cloud, planning → cloud, ambiguous → cloud, etc.  The shipped *default*
config (``example.toml``) is local-only (T001 / ADR-0001); see test_config.py for
tests that assert the local-only default topology.

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
    PRESETS,
    Intent,
    parse_model,
    resolve,
)
from anvil_serving.router.internal import InternalRequest, Message

# CWD-independent: example-with-cloud.toml at <repo>/configs/; this file is at
# <repo>/tests/router/test_intent.py (parents[2] == repo root).
# These tests exercise cloud intent-resolution logic, so they use the opt-in cloud
# config.  The local-only default (example.toml) is tested in test_config.py (T001).
EXAMPLE = pathlib.Path(__file__).resolve().parents[2] / "configs" / "example-with-cloud.toml"
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


def test_ac1_every_preset_bare_and_prefixed_resolve_equal():
    """AC1 generalized (T013 criterion 1): for EVERY canonical preset, the bare
    wire form and the ``anvil/<preset>`` form resolve to the SAME Intent — the
    front door accepts both wire forms. Iterating ``PRESETS`` (the canonical
    registry) also guards against a preset being added to the enum but not wired
    into the example config."""
    for p in PRESETS:
        assert p.id in CONFIG.presets, f"{p.id!r} missing from example.toml presets"
        bare = resolve(_req(p.id), CONFIG)
        prefixed = resolve(_req(f"anvil/{p.id}"), CONFIG)
        # decision is compare=False, so equality ignores the differing raw input.
        assert bare == prefixed, p.id
        assert bare.preset == prefixed.preset == p.id, p.id
        assert bare.source == prefixed.source == "declared-preset", p.id
        assert bare.candidate_tiers == prefixed.candidate_tiers, p.id
        # the audit record still preserves the two distinct raw wire strings.
        assert bare.decision["model_in"] == p.id
        assert prefixed.decision["model_in"] == f"anvil/{p.id}"
        assert bare.decision["normalized"] == prefixed.decision["normalized"] == p.id


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


# ── ocr preset (gpu-reservations:T011) ───────────────────────────────────────
def test_ocr_preset_resolves_declared_with_ocr_work_class():
    # "ocr" is a declared-preset-only work class: the classifier never infers
    # it (no Tier-0 keywords), but naming the preset routes to the configured
    # OCR pool with the taxonomy work class as its profile key.
    intent = resolve(_req("ocr"), CONFIG)
    assert intent.source == "declared-preset"
    assert intent.preset == "ocr"
    assert intent.work_class == "ocr"
    assert intent.work_class in classify_mod.WORK_CLASSES
    assert intent.candidate_tiers == CONFIG.presets["ocr"]


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
