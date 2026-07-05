"""Tests for the "chat-fast" (voice pipeline) low-latency work class (flexibility:T018).

Covers all three router layers additively touched to introduce it:

* ``classify.py`` -- "chat-fast" joins the WORK_CLASSES taxonomy, and an
  explicit wire ``modality``/``voice`` marker (the voice pipeline's LLM stage
  always sends one -- see ``anvil_serving/voice/stages/llm.py``) classifies a
  request as "chat-fast" REGARDLESS of stated-intent keywords, since a
  real-time voice turn wants the fast tier even if its content mentions
  "plan"/"review"/etc.
* ``intent.py`` -- "chat-fast" is a declared preset (the wire ``model`` field
  a caller can name directly), mapped to the "chat-fast" work class.
* ``policy.py`` -- "chat-fast" is exempted from the anti-thrash residency
  deferral (LATENCY_SENSITIVE_WORK_CLASSES), so it is always routed
  fast-tier-first even when a DIFFERENT local tier currently holds the
  multiplexer's one resident slot -- the opposite tradeoff every other work
  class makes (see tests/router/test_residency.py for the general case).

Existing classify/policy/intent behavior for every OTHER work class must be
completely unaffected -- this file adds coverage, it does not replace
tests/router/test_classify.py, test_intent.py, or test_residency.py, all of
which must keep passing unmodified (the regression guard for this unit).
"""
from __future__ import annotations

import pathlib
import tempfile
from types import MappingProxyType

from anvil_serving.router.classify import WORK_CLASSES, classify
from anvil_serving.router.config import load
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.intent import (
    PRESET_TO_WORK_CLASS,
    WORK_CLASS_TO_PRESET,
    Intent,
    resolve,
)
from anvil_serving.router.policy import LATENCY_SENSITIVE_WORK_CLASSES, route
from anvil_serving.router.profile_store import default_profile


def _req(text="", *, raw=None, model="x"):
    return InternalRequest(
        model=model, messages=[Message("user", text)], raw=raw if raw is not None else {},
    )


# --------------------------------------------------------------------------- #
# classify.py: taxonomy + explicit voice-modality marker
# --------------------------------------------------------------------------- #
def test_chat_fast_is_a_known_work_class():
    assert "chat-fast" in WORK_CLASSES


def test_modality_voice_marker_classifies_chat_fast():
    c = classify(_req("hello, how are you today", raw={"modality": "voice"}))
    assert c.work_class == "chat-fast"
    assert c.confident is True
    assert c.signals["is_voice"] is True


def test_bare_voice_flag_also_classifies_chat_fast():
    c = classify(_req("hello there", raw={"voice": True}))
    assert c.work_class == "chat-fast"
    assert c.confident is True


def test_voice_marker_overrides_stated_intent_keywords():
    # Content-wise this would normally classify as "planning" -- but the
    # explicit voice marker means the turn wants the fast tier regardless.
    c = classify(_req("let's plan the road trip", raw={"modality": "voice"}))
    assert c.work_class == "chat-fast"
    assert c.confident is True


def test_voice_marker_still_loses_to_long_context():
    # Window pressure dominates everything, including the voice marker.
    big = " ".join("token" for _ in range(7000))
    c = classify(_req(big, raw={"modality": "voice"}))
    assert c.work_class == "long-context"


def test_without_marker_short_payload_stays_plain_chat():
    # REGRESSION: a short single-turn payload with no explicit signal must
    # still classify as "chat" (mirrors test_classify.test_neutral_is_ambiguous_chat)
    # -- adding "chat-fast" must not broaden the plain chat default.
    c = classify(_req("hello there"))
    assert c.work_class == "chat"
    assert c.confident is False
    assert c.signals["is_voice"] is False


def test_non_voice_modality_value_is_ignored():
    c = classify(_req("hello there", raw={"modality": "text"}))
    assert c.work_class == "chat"
    assert c.signals["is_voice"] is False


# --------------------------------------------------------------------------- #
# intent.py: "chat-fast" is a declared preset (a short single-turn voice
# payload naming it directly, as the voice LLM stage's request does)
# --------------------------------------------------------------------------- #
_EXAMPLE = pathlib.Path(__file__).resolve().parents[2] / "configs" / "example.toml"
_CONFIG = load(str(_EXAMPLE))


