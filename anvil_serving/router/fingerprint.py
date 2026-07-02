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

# The serve-identity fields that feed the digest, as ``canonical_key -> synonyms``.
# A spec may be a Mapping or any object with these as attributes (e.g. a
# config.Tier); the FIRST synonym that resolves to a non-None value wins. Only
# things that change OUTPUT QUALITY belong here — model weights/quantization, the
# endpoint serving them, the wire dialect, the usable context window, and an
# explicit bag of serve/sampling params. Volatile operational fields (latency,
# pid, load) are deliberately excluded so they don't churn the profile.
IDENTITY_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tier_id", ("tier_id", "id")),
    ("model", ("model", "model_id", "served_model", "model_name")),
    ("revision", ("revision", "model_revision", "weights_revision")),
    ("quantization", ("quantization", "quant", "dtype")),
    ("endpoint", ("base_url", "endpoint", "url")),
    ("dialect", ("dialect",)),
    ("context_limit", ("context_limit", "max_context", "context_window")),
    ("params", ("params", "serve_params", "sampling_params")),
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


def identity(spec: Any) -> dict[str, Any]:
    """The normalized serve-identity dict that feeds :func:`serve_fingerprint`.

    Exposed (not just inlined) so callers/tests can SEE exactly what is hashed —
    only the present, documented :data:`IDENTITY_FIELDS`, under their canonical
    names. A field that resolves to ``None`` on ``spec`` is omitted entirely, so
    adding an unrelated attribute to a spec never changes its fingerprint.
    """
    out: dict[str, Any] = {}
    for canonical, names in IDENTITY_FIELDS:
        value = _resolve(spec, names)
        if value is not None:
            out[canonical] = value
    return out


def serve_fingerprint(spec: Any) -> str:
    """Stable digest over a tier's quality-affecting serve identity.

    ``spec`` is anything exposing the :data:`IDENTITY_FIELDS` as Mapping keys or
    attributes (a ``dict`` or a :class:`~anvil_serving.router.config.Tier` both
    work). The identity is canonicalized to sorted-key JSON (with the schema tag)
    and SHA-256'd, so two specs that agree on every identity field hash the same
    and any change to a hashed field changes the digest. Returns a 64-char hex
    string.
    """
    blob = {"schema": FINGERPRINT_SCHEMA, "identity": identity(spec)}
    canonical = json.dumps(
        blob, sort_keys=True, separators=(",", ":"), default=_canonical_default
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_default(o: Any) -> Any:
    """Deterministic JSON fallback for non-JSON identity values.

    ``default=str`` alone made the "stable" digest hash-seed-dependent for a
    ``set``/``frozenset`` inside ``params`` (its str() order varies across
    processes), causing spurious cross-process staleness marks. Sets are
    canonicalized element-wise and sorted by their serialized form; everything
    else keeps the str() fallback.
    """
    if isinstance(o, (set, frozenset)):
        return sorted(
            (json.dumps(e, sort_keys=True, default=_canonical_default) for e in o)
        )
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
    store: ProfileStore, tier_id: str, spec: Any
) -> List[Optional[str]]:
    """Convenience wire: fingerprint ``spec`` then :func:`mark_stale_on_change`.

    The one call a serve-startup / reconfigure hook makes to keep the profile
    honest: compute the current serve fingerprint for ``tier_id`` and stale its
    rows if the identity changed since they were measured.
    """
    return mark_stale_on_change(store, tier_id, serve_fingerprint(spec))
