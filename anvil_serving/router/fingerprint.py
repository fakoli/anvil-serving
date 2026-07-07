"""Serve-fingerprint + profile-row staleness (harness-router:T016).

The quality profile (:mod:`anvil_serving.router.profile_store`) records, per
``(tier_id, work_class)``, how much routing should TRUST a tier. Those numbers
are only meaningful while the underlying *serve* is unchanged: swap the model,
the endpoint, the dialect, or a quality-affecting serve param and the measured
score is suddenly about a different thing. This module makes that change
observable and converts it into a per-row **staleness** flag the router can use
to distrust an out-of-date row.

Two pieces:

* :func:`serve_fingerprint` — a stable, content-addressed digest over the parts
  of a tier's serve identity that AFFECT OUTPUT QUALITY (and therefore the
  validity of a measured score). Deterministic: identical inputs hash identically
  across processes and runs; it includes ONLY the documented identity fields
  (:data:`IDENTITY_FIELDS`), so an incidental change (e.g. a latency number) does
  not invalidate the profile.
* :func:`mark_stale_on_change` — associate a fingerprint with a tier's profile
  rows and mark them stale when it differs from the stored one. Delegates the
  atomic compare-and-set to :meth:`ProfileStore.apply_fingerprint` (which holds
  the store lock), so it composes with the concurrent calibration writes.

Stdlib-only (``hashlib`` + ``json``); deterministic; no clock, no I/O.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, List, Mapping, Optional

from .profile_store import ProfileStore

#: Schema tag for the canonicalized identity blob — bump if the set of fields
#: that feed the digest changes (so an old fingerprint can't silently collide
#: with a new-scheme one).
FINGERPRINT_SCHEMA = "anvil-serving.router.fingerprint/v1"

# ``Tier.params`` is part of the serve identity because it can carry
# quality-affecting serve/sampling metadata. Some keys are deliberately
# smoke/eval-only recipe metadata: changing them should make benchmark tooling
# more repeatable, but must not stale measured profile rows.
FINGERPRINT_IGNORED_PARAM_KEYS = frozenset(
    {
        "generation_probe_max_tokens",
        "interaction_benchmark_max_tokens",
        "interaction_benchmark_stream_max_tokens",
        "interaction_benchmark_reasoning_effort",
        "interaction_benchmark_max_tokens_by_intent",
        "interaction_benchmark_stream_max_tokens_by_intent",
    }
)

# The serve-identity fields that feed the digest, as ``canonical_key -> synonyms``.
# A spec may be a Mapping or any object with these as attributes (e.g. a
# config.Tier); the FIRST synonym that resolves to a non-None value wins. Only
# things that change OUTPUT QUALITY belong here — model weights/quantization, the
# serving ENGINE (vLLM/SGLang/…), the endpoint serving them, the wire dialect, the
# usable context window, an explicit bag of serve/sampling params, the REASONING
# configuration (thinking on/off, reasoning effort) carried in ``extra_body``, and
# the active serving MODE (agentic vs flexibility): a thinking-ON serve and a
# thinking-OFF serve of the same model are different quality regimes (CLAUDE.md
# gotcha #6/#9), so they must be DISTINCT fingerprints. The ``engine`` axis
# (ADR-0010) makes an in-place engine swap at the SAME base_url — which can shift
# tokenization/sampling behavior and thus output quality — observable, so old
# measured rows go stale on the swap. The ``mode`` axis (ADR-0011 / flexibility:T013)
# makes the SAME model measured under the agentic vs flexibility config a DISTINCT
# measured identity — mode is a GLOBAL, not a per-tier attribute, so it is threaded
# into :func:`identity` / :func:`serve_fingerprint` explicitly (a Tier carries no
# ``mode``) rather than resolved off a tier spec. Volatile operational fields
# (latency, pid, load) are deliberately excluded so they don't churn the profile. A
# field a tier does not set (no ``engine``, no ``extra_body``, no ``mode``) resolves
# to None, which :func:`identity` omits — so it hashes byte-identically to before
# that field existed (no churn); only serves that SET the field change.
IDENTITY_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tier_id", ("tier_id", "id")),
    ("model", ("model", "model_id", "served_model", "model_name")),
    ("revision", ("revision", "model_revision", "weights_revision")),
    ("quantization", ("quantization", "quant", "dtype")),
    ("engine", ("engine",)),
    ("endpoint", ("base_url", "endpoint", "url")),
    ("dialect", ("dialect",)),
    ("context_limit", ("context_limit", "max_context", "context_window")),
    ("params", ("params", "serve_params", "sampling_params")),
    ("reasoning", ("extra_body",)),
)


def _resolve(spec: Any, names: tuple[str, ...]) -> Any:
    """First non-None value among ``names`` on ``spec`` (Mapping keys or attrs)."""
    for name in names:
        if isinstance(spec, Mapping):
            if name in spec and spec[name] is not None:
                return spec[name]
        else:
            value = getattr(spec, name, None)
            if value is not None:
                return value
    return None


def identity(spec: Any, *, mode: Optional[str] = None) -> dict[str, Any]:
    """The normalized serve-identity dict that feeds :func:`serve_fingerprint`.

    Exposed (not just inlined) so callers/tests can SEE exactly what is hashed —
    only the present, documented :data:`IDENTITY_FIELDS`, under their canonical
    names. A field that resolves to ``None`` on ``spec`` is omitted entirely, so
    adding an unrelated attribute to a spec never changes its fingerprint.

    ``mode`` (ADR-0011 / flexibility:T013) is the active serving mode (``agentic``
    / ``flexibility``). It is a GLOBAL, not a per-tier ``spec`` attribute, so it is
    threaded in ONLY as this keyword — a ``mode`` key on ``spec`` itself is
    deliberately NOT resolved (it is absent from :data:`IDENTITY_FIELDS`). That
    keeps the no-churn invariant UNCONDITIONAL: a mode-less call is byte-identical
    to pre-T013 no matter what keys ``spec`` carries. When set, ``mode`` enters the
    hashed identity under the canonical ``"mode"`` key — so the SAME model measured
    in agentic vs flexibility mode is a DISTINCT identity. When ``None`` (a
    ``--config`` boot with no mode) it is omitted (no churn).
    """
    out: dict[str, Any] = {}
    for canonical, names in IDENTITY_FIELDS:
        value = _resolve(spec, names)
        if canonical == "params" and isinstance(value, Mapping):
            value = {
                key: item
                for key, item in value.items()
                if str(key) not in FINGERPRINT_IGNORED_PARAM_KEYS
            }
            if not value:
                value = None
        if value is not None:
            out[canonical] = value
    if mode is not None:
        out["mode"] = mode
    return out


def serve_fingerprint(spec: Any, *, mode: Optional[str] = None) -> str:
    """Stable digest over a tier's quality-affecting serve identity.

    ``spec`` is anything exposing the :data:`IDENTITY_FIELDS` as Mapping keys or
    attributes (a ``dict`` or a :class:`~anvil_serving.router.config.Tier` both
    work). The identity is canonicalized to sorted-key JSON (with the schema tag)
    and SHA-256'd, so two specs that agree on every identity field hash the same
    and any change to a hashed field changes the digest. Returns a 64-char hex
    string.

    ``mode`` (ADR-0011 / flexibility:T013) overlays the active serving mode onto
    the hashed identity; ``None`` (unset) hashes byte-identically to pre-T013.
    """
    blob = {"schema": FINGERPRINT_SCHEMA, "identity": identity(spec, mode=mode)}
    canonical = json.dumps(
        blob, sort_keys=True, separators=(",", ":"), default=_canonical_default
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_default(o: Any) -> Any:
    """Deterministic JSON fallback for non-JSON identity values.

    ``default=str`` alone made the "stable" digest hash-seed-dependent for a
    ``set``/``frozenset`` inside ``params`` (its str() order varies across
    processes), causing spurious cross-process staleness marks. Sets are
    canonicalized element-wise and sorted by their serialized form.

    A non-``dict`` :class:`~collections.abc.Mapping` (e.g. the ``MappingProxyType``
    a :class:`~anvil_serving.router.config.Tier` stores its ``extra_body`` /
    reasoning config in) is converted to a plain ``dict`` so the encoder
    re-serializes it with ``sort_keys`` — it hashes identically to an equivalent
    inline dict and is insensitive to key insertion order, rather than falling to
    an order-dependent ``mappingproxy(...)`` repr. Everything else keeps the
    str() fallback.
    """
    if isinstance(o, (set, frozenset)):
        return sorted(
            (json.dumps(e, sort_keys=True, default=_canonical_default) for e in o)
        )
    if isinstance(o, Mapping):
        return dict(o)
    return str(o)


def mark_stale_on_change(
    store: ProfileStore, tier_id: str, new_fingerprint: str
) -> List[Optional[str]]:
    """Mark every ``tier_id`` row stale when its serve identity changed.

    Associates ``new_fingerprint`` with all of ``tier_id``'s profile rows via the
    store's atomic :meth:`~anvil_serving.router.profile_store.ProfileStore.apply_fingerprint`:

    * a row with no recorded fingerprint adopts ``new_fingerprint`` as its
      baseline (NOT stale — nothing was invalidated);
    * a row whose recorded fingerprint already equals ``new_fingerprint`` is left
      untouched;
    * a row whose recorded fingerprint DIFFERS is advanced to ``new_fingerprint``
      and flagged ``stale`` (routing should now distrust it until it is
      re-measured by the calibrator).

    Rows of other tiers are never touched. Returns the work classes newly marked
    stale (sorted) — the staleness blast radius for this change.
    """
    return store.apply_fingerprint(tier_id, new_fingerprint)


def refresh_fingerprint(
    store: ProfileStore, tier_id: str, spec: Any, *, mode: Optional[str] = None
) -> List[Optional[str]]:
    """Convenience wire: fingerprint ``spec`` then :func:`mark_stale_on_change`.

    The one call a serve-startup / reconfigure hook makes to keep the profile
    honest: compute the current serve fingerprint for ``tier_id`` and stale its
    rows if the identity changed since they were measured.

    ``mode`` (ADR-0011 / flexibility:T013) folds the active serving mode into the
    fingerprint, so a row measured under one mode goes stale when the SAME tier is
    next served under a different mode. ``None`` preserves the pre-T013 digest.
    """
    return mark_stale_on_change(store, tier_id, serve_fingerprint(spec, mode=mode))
