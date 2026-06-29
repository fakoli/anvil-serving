"""Intent resolution: presets, Tier-0 classifier, override (harness-router:T003).

Turns a caller's request into an :class:`Intent` — the work class plus the
ordered pool of candidate tier ids a later ranking stage will pick from. Three
sources feed it, in precedence order:

1. **declared-preset** — the ``model`` field names a configured preset
   (``"planning"``, ``"quick-edit"``, ...). Optionally namespaced ``anvil/``.
2. **pinned** — the ``model`` field names a concrete tier id. This is the
   override escape hatch: skip inference, route straight to that one tier.
3. **inferred** — anything else (unknown or empty model): hand off to the
   Tier-0 :func:`~anvil_serving.router.classify.classify` heuristics.

Two safety properties this module guarantees:

* :func:`resolve` **never raises** (AC2): an unknown or empty model still yields
  a valid :class:`Intent` with a classifier-assigned work class.
* **Ambiguous -> safer tier** (AC3): when inference is not confident (or the
  inferred preset is absent from the config) the candidate pool collapses to the
  single *safer tier* — the configured cloud endpoint, which is always available
  — and that fact is recorded in the decision log.

Stdlib-only; mirrors the frozen-dataclass style of ``config.py`` / ``internal.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Optional

from .classify import classify
from .config import RouterConfig
from .internal import InternalRequest

# Canonical taxonomy bridges. Presets are the caller-facing vocabulary; work
# classes are the classifier-facing vocabulary. These maps translate between
# them so a declared preset and an inferred work class land in the same space.
PRESET_TO_WORK_CLASS = {
    "chat": "chat",
    "quick-edit": "bounded-edit",
    "review": "review",
    "planning": "planning",
    "long-context": "long-context",
}

WORK_CLASS_TO_PRESET = {
    "chat": "chat",
    "bounded-edit": "quick-edit",
    "multi-file-refactor": "review",
    "planning": "planning",
    "review": "review",
    "long-context": "long-context",
}


@dataclass(frozen=True)
class Intent:
    """The resolved routing intent for one request.

    ``decision`` is the audit record (raw model string, normalization, source,
    chosen pool, safer-tier id). It is **excluded from equality and hashing**
    (``compare=False``) on purpose: the two equivalent inputs ``"planning"`` and
    ``"anvil/planning"`` must compare EQUAL (AC1) even though their decision logs
    differ in the raw model string. It is wrapped read-only via ``MappingProxyType``.
    """

    work_class: str
    preset: Optional[str]
    source: str  # "declared-preset" | "inferred" | "pinned"
    candidate_tiers: tuple[str, ...]
    ambiguous: bool
    decision: Mapping[str, Any] = field(compare=False, hash=False)


def parse_model(model: Optional[str]) -> str:
    """Normalize a wire ``model`` token.

    Lower-cases, strips surrounding whitespace, and removes a leading ``anvil/``
    or ``anvil:`` namespace prefix. ``None``/empty normalize to ``""``.
    """
    if not model:
        return ""
    if not isinstance(model, str):  # contract says str, but never raise (AC2)
        model = str(model)
    token = model.strip().lower()
    for prefix in ("anvil/", "anvil:"):
        if token.startswith(prefix):
            token = token[len(prefix):].strip()
            break
    return token


def _safer_tier(config: RouterConfig) -> str:
    """Id of the configured *safer* tier.

    The first ``privacy == "cloud"`` tier (the always-available safe fallback);
    if there is none, the last declared tier. Used as the sole candidate when an
    inferred request is ambiguous.
    """
    for t in config.tiers:
        if t.privacy == "cloud":
            return t.id
    return config.tiers[-1].id


def _candidate_ids(config: RouterConfig, preset: str) -> tuple[str, ...]:
    """Ordered candidate tier ids for ``preset``; ``()`` if it cannot resolve."""
    try:
        return tuple(t.id for t in config.candidates(preset))
    except Exception:
        return ()


def resolve(request: InternalRequest, config: RouterConfig) -> Intent:
    """Resolve ``request`` against ``config`` into an :class:`Intent`.

    Never raises (AC2). Ambiguous inferred requests collapse to the safer tier
    (AC3). ``"planning"`` and ``"anvil/planning"`` resolve equal (AC1).
    """
    m = parse_model(getattr(request, "model", None))
    safer = _safer_tier(config)

    if m and m in config.presets:
        # 1. Declared preset: caller named the routing class directly.
        preset = m
        work_class = PRESET_TO_WORK_CLASS.get(m, m)
        candidate_tiers = _candidate_ids(config, m)
        source = "declared-preset"
        ambiguous = False
    elif m and any(t.id == m for t in config.tiers):
        # 2. Pinned: caller named a concrete tier id (override escape hatch).
        preset = None
        work_class = classify(request).work_class
        candidate_tiers = (m,)
        source = "pinned"
        ambiguous = False
    else:
        # 3. Inferred: unknown/empty model -> Tier-0 classifier.
        c = classify(request)
        work_class = c.work_class
        source = "inferred"
        ambiguous = not c.confident
        preset = WORK_CLASS_TO_PRESET.get(work_class)
        if ambiguous or preset not in config.presets:
            candidate_tiers = (safer,)
        else:
            candidate_tiers = _candidate_ids(config, preset)

    decision = MappingProxyType({
        "model_in": getattr(request, "model", None),
        "normalized": m,
        "source": source,
        "preset": preset,
        "work_class": work_class,
        "ambiguous": ambiguous,
        "candidate_tiers": candidate_tiers,
        "safer_tier": safer,
    })
    return Intent(
        work_class=work_class,
        preset=preset,
        source=source,
        candidate_tiers=candidate_tiers,
        ambiguous=ambiguous,
        decision=decision,
    )
