"""Drift guard for the Tier-0 keyword taxonomy (follow-up to the T014 review).

The keyword VOCABULARY lives in ONE canonical data file,
``anvil_serving/router/tier0_keywords.json``, and is mirrored byte-for-byte into
the OpenClaw plugin (``plugins/openclaw-anvil-intent-router/tier0_keywords.json``)
because the standalone-distributed plugin cannot read the Python package once
installed into ``~/.openclaw``. ``classify.py`` (router) and ``classify.mjs``
(plugin) both BUILD their keyword regexes from their respective copies.

These tests fail loudly if:
  (a) the plugin's bundled JSON drifts from the canonical JSON — the core
      anti-drift check (the two silently diverged once; found in T014 review);
  (b) ``classify.py``'s effective ``_KEYWORD_PHRASES`` stops matching the
      canonical JSON (or its hardcoded fallback drifts from it);
  (c) representative prompts stop classifying to the work-class whose mapped
      plugin preset both sides agree on.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import anvil_serving.router.classify as classify_mod
from anvil_serving.router.classify import classify
from anvil_serving.router.internal import InternalRequest, Message

# repo root = tests/router/ -> tests/ -> <root>
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANONICAL = _REPO_ROOT / "anvil_serving" / "router" / "tier0_keywords.json"
_PLUGIN = (
    _REPO_ROOT / "plugins" / "openclaw-anvil-intent-router" / "tier0_keywords.json"
)


def _req(text: str) -> InternalRequest:
    return InternalRequest(model="x", messages=[Message("user", text)], raw={})


def _work_classes(data: dict) -> dict:
    """Just the work-class -> phrases mapping (drop "_"-prefixed metadata)."""
    return {k: v for k, v in data.items() if not k.startswith("_")}


# ── (a) core anti-drift check: the two JSON copies are content-identical ──────
def test_canonical_and_plugin_json_exist():
    assert _CANONICAL.is_file(), f"missing canonical taxonomy: {_CANONICAL}"
    assert _PLUGIN.is_file(), f"missing bundled plugin taxonomy: {_PLUGIN}"


def test_plugin_json_is_byte_identical_to_canonical():
    assert _PLUGIN.read_bytes() == _CANONICAL.read_bytes(), (
        "plugins/openclaw-anvil-intent-router/tier0_keywords.json has DRIFTED "
        "from anvil_serving/router/tier0_keywords.json — re-copy the canonical "
        "file into the plugin verbatim (the two MUST be byte-identical)."
    )


def test_plugin_json_content_identical_to_canonical():
    canon = json.loads(_CANONICAL.read_text(encoding="utf-8"))
    plugin = json.loads(_PLUGIN.read_text(encoding="utf-8"))
    # same keys, in the same ORDER (priority is encoded by key order).
    assert list(canon.keys()) == list(plugin.keys())
    # same ordered phrase list for every work class (list eq is order-sensitive).
    assert _work_classes(canon) == _work_classes(plugin)


# ── (b) classify.py's effective phrases == the canonical JSON ─────────────────
def test_classify_py_phrases_match_canonical_json():
    canon_wc = _work_classes(json.loads(_CANONICAL.read_text(encoding="utf-8")))
    effective = {wc: list(phrases) for wc, phrases in classify_mod._KEYWORD_PHRASES}
    assert effective == canon_wc
    # priority order preserved too.
    assert [wc for wc, _ in classify_mod._KEYWORD_PHRASES] == list(canon_wc.keys())


def test_classify_py_loads_from_file_not_just_fallback():
    # Direct load must succeed and equal the canonical JSON — proves the file
    # ships and parses (the hardcoded fallback is only a safety net).
    canon_wc = _work_classes(json.loads(_CANONICAL.read_text(encoding="utf-8")))
    loaded = {wc: list(p) for wc, p in classify_mod._load_keyword_phrases()}
    assert loaded == canon_wc


def test_classify_py_fallback_mirrors_canonical():
    # The hardcoded fallback (used only if the JSON is unreadable) must itself
    # stay a verbatim mirror, so a fallback can never silently change behavior.
    canon_wc = _work_classes(json.loads(_CANONICAL.read_text(encoding="utf-8")))
    fallback = {wc: list(p) for wc, p in classify_mod._FALLBACK_KEYWORD_PHRASES}
    assert fallback == canon_wc
    assert [wc for wc, _ in classify_mod._FALLBACK_KEYWORD_PHRASES] == list(
        canon_wc.keys()
    )


# ── (c) cross-side consistency on representative prompts ──────────────────────
# The plugin re-maps ROUTER work-classes onto its preset enum; this mirror MUST
# match WORK_CLASS_TO_PRESET in classify.mjs.
_WORK_CLASS_TO_PRESET = {
    "review": "review",
    "planning": "planning",
    "multi-file-refactor": "review",
    "bounded-edit": "quick-edit",
}


@pytest.mark.parametrize(
    "prompt, work_class, plugin_preset",
    [
        ("Help me plan the migration", "planning", "planning"),
        ("We need solid plans for the release", "planning", "planning"),
        ("Help me with planning the sprint", "planning", "planning"),
        ("Walk me through it step by step", "planning", "planning"),
        ("Migrate the database to Postgres", "multi-file-refactor", "review"),
        ("Patch the null deref", "bounded-edit", "quick-edit"),
        ("Please review this diff", "review", "review"),
    ],
)
def test_python_classifier_agrees_with_plugin_preset(prompt, work_class, plugin_preset):
    c = classify(_req(prompt))
    assert c.work_class == work_class, prompt
    assert _WORK_CLASS_TO_PRESET[c.work_class] == plugin_preset, prompt
