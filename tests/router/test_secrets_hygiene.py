"""Secrets-hygiene redaction for router logs/metrics (T006).

Proves PRD acceptance criterion 2:
  AC2 - a representative decision-log / metrics record carrying an API key and a
        full prompt body, run through sanitize() with calibration OFF, contains
        NEITHER the key NOR the full prompt body. Calibration ON behaves as
        designed (keeps prompts; still redacts keys).

Hermetic, stdlib-only, no network.
"""

from __future__ import annotations

import json

from anvil_serving.router.secrets import (
    MASK,
    redact_key,
    redact_prompt,
    sanitize,
)

API_KEY = "sk-ant-test-SECRET-0123456789abcdef"
PROMPT_BODY = (
    "Refactor the authentication module and remove the hardcoded password "
    "hunter2 from the connection string before shipping to production."
)
COMPLETION = "Here is the refactored module with the secret removed."


def _record() -> dict:
    """A representative decision-log / metrics record (nested, mixed fields)."""
    return {
        "ts": "2026-06-29T12:00:00Z",
        "tier": "cloud",
        "model": "claude-x",
        "decision": {"preset": "planning", "chosen_tier": "cloud", "verified": True},
        "latency_ms": 1234,
        # secret-named fields (must always be masked)
        "api_key": API_KEY,
        "headers": {"x-api-key": API_KEY, "Authorization": f"Bearer {API_KEY}"},
        # prompt/completion bodies (masked unless calibration)
        "prompt": PROMPT_BODY,
        "messages": [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": PROMPT_BODY},
        ],
        "completion": COMPLETION,
        # a benign free-text field that happens to embed a key (defense in depth)
        "note": f"curl -H 'authorization: Bearer {API_KEY}' failed",
    }


def _flatten(obj) -> str:
    """Serialize the whole record so we can assert absence anywhere within it."""
    return json.dumps(obj, default=str)


# ── AC2: calibration OFF removes the key AND the full prompt body ──────────────
def test_sanitize_removes_key_everywhere_calibration_off():
    out = sanitize(_record(), calibration=False)
    blob = _flatten(out)
    assert API_KEY not in blob               # no API key anywhere in the output
    assert "Bearer " + API_KEY not in blob   # nor inside any header/free text


def test_sanitize_removes_full_prompt_body_calibration_off():
    out = sanitize(_record(), calibration=False)
    blob = _flatten(out)
    assert PROMPT_BODY not in blob           # the full prompt body is gone
    assert "hunter2" not in blob             # incl. the secret embedded in it
    assert COMPLETION not in blob            # completion body redacted too


def test_sanitize_keeps_useful_nonsecret_fields():
    out = sanitize(_record(), calibration=False)
    # Redaction must not nuke the audit value of the record.
    assert out["tier"] == "cloud"
    assert out["latency_ms"] == 1234
    assert out["decision"]["chosen_tier"] == "cloud"
    # Prompt fields become a content-free fingerprint, not just dropped.
    assert "REDACTED_PROMPT" in out["prompt"]
    assert "chars=" in out["prompt"]


def test_secret_named_fields_are_masked():
    out = sanitize(_record(), calibration=False)
    assert MASK in out["api_key"]
    assert API_KEY not in out["api_key"]
    assert API_KEY not in _flatten(out["headers"])


# ── AC2: calibration ON keeps prompts but STILL redacts keys ──────────────────
def test_calibration_on_keeps_prompt_but_redacts_key():
    out = sanitize(_record(), calibration=True)
    blob = _flatten(out)
    # Fuller capture: the prompt + completion text are retained for scoring.
    assert PROMPT_BODY in blob
    assert COMPLETION in blob
    # But secrets are NEVER un-redacted, even in calibration mode.
    assert out["api_key"] != API_KEY
    assert MASK in out["api_key"]
    assert API_KEY not in _flatten(out["headers"])


def test_calibration_on_still_scrubs_key_embedded_in_prompt():
    # If a key leaks INTO the prompt text, calibration capture must still scrub it
    # from the free-text fields (the key, not the surrounding prose).
    rec = {"prompt": f"my key is {API_KEY} keep it safe"}
    out = sanitize(rec, calibration=True)
    assert API_KEY not in _flatten(out)
    assert "keep it safe" in out["prompt"]  # surrounding prompt text retained


# ── known-literal scrubbing (e.g. the resolved key passed in explicitly) ───────
def test_explicit_secret_literal_scrubbed_anywhere():
    weird = "weird-format-token-NOTMATCHINGREGEX-12345"
    rec = {"misc": f"value={weird} trailing", "nested": {"deep": weird}}
    out = sanitize(rec, calibration=False, secrets=[weird])
    blob = _flatten(out)
    assert weird not in blob
    assert "trailing" in out["misc"]


# ── unit-level helpers ────────────────────────────────────────────────────────
def test_redact_key_keeps_only_a_short_hint():
    masked = redact_key(API_KEY)
    assert API_KEY not in masked
    assert masked.startswith("sk-a")  # short prefix hint only
    assert MASK in masked


def test_redact_key_empty_is_bare_mask():
    assert redact_key("") == MASK
    assert redact_key(None) == MASK


def test_redact_prompt_fingerprint_is_content_free():
    fp = redact_prompt(PROMPT_BODY, calibration=False)
    assert PROMPT_BODY not in fp
    assert "hunter2" not in fp
    assert f"chars={len(PROMPT_BODY)}" in fp
    assert "sha256=" in fp


def test_redact_prompt_calibration_returns_text():
    assert redact_prompt(PROMPT_BODY, calibration=True) == PROMPT_BODY


def test_sanitize_does_not_mutate_input():
    rec = _record()
    before = _flatten(rec)
    sanitize(rec, calibration=False)
    assert _flatten(rec) == before  # original untouched
