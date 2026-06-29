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

# Field NAMES (case-insensitive) whose value is a secret and is always masked,
# in every mode. Substring match so ``x-api-key`` / ``anthropic_api_key`` hit.
_SECRET_NAME_RE = re.compile(
    r"(api[_-]?key|secret|token|authorization|x-api-key|password|bearer|credential)",
    re.IGNORECASE,
)

# Field NAMES (case-insensitive) that carry a full prompt / completion body.
# Masked to a fingerprint unless calibration capture is opted in.
_PROMPT_NAME_RE = re.compile(
    r"(prompt|messages|completion|content|system|input|output|response_text|user_text)",
    re.IGNORECASE,
)

# Secret-SHAPED substrings to scrub out of any free-text value as defense in
# depth — even a value under a benign field name (e.g. a stack trace or a copied
# curl line) must not leak a key. Mirrors the markers the config layer rejects.
_KEYLIKE_RE = re.compile(
    r"""(
        sk-[A-Za-z0-9._-]{6,}          # OpenAI / Anthropic style
      | ghp_[A-Za-z0-9]{6,}            # GitHub PAT
      | xox[baprs]-[A-Za-z0-9-]{6,}    # Slack
      | (?:AKIA|ASIA)[0-9A-Z]{16}      # AWS access key id
      | Bearer\s+[A-Za-z0-9._\-]{6,}   # Authorization: Bearer <...>
    )""",
    re.VERBOSE,
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

    1. A field whose NAME looks like a secret (``api_key``, ``authorization``,
       ``x-api-key`` …) -> value masked via :func:`redact_key`. **Always**, in
       both modes.
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
            out[key] = _sanitize_value(
                v,
                calibration,
                extra,
                _name_secret=bool(_SECRET_NAME_RE.search(key)),
                _name_prompt=bool(_PROMPT_NAME_RE.search(key)),
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
