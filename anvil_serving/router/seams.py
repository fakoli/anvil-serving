"""The typed seam catalog — naming the router pipeline's stages (harness-router:T011).

The router is a pipeline, so "plugin architecture" here means **naming its stages
as typed seams** (``typing.Protocol`` + the small in-process
:mod:`~anvil_serving.router.registry`), *not* a plugin framework — exactly the
split argued in ``docs/QUALITY-GATED-ROUTER.md`` §10. This module is the single
catalog of those seams: one ``runtime_checkable`` ``Protocol`` per pipeline stage,
plus :data:`SEAMS` metadata the registry and tests enumerate.

**Single source of truth.** Two stages already shipped a real ``Protocol`` in
earlier milestones; this module RE-EXPORTS those objects rather than redefining
them, so there is exactly one ``Backend`` / ``Verifier`` / ``Dialect`` type in the
codebase (``seams.Backend is internal.Backend`` holds — pinned by a test):

* :class:`Backend`  — re-exported from :mod:`.internal` (M0 inference seam).
* :class:`Verifier` — re-exported from :mod:`.verify` (T007 verify seam).
* :class:`Dialect`  — re-exported from :mod:`.dialects` (M0 front-door seam).

The remaining stages had a concrete implementation but no named ``Protocol`` yet;
this module defines a minimal ``runtime_checkable`` one that the EXISTING impl
satisfies structurally (so ``isinstance(existing_impl, TheSeam)`` is already
``True``). Where the existing implementation is a *module-level function* rather
than an object (``classify.classify``, ``policy.route``) a tiny stateless adapter
(:class:`FunctionClassifier` / :class:`FunctionRoutingPolicy`) lifts it onto the
object-shaped ``Protocol``; :class:`DecisionLogObserver` likewise lifts a
:class:`~anvil_serving.router.decision_log.DecisionLog` onto :class:`Observer`.

**Contract rules from day one (§10).** These shape the seam surface, and the
failure-isolation rule is enforced in the registry (``safe_verify`` / ``safe_call``):

1. **failure isolation = fallback** — a seam impl that throws/times out is treated
   as another fallback trigger, wrapped (never a crash). See
   :func:`anvil_serving.router.registry.safe_verify`.
2. **latency budget** — data-plane seams declare & respect a budget; heavy work
   goes async (``safe_verify(..., timeout=...)``).
3. **versioned contracts** — the registry refuses an unknown seam name; a public
   third-party plugin SDK (entry points + capability manifest) is deferred to M3+.
4. **trust** — seam impls run in the request path; in-process / first-party only
   for now (no dynamic loading until a seam has a real second impl).

Stdlib-only; ``from __future__`` annotations keep the seam type references lazy.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Optional

from typing import Protocol, runtime_checkable

# Re-export the seams that ALREADY own a Protocol — single source of truth. These
# imports are the canonical objects; ``seams.Backend is internal.Backend`` etc.
from .internal import Backend
from .verify import Verifier
from .dialects import Dialect

if TYPE_CHECKING:  # annotation-only; never evaluated at runtime (future annotations).
    from .classify import Classification
    from .config import RouterConfig
    from .decision_log import DecisionRecord
    from .intent import Intent
    from .internal import InternalRequest
    from .policy import Needs, RoutingDecision


# --------------------------------------------------------------------------- #
# Protocols for the pipeline stages that had no named seam yet.
#
# Each is runtime_checkable so the EXISTING concrete impl (or a thin adapter
# below) satisfies isinstance() structurally — these add a name to a stage, they
# do not introduce a new contract the impls must newly conform to.
# --------------------------------------------------------------------------- #
@runtime_checkable
class Classifier(Protocol):
    """The Tier-0 work-class seam (DATA plane; early — M1).

    Labels an :class:`~anvil_serving.router.internal.InternalRequest` with one of
    :data:`~anvil_serving.router.classify.WORK_CLASSES`. The shipped impl is the
    module-level :func:`anvil_serving.router.classify.classify` function; wrap it
    with :class:`FunctionClassifier` to satisfy this object-shaped seam.
    """

    def classify(self, request: "InternalRequest") -> "Classification":
        ...


@runtime_checkable
class RoutingPolicy(Protocol):
    """The (intent, profile) -> ordered tier list seam (DATA plane; early — M2).

    Resolves a routing :class:`~anvil_serving.router.intent.Intent` against the
    config and the quality :class:`ProfileStore` into an ordered
    :class:`~anvil_serving.router.policy.RoutingDecision`. The shipped impl is the
    module-level :func:`anvil_serving.router.policy.route`; wrap it with
    :class:`FunctionRoutingPolicy`. ``residency`` / ``needs`` are accepted as the
    real ``route`` takes them but are optional, so the minimal three-argument call
    ``route(intent, config, profile)`` is the seam's required shape.
    """

    def route(
        self,
        intent: "Intent",
        config: "RouterConfig",
        profile: "ProfileStore",
        *,
        residency: Optional[str] = None,
        needs: "Optional[Needs]" = None,
    ) -> "RoutingDecision":
        ...


@runtime_checkable
class ProfileStore(Protocol):
    """Where the quality table lives (CONTROL plane; maybe-early — M2).

    Resolves a ``(tier_id, work_class)`` pair to a trust ``decision`` (one of
    :data:`~anvil_serving.router.profile_store.DECISIONS`) and an advisory
    ``score``. The shipped impl —
    :class:`anvil_serving.router.profile_store.ProfileStore` (built by
    ``default_profile()``) — satisfies this seam directly; a future store could
    back the same two methods with a measured/persisted table.
    """

    def decision(
        self, tier_id: str, work_class: Optional[str], *, is_cloud: bool = False
    ) -> str:
        ...

    def score(self, tier_id: str, work_class: Optional[str]) -> float:
        ...


@runtime_checkable
class Observer(Protocol):
    """The audit / metrics / fallback-event sink seam (CROSS-cutting; early — M2).

    A one-method hook the router calls with each finished
    :class:`~anvil_serving.router.decision_log.DecisionRecord`. The shipped sink is
    :class:`~anvil_serving.router.decision_log.DecisionLog` (its ``record`` method
    is the same shape); :class:`DecisionLogObserver` adapts it to the seam's
    ``observe`` verb so a metrics exporter could be dropped in alongside it.
    """

    def observe(self, record: "DecisionRecord") -> None:
        ...


# --------------------------------------------------------------------------- #
# Thin adapters: lift the shipped function/object impls onto the object-shaped
# Protocols above. Stateless and import-free of the concrete modules — the
# registry supplies the real callable / sink — so seams.py stays a low-coupling
# catalog.
# --------------------------------------------------------------------------- #
class FunctionClassifier:
    """Adapter: make a ``classify(request) -> Classification`` function a :class:`Classifier`."""

    def __init__(self, fn: Any) -> None:
        self._fn = fn

    def classify(self, request: "InternalRequest") -> "Classification":
        return self._fn(request)


class FunctionRoutingPolicy:
    """Adapter: make a ``route(intent, config, profile, *, ...)`` function a :class:`RoutingPolicy`."""

    def __init__(self, fn: Any) -> None:
        self._fn = fn

    def route(
        self,
        intent: "Intent",
        config: "RouterConfig",
        profile: "ProfileStore",
        *,
        residency: Optional[str] = None,
        needs: "Optional[Needs]" = None,
    ) -> "RoutingDecision":
        # Forward the optional kwargs only when set, so the documented minimal
        # three-argument route function lifts without a TypeError at request
        # time (the real policy.route accepts both; a plugin's may not).
        kwargs: dict = {}
        if residency is not None:
            kwargs["residency"] = residency
        if needs is not None:
            kwargs["needs"] = needs
        return self._fn(intent, config, profile, **kwargs)


class DecisionLogObserver:
    """Adapter: forward each record to a :class:`DecisionLog`-shaped sink (``.record``).

    Wraps any object exposing ``record(DecisionRecord) -> None`` (the
    :class:`~anvil_serving.router.decision_log.DecisionLog` audit sink) and exposes
    it as an :class:`Observer`. The wrapped sink is reachable via :attr:`log` so a
    caller can read back the recorded trail.
    """

    def __init__(self, log: Any) -> None:
        self._log = log

    def observe(self, record: "DecisionRecord") -> None:
        self._log.record(record)

    @property
    def log(self) -> Any:
        """The wrapped sink (e.g. the backing :class:`DecisionLog`)."""
        return self._log


# --------------------------------------------------------------------------- #
# Seam catalog — the enumerable metadata the registry validates against and the
# tests iterate. Adding a stage here WITHOUT seeding an impl in
# ``registry.default_registry`` deliberately fails the AC1 enumeration test.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SeamSpec:
    """One seam's catalog entry: its name, ``Protocol``, plane, and timing.

    ``plane`` is ``"data"`` / ``"control"`` / ``"cross"`` (§10); ``early`` is True
    for the M0-M2 seams (all of the current catalog) versus a deferred M3+ one.
    ``protocol`` is the ``runtime_checkable`` type an impl must satisfy.
    """

    name: str
    protocol: type
    plane: str
    early: bool
    summary: str


_SPECS = (
    SeamSpec("dialect", Dialect, "data", True,
             "front door: one wire protocol <-> InternalRequest, both directions"),
    SeamSpec("classifier", Classifier, "data", True,
             "request -> work class (Tier-0)"),
    SeamSpec("routing_policy", RoutingPolicy, "data", True,
             "(intent, profile) -> ordered candidate tier list"),
    SeamSpec("backend", Backend, "data", True,
             "tier -> inference engine; yields text deltas"),
    SeamSpec("verifier", Verifier, "data", True,
             "response -> pass/fail/score (cheap, chainable)"),
    SeamSpec("profile_store", ProfileStore, "control", True,
             "where the (tier, work-class) quality table lives"),
    SeamSpec("observer", Observer, "cross", True,
             "audit / metrics / fallback-event sink"),
)

#: The seam catalog, keyed by seam name (insertion-ordered to mirror the pipeline).
SEAMS: Mapping[str, SeamSpec] = MappingProxyType({s.name: s for s in _SPECS})

#: The seam names, in pipeline order — the registry validates against this set.
SEAM_NAMES = tuple(SEAMS)


__all__ = [
    # re-exported single-source-of-truth Protocols
    "Backend",
    "Verifier",
    "Dialect",
    # newly-named Protocols for the remaining stages
    "Classifier",
    "RoutingPolicy",
    "ProfileStore",
    "Observer",
    # thin adapters lifting shipped impls onto the object-shaped seams
    "FunctionClassifier",
    "FunctionRoutingPolicy",
    "DecisionLogObserver",
    # catalog
    "SeamSpec",
    "SEAMS",
    "SEAM_NAMES",
]
