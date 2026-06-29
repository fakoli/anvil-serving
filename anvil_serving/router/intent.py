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

Precedence is deliberate: **declared-preset is checked BEFORE pin**, so if a
config gives a tier an id that collides with a preset name, the *preset* wins
(presets are the primary wire vocabulary; pinning a same-named tier is a config
smell). Do not reorder — checking pins first would make a shadowed preset
unreachable. Preset and tier matching are **case-insensitive** (``parse_model``
lower-cases the wire token, but config preset keys / tier ids are unconstrained),
resolving against the actual-cased config key.

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

    ``work_class`` is the profile-lookup key. It is always a valid
    :data:`~anvil_serving.router.classify.WORK_CLASSES` value for *inferred* and
    *pinned* intents, but may be ``None`` for a **declared custom preset** that
    has no taxonomy mapping in :data:`PRESET_TO_WORK_CLASS` (routing still works:
    it uses ``preset`` / ``candidate_tiers``, which are independent of the
    profile key).
    """

    work_class: Optional[str]
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
    # Contract says str, but resolve() must never raise (AC2): coerce defensively
    # so even a model object whose ``__str__`` raises degrades to "" rather than
    # escaping.
    try:
        token = str(model).strip().lower()
    except Exception:
        return ""
    for prefix in ("anvil/", "anvil:"):
        if token.startswith(prefix):
            token = token[len(prefix):].strip()
            break
    return token


def _safer_tier(config: RouterConfig) -> str:
    """Id of the configured *safer* tier.

    The first ``privacy == "cloud"`` tier (the always-available safe fallback);
    if there is none, the last declared tier. Used as the sole candidate when an
    inferred request is ambiguous. Returns ``""`` if the config has no tiers, so
    a directly-constructed empty-tiers :class:`RouterConfig` does not make
    :func:`resolve` raise (AC2).
    """
    if not config.tiers:
        return ""
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

    # Case-insensitive lookup tables mapping the normalized (lower-cased) token
    # back to the ACTUAL-cased config key, so a mixed-case preset/tier id is
    # reachable. ``parse_model`` already lower-cased ``m``.
    preset_lc = {p.lower(): p for p in config.presets}
    tier_lc = {t.id.lower(): t.id for t in config.tiers}

    if m and m in preset_lc:
        # 1. Declared preset: caller named the routing class directly.
        #    Checked BEFORE pin so a preset shadows a same-named tier id.
        actual_preset = preset_lc[m]
        preset = actual_preset
        # ``None`` for a custom preset outside the taxonomy: routing uses
        # ``preset`` / ``candidate_tiers``; work_class is only the profile key.
        # Keyed on the normalized token so a mixed-case spelling of a standard
        # preset ("Planning") still maps to its taxonomy work class.
        work_class = PRESET_TO_WORK_CLASS.get(m)
        candidate_tiers = _candidate_ids(config, actual_preset)
        source = "declared-preset"
        ambiguous = False
    elif m and m in tier_lc:
        # 2. Pinned: caller named a concrete tier id (override escape hatch).
        actual_tier = tier_lc[m]
        preset = None
        work_class = classify(request).work_class
        candidate_tiers = (actual_tier,)
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
