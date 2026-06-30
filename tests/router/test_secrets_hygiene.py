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


# ── review fix 4: don't over-redact usage metrics (token COUNTS, not secrets) ──
def test_usage_metrics_survive_sanitize():
    rec = {
        "usage": {"input_tokens": 12, "output_tokens": 34, "total_tokens": 46},
        "token_count": 46,
        "tokens": 46,
        "completion_tokens": 34,
        "prompt_tokens": 12,
    }
    out = sanitize(rec, calibration=False)
    assert out["usage"]["input_tokens"] == 12
    assert out["usage"]["output_tokens"] == 34
    assert out["usage"]["total_tokens"] == 46
    assert out["token_count"] == 46
    assert out["tokens"] == 46
    assert out["completion_tokens"] == 34
    assert out["prompt_tokens"] == 12


# ── review fix 4: but real *_token secret fields ARE still masked ──────────────
def test_token_secret_fields_are_masked():
    rec = {
        "api_token": "abc123-api-token-secretvalue",
        "access_token": "xyz789-access-token-secretvalue",
        "auth_token": "auth-token-secretvalue-000",
        "token": "bare-token-secretvalue-111",
    }
    out = sanitize(rec, calibration=False)
    blob = _flatten(out)
    for v in rec.values():
        assert v not in blob          # no secret token value survives
    for field in rec:
        assert MASK in out[field]     # each masked


# ── review fix 5: broaden keylike scrubbing (GitHub fine-grained PAT, Google) ──
def test_github_and_google_keys_scrubbed_from_free_text():
    gh = "github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyzABCDEF"
    goog = "AIzaSyD-1234567890abcdefghijklmnopqrstuv"
    rec = {"note": f"used {gh} and {goog} in a deploy script"}
    out = sanitize(rec, calibration=False)
    blob = _flatten(out)
    assert gh not in blob
    assert goog not in blob
    assert "deploy script" in out["note"]  # surrounding prose retained


def test_github_short_prefixes_scrubbed():
    for tok in ("gho_AAAA1111BBBB2222", "ghs_CCCC3333DDDD4444"):
        out = sanitize({"note": f"token={tok} end"}, calibration=False)
        assert tok not in _flatten(out)


# ── review fix 6: IGNORECASE so a lowercase `bearer …` is scrubbed ────────────
def test_lowercase_bearer_scrubbed():
    rec = {"log": "curl -H 'authorization: bearer abc123def456ghi789'"}
    out = sanitize(rec, calibration=False)
    assert "bearer abc123def456ghi789" not in _flatten(out)
    assert MASK in out["log"]


# ── Gap 1 + 2: camelCase / glued creds and cookie / private_key ───────────────
_CAMEL_CRED_VALUES = {
    "apiToken": "tok-api-1234-ABCDEF-SECRET",
    "accessToken": "tok-access-5678-GHIJKL-SECRET",
    "sessionToken": "tok-session-ABCDEF-SECRET",
    "refreshToken": "tok-refresh-MNOPQR-SECRET",
    "clientSecret": "secret-client-9012-STUVWX",
    "secretKey": "key-secret-3456-YZABCD",
    "privateKey": "key-private-7890-EFGHIJ",
    "apiKey": "key-api-1234-KLMNOP",
    "authToken": "tok-auth-5678-QRSTUV",
    "bearerToken": "tok-bearer-9012-WXYZAB",
    "cookie": "session=abc123xyz; Path=/; HttpOnly",
    "private_key": "super-private-key-value-789-SECRET",
}


def test_camelcase_and_gap2_names_masked_calibration_off():
    rec = dict(_CAMEL_CRED_VALUES)
    out = sanitize(rec, calibration=False)
    blob = _flatten(out)
    for field, val in _CAMEL_CRED_VALUES.items():
        assert val not in blob, f"raw value of {field!r} survived (calibration=False)"
        assert MASK in out[field], f"{field!r} not masked (calibration=False)"