def test_chat_fast_preset_is_declared_in_the_shipped_config():
    assert "chat-fast" in _CONFIG.presets
    assert _CONFIG.presets["chat-fast"][0] == "fast-local"  # fast-tier-first


def test_short_single_turn_payload_declaring_chat_fast_resolves_work_class():
    intent = resolve(_req("hi, quick question", model="chat-fast"), _CONFIG)
    assert intent.work_class == "chat-fast"
    assert intent.source == "declared-preset"
    assert intent.candidate_tiers == _CONFIG.presets["chat-fast"]


def test_preset_work_class_mapping_is_consistent_both_ways():
    assert PRESET_TO_WORK_CLASS["chat-fast"] == "chat-fast"
    assert WORK_CLASS_TO_PRESET["chat-fast"] == "chat-fast"


# --------------------------------------------------------------------------- #
# policy.py: fast-tier-first bypasses the anti-thrash residency deferral
# --------------------------------------------------------------------------- #
_RES_TOML = """\
[router]
mapping_version = "test.chat-fast"
metered_cloud = ["chat-fast", "bounded-edit"]

[[router.tiers]]
id            = "fast-local"
base_url      = "http://127.0.0.1:30001/v1"
dialect       = "openai"
context_limit = 32768
privacy       = "local"
tool_support  = true
auth_env      = "ANVIL_FAST_LOCAL_KEY"

[[router.tiers]]
id            = "heavy-local"
base_url      = "http://127.0.0.1:30000/v1"
dialect       = "openai"
context_limit = 131072
privacy       = "local"
tool_support  = true
auth_env      = "ANVIL_HEAVY_LOCAL_KEY"

[[router.tiers]]
id            = "cloud"
base_url      = "https://api.anthropic.com"
dialect       = "anthropic"
context_limit = 200000
privacy       = "cloud"
tool_support  = true
auth_env      = "ANTHROPIC_API_KEY"
"""
_res_path = pathlib.Path(tempfile.mkdtemp()) / "chat_fast_residency.toml"
_res_path.write_text(_RES_TOML, encoding="utf-8")
_RES_CONFIG = load(str(_res_path))
_PROFILE = default_profile()


def _intent(work_class, candidate_tiers):
    return Intent(
        work_class=work_class, preset=None, source="test",
        candidate_tiers=tuple(candidate_tiers), ambiguous=False,
        decision=MappingProxyType({}),
    )


def test_chat_fast_is_latency_sensitive():
    assert "chat-fast" in LATENCY_SENSITIVE_WORK_CLASSES


def test_chat_fast_stays_fast_tier_first_even_when_heavy_is_resident():
    intent = _intent("chat-fast", ("fast-local", "heavy-local", "cloud"))
    dec = route(intent, _RES_CONFIG, _PROFILE, residency="heavy-local")
    assert dec.tiers == ("fast-local", "heavy-local", "cloud")
    assert dec.notes["residency_deferred"] == ()
    assert dec.notes["latency_sensitive_bypass"] is True


def test_bounded_edit_is_still_deferred_under_the_same_residency():
    # Contrast/regression: a NON-latency-sensitive class keeps the existing
    # anti-thrash behavior (test_residency.py's general-case property) --
    # this addition must not change routing for any other work class.
    intent = _intent("bounded-edit", ("fast-local", "heavy-local", "cloud"))
    dec = route(intent, _RES_CONFIG, _PROFILE, residency="heavy-local")
    assert dec.tiers[-1] == "fast-local"
    assert "fast-local" in dec.notes["residency_deferred"]
    assert dec.notes["latency_sensitive_bypass"] is False


def test_chat_fast_with_no_residency_keeps_cost_order():
    intent = _intent("chat-fast", ("fast-local", "heavy-local", "cloud"))
    dec = route(intent, _RES_CONFIG, _PROFILE, residency=None)
    assert dec.tiers == ("fast-local", "heavy-local", "cloud")


# --------------------------------------------------------------------------- #
# profile_store.py: chat-fast is fully trusted (not merely allow-with-verify)
# --------------------------------------------------------------------------- #
def test_chat_fast_seed_profile_allows_every_tier():
    profile = default_profile()
    assert profile.decision("fast-local", "chat-fast") == "allow"
    assert profile.decision("heavy-local", "chat-fast") == "allow"
    assert profile.decision("cloud", "chat-fast", is_cloud=True) == "allow"
