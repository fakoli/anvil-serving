"""Secrets-hygiene primitives for the router's logs and metrics (T006).

The decision log itself is a later task (T010); here we supply — and prove — the
redaction primitives it (and the metrics path) MUST run records through:

* :func:`redact_key`    — mask a secret literal, keeping only a short hint.
* :func:`redact_prompt` — replace a full prompt body with a content-free
  fingerprint (length + short SHA-256) so records stay correlatable without
  persisting the text.
* :func:`sanitize`      — walk a log/metrics record (nested dicts/lists) and
  apply both: secret-named fields are masked, prompt-named fields are
  fingerprinted, and any secret-SHAPED substring left in free text is scrubbed.

Policy (the gate): **default is redact.** A ``calibration`` opt-in allows fuller
capture — it keeps prompt bodies (calibration needs the text to score quality) —
but it **never** un-redacts API keys. Secrets are masked in both modes; only
prompt capture is gated. ``redact=True`` is the safe default; you must pass
``calibration=True`` explicitly to retain prompts.

Stdlib-only. No secret value is ever logged, hashed-then-printed, or returned in
the clear by any function here.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Mapping

#: What a redacted secret looks like in output.
MASK = "[REDACTED]"

# Usage/metric field names that must NEVER be redacted — they carry no secret
# and are the whole point of a metrics record. Checked BEFORE the secret/prompt
# rules so a substring like ``token`` (in ``output_tokens``) or ``output`` can't
# clobber the count. Plural ``*_tokens`` / ``token_count`` only; the singular
# ``token`` field (a real secret) is intentionally NOT here.
_METRIC_NAME_RE = re.compile(
    r"(?i)("
    r"(^|_)tokens$|token_count$|^token_usage$"
    # Correlation / metric / limit suffixes: request_id, response_ms,
    # input_cost_per_mtok, context_limit, content_type carry no prompt text —
    # destroying them defeats the module's own "records stay correlatable"
    # promise. Checked BEFORE the prompt rule, exactly like the token counts.
    r"|_id$|_ms$|_cost(_per_\w+)?$|_limit$|_type$"
    r")"
)

# Field NAMES whose value is a secret and is always masked, in every mode.
# Word-boundaried (``(?:^|[^a-z0-9]) … (?![a-z0-9])``) so a secret-ish fragment
# can't catch a metric: ``token`` matches a bare ``token`` field but NOT
# ``output_tokens`` / ``token_count``; ``access_token`` matches but not
# ``access_tokens``. Substring-anchored so ``x-api-key`` / ``anthropic_api_key``
# still hit.
_SECRET_NAME_RE = re.compile(
    r"""(?ix)
    (?:^|[^a-z0-9])
    (?:
        # ── snake / kebab / plain ────────────────────────────────────────────
        api[_-]?key
      | apikey
      | secret
      | password
      | passwd
      | authorization
      | (?:access|auth|refresh|id|bearer|session|api|client)[_-]token
      | token
      | bearer
      | credentials?
      | cookie                  # HTTP Cookie / Set-Cookie headers
      | private[_-]key          # private_key / private-key
      | signing[_-]key          # signing_key / signing-key
      | session                 # bare session field

        # ── camelCase / glued forms ──────────────────────────────────────────
        # The sensitive keyword appears as a camelCase component; the spelled-out
        # suffix (Key, Token, Secret) serves as the camelCase word boundary so the
        # post-boundary lookahead (?![a-z0-9]) passes at the end of the token.
      | (?:api|access|auth|refresh|client|bearer|session)Token
      | (?:client|api)Secret
      | (?:private|signing|api|secret|auth)Key
    )
    (?![a-z0-9])
    """,
)

# Field NAMES (case-insensitive) that carry a full prompt / completion body.
# Masked to a fingerprint unless calibration capture is opted in. (The metric
# guard above runs first, so ``prompt_tokens`` / ``completion_tokens`` are NOT
# caught here despite the ``prompt`` / ``completion`` substrings.)
# Component-boundaried like ``_SECRET_NAME_RE`` (not bare substrings): the old
# unanchored form caught ``request_id`` (via ``request``), ``metadata`` (via
# ``data``), ``context_limit`` (via ``text``!) and friends, silently destroying
# IDs and numeric metadata in every sanitized record.
_PROMPT_NAME_RE = re.compile(
    r"""(?ix)
    (?:^|[^a-z0-9])
    (?:prompt|messages?|completions?|content|system|input|output
      |response_text|user_text|query|data|text|request|response)
    (?![a-z0-9])
    """,
)

# Secret-SHAPED substrings to scrub out of any free-text value as defense in
# depth — even a value under a benign field name (e.g. a stack trace or a copied
# curl line) must not leak a key. Known prefixes only (no generic high-entropy
# matcher, to avoid false positives), but IGNORECASE so a lowercase ``bearer …``
# in a logged header/curl line is still caught.
_KEYLIKE_RE = re.compile(
    r"""(
        sk-[A-Za-z0-9._-]{6,}              # OpenAI / Anthropic style (sk-, sk-ant-)
      | sk_live_[A-Za-z0-9]{6,}            # Stripe live secret key
      | sk_test_[A-Za-z0-9]{6,}            # Stripe test secret key
      | rk_live_[A-Za-z0-9]{6,}            # Stripe restricted key
      | github_pat_[A-Za-z0-9_]{6,}        # GitHub fine-grained PAT
      | gh[oprsu]_[A-Za-z0-9]{6,}          # GitHub tokens (gho_/ghp_/ghr_/ghs_/ghu_)
      | xox[baprs]-[A-Za-z0-9-]{6,}        # Slack
      | AIza[A-Za-z0-9_\-]{10,}            # Google API key
      | (?:AKIA|ASIA)[0-9A-Z]{16}          # AWS access key id
      | Bearer\s+[A-Za-z0-9._\-]{6,}       # Authorization: Bearer <...>
      | -----BEGIN\s+(?:\w+\s+)*PRIVATE\s+KEY-----[\s\S]+?-----END\s+(?:\w+\s+)*PRIVATE\s+KEY-----  # PEM private key block
    )""",
    re.VERBOSE | re.IGNORECASE,
)


def redact_key(value: Any, *, keep: int = 4) -> str:
    """Mask a secret literal, keeping only the first ``keep`` chars as a hint.

    ``redact_key("sk-test-abc123")`` -> ``"sk-t…[REDACTED]"``. Empty/None
    collapses to the bare mask. Never returns the full secret.
    """
    s = "" if value is None else str(value)
    if not s:
        return MASK
    if keep > 0 and len(s) > keep:
        return f"{s[:keep]}…{MASK}"
    return MASK


def _fingerprint(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"[REDACTED_PROMPT chars={len(text)} sha256={digest}]"


def redact_prompt(value: Any, *, calibration: bool = False) -> Any:
    """Redact a full prompt/completion body.

    With ``calibration`` off (default) the body is replaced by a content-free
    fingerprint (length + short SHA-256) — correlatable, but the text is gone.
    With ``calibration`` on the value is returned unchanged for quality capture
    (keys inside are still scrubbed by :func:`sanitize`).
    """
    if calibration:
        return value
    if isinstance(value, str):
        return _fingerprint(value)
    # Non-string bodies (e.g. a ``messages`` list) are canonicalized first so the
    # fingerprint is stable and no fragment of the content survives.
    try:
        canonical = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        canonical = repr(value)
    return _fingerprint(canonical)


def _scrub_text(text: str, extra_secrets: Iterable[str]) -> str:
    """Remove secret-shaped substrings + any known-literal secrets from text."""
    out = _KEYLIKE_RE.sub(MASK, text)
    for lit in extra_secrets:
        if lit:
            out = out.replace(lit, MASK)
    return out


def sanitize(
    record: Mapping[str, Any],
    *,
    calibration: bool = False,
    secrets: Iterable[str] = (),
) -> Dict[str, Any]:
    """Return a redacted deep copy of a log/metrics ``record``.

    Rules, applied recursively to nested dicts/lists:

    0. A field whose NAME is a usage metric (``output_tokens``, ``input_tokens``,
       ``token_count`` …) is NEVER name-redacted — metrics are preserved (its
       value still recurses so nested strings are scrubbed).
    1. A field whose NAME looks like a secret (``api_key``, ``authorization``,
       ``x-api-key``, ``access_token`` …) -> value masked via :func:`redact_key`.
       **Always**, in both modes.
    2. A field whose NAME looks like a prompt body (``prompt``, ``messages`` …)
       -> :func:`redact_prompt` (fingerprint unless ``calibration``).
    3. Every surviving free-text value has secret-shaped substrings (and any
       literal in ``secrets``) scrubbed.

    ``secrets`` is an optional set of known-literal key values (e.g. the
    resolved API key) to scrub anywhere they appear, in any mode. The input
    record is never mutated.
    """
    extra = tuple(s for s in secrets if s)
    return _sanitize_value(record, calibration, extra, _name_secret=False, _name_prompt=False)


def _sanitize_value(
    value: Any,
    calibration: bool,
    extra: Iterable[str],
    *,
    _name_secret: bool,
    _name_prompt: bool,
) -> Any:
    # Field-name verdicts (computed by the parent dict) win over content rules.
    # A secret-named field is masked in EVERY mode.
    if _name_secret:
        return redact_key(value)
    # A prompt-named field is fingerprinted only when calibration is OFF. With
    # calibration ON we keep the body, but fall through to normal recursion so
    # any secret-shaped substring inside it is still scrubbed (defense in depth).
    if _name_prompt and not calibration:
        return redact_prompt(value, calibration=False)

    if isinstance(value, Mapping):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            # A metric/usage field is never name-masked (don't destroy the
            # counts); its value still recurses so any nested string is scrubbed.
            is_metric = bool(_METRIC_NAME_RE.search(key))
            out[key] = _sanitize_value(
                v,
                calibration,
                extra,
                _name_secret=(not is_metric) and bool(_SECRET_NAME_RE.search(key)),
                _name_prompt=(not is_metric) and bool(_PROMPT_NAME_RE.search(key)),
            )
        return out
    if isinstance(value, (list, tuple)):
        items: List[Any] = [
            _sanitize_value(v, calibration, extra, _name_secret=False, _name_prompt=False)
            for v in value
        ]
        return type(value)(items) if isinstance(value, tuple) else items
    if isinstance(value, str):
        return _scrub_text(value, extra)
    return value