def test_camelcase_and_gap2_names_masked_calibration_on():
    """Secrets are always masked, even in calibration mode."""
    rec = dict(_CAMEL_CRED_VALUES)
    out = sanitize(rec, calibration=True)
    blob = _flatten(out)
    for field, val in _CAMEL_CRED_VALUES.items():
        assert val not in blob, f"raw value of {field!r} survived (calibration=True)"
        assert MASK in out[field], f"{field!r} not masked (calibration=True)"


# ── Gap 2: PEM private key body scrubbed from free text ───────────────────────
_PEM_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEA0Z3VS5JJcds3xHn/ygWep4YBTlOoGFEBMsql4WkPkNmgBXVL\n"
    "V7B5GQCIMZf4Q2yKMH3BIi9yRDaDulvBsZPVBVdvZ3VbOjbdWBIHGJWPRCcFmpXM\n"
    "ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF12345678\n"
    "-----END RSA PRIVATE KEY-----"
)


def test_pem_private_key_scrubbed_from_free_text():
    rec = {"log": f"Loaded key: {_PEM_KEY} done"}
    out = sanitize(rec, calibration=False)
    blob = _flatten(out)
    assert "BEGIN RSA PRIVATE KEY" not in blob, "PEM BEGIN marker survived"
    assert "MIIEowIBAAKCAQEA" not in blob, "PEM base64 body survived"
    assert MASK in out["log"], "MASK not inserted in log field"
    assert "done" in out["log"], "surrounding prose after PEM was lost"


def test_pem_private_key_scrubbed_calibration_on():
    """PEM blocks are free-text secrets: scrubbed in both modes."""
    rec = {"note": f"key={_PEM_KEY}"}
    out = sanitize(rec, calibration=True)
    blob = _flatten(out)
    assert "BEGIN RSA PRIVATE KEY" not in blob
    assert "MIIEowIBAAKCAQEA" not in blob


# ── Gap 3: query / data / text / request / response treated as prompt fields ──
_PROMPT_BODY_2 = "Write a function that computes the Fibonacci sequence efficiently."


def test_query_data_text_request_response_redacted_calibration_off():
    rec = {
        "query": _PROMPT_BODY_2,
        "data": _PROMPT_BODY_2,
        "text": _PROMPT_BODY_2,
        "request": _PROMPT_BODY_2,
        "response": _PROMPT_BODY_2,
    }
    out = sanitize(rec, calibration=False)
    blob = _flatten(out)
    assert _PROMPT_BODY_2 not in blob, "prompt body survived under one of the new field names"
    for field in rec:
        assert "REDACTED_PROMPT" in out[field], f"{field!r} not fingerprinted (calibration=False)"


def test_query_data_text_request_response_kept_calibration_on():
    """Calibration mode keeps prompt bodies but still strips embedded keys."""
    embedded_key = "sk-ant-xyz123SECRETVALUE-ABCDEF"
    rec = {
        "query": f"{_PROMPT_BODY_2} key={embedded_key}",
        "data": _PROMPT_BODY_2,
        "text": _PROMPT_BODY_2,
        "request": {"prompt": _PROMPT_BODY_2},
        "response": COMPLETION,
    }
    out = sanitize(rec, calibration=True)
    blob = _flatten(out)
    # Prompt bodies are retained for calibration scoring.
    assert _PROMPT_BODY_2 in blob, "prompt body should be retained with calibration=True"
    assert COMPLETION in blob, "completion should be retained with calibration=True"
    # But the embedded key must still be scrubbed.
    assert embedded_key not in blob, "embedded API key survived calibration=True"


# ── Gap 1+2+3 regression: metric fields still survive (no over-redaction) ─────
def test_new_patterns_do_not_over_redact_metrics():
    """The metric guard must hold even with the new secret / prompt patterns."""
    rec = {
        "usage": {"input_tokens": 12, "output_tokens": 34, "total_tokens": 46},
        "token_count": 46,
        "completion_tokens": 34,
        "prompt_tokens": 12,
    }
    out = sanitize(rec, calibration=False)
    assert out["usage"]["input_tokens"] == 12
    assert out["usage"]["output_tokens"] == 34
    assert out["usage"]["total_tokens"] == 46
    assert out["token_count"] == 46
    assert out["completion_tokens"] == 34
    assert out["prompt_tokens"] == 12
