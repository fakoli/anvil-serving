"""A small in-process seam registry + failure isolation (harness-router:T011).

This is the "small in-process registry" half of the §10 seam design — NOT a
plugin framework. It maps ``(seam, name) -> implementation`` for the seams named
in :mod:`.seams`, validating every registration against that closed catalog
(contract rule 3, *versioned contracts*: an unknown seam is refused). There is no
dynamic loading, no entry-point discovery, no manifest — those wait for M3+, once
a seam has a real third-party second impl.

**Failure isolation = fallback (contract rule 1; AC2).** A seam implementation
runs arbitrary code in the request path, so a throwing/slow one must degrade to a
fallback trigger, never crash the request. For the verify seam — the one the
T009 fallback walk gates on — :func:`safe_verify` runs a verifier and converts
EVERY fault shape (a RAISE, a ``.name`` that itself raises, a non-VerifyResult
return, or a latency-budget overrun) into a *failing*
:class:`~anvil_serving.router.verify.VerifyResult` (``passed=False``, a
content-free reason). The isolation boundary NEVER raises and NEVER hangs: a
budgeted verifier runs on a **daemon** thread that is *abandoned* (not joined) on
overrun, so a genuinely-hung verifier can never block interpreter / CLI / pytest
exit. :func:`wrap_verifier` returns a drop-in
:class:`~anvil_serving.router.verify.Verifier` that delegates to
:func:`safe_verify`, so a throwing verifier handed to ``route_with_fallback``
simply makes that tier fail verify and the router escalates — and it inherits
every guard, name-raise and bad-return included. :func:`safe_call` is the same
idea for any data-plane seam.

:func:`default_registry` pre-seeds at least one real implementation for EVERY
seam in the catalog (AC1), resolved BY NAME.

Stdlib-only; mirrors the house style of the rest of the router package.
"""

from __future__ import annotations

import threading
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

from .seams import (
    DecisionLogObserver,
    FunctionClassifier,
    FunctionRoutingPolicy,
    SEAM_NAMES,
)
from .verify import VerifyResult


# --------------------------------------------------------------------------- #
# errors — KeyError-derived so a caller can ``except KeyError`` generically,
# with a clean (unquoted) message instead of KeyError's repr-wrapped default.
# --------------------------------------------------------------------------- #
class RegistryError(KeyError):
    """Base for registry lookup/validation failures (a :class:`KeyError`)."""

    def __str__(self) -> str:  # KeyError repr-wraps args[0]; keep the message plain.
        return self.args[0] if self.args else ""


class UnknownSeamError(RegistryError):
    """A seam name absent from the :mod:`.seams` catalog was used."""


class UnknownImplementationError(RegistryError):
    """No implementation is registered under the requested ``(seam, name)``."""


# --------------------------------------------------------------------------- #
# the registry
# --------------------------------------------------------------------------- #
class Registry:
    """An in-process map of ``(seam, name) -> implementation``.

    The set of valid seams is fixed at construction from the :mod:`.seams`
    catalog (``SEAM_NAMES`` by default); :meth:`register` refuses any other seam
    (contract rule 3). Implementations are looked up by name via :meth:`resolve`.
    Nothing here loads code dynamically — a caller hands in already-constructed
    implementation objects.
    """

    __slots__ = ("_seams", "_impls")

    def __init__(self, seam_names: Optional[Any] = None) -> None:
        names = tuple(seam_names) if seam_names is not None else tuple(SEAM_NAMES)
        # Dedupe (order-preserving) so ``seams`` and the error-message catalogs
        # match the ``_impls`` buckets EXACTLY — a repeated seam name must not
        # leave ``seams`` listing a stage twice while ``_impls`` holds one bucket.
        self._seams = tuple(dict.fromkeys(names))
        # seam -> {name: impl}; one bucket per known seam, created up front so
        # names()/implementations() never KeyError on a valid-but-empty seam.
        self._impls: dict[str, dict[str, Any]] = {s: {} for s in self._seams}

    @property
    def seams(self) -> tuple[str, ...]:
        """The seam names this registry accepts (the catalog it was built from)."""
        return self._seams

    def _check_seam(self, seam: str) -> None:
        if seam not in self._impls:
            raise UnknownSeamError(
                f"unknown seam {seam!r}; known seams: {', '.join(self._seams)}"
            )

    def register(self, seam: str, name: str, impl: Any) -> Any:
        """Register ``impl`` under ``(seam, name)``; return it for chaining.

        Refuses a seam outside the catalog (:class:`UnknownSeamError`). A repeated
        ``(seam, name)`` overwrites — last registration wins (a deliberate
        in-process override, not an error).
        """
        self._check_seam(seam)
        self._impls[seam][name] = impl
        return impl

    def resolve(self, seam: str, name: str) -> Any:
        """Return the implementation registered under ``(seam, name)``.

        Raises :class:`UnknownSeamError` for an unknown seam, or
        :class:`UnknownImplementationError` (naming what *is* registered) for an
        unknown name — both :class:`KeyError` subclasses.
        """
        self._check_seam(seam)
        try:
            return self._impls[seam][name]
        except KeyError:
            raise UnknownImplementationError(
                f"no implementation {name!r} registered for seam {seam!r}; "
                f"registered: {', '.join(self.names(seam)) or '(none)'}"
            ) from None

    def names(self, seam: str) -> tuple[str, ...]:
        """The registered implementation names for ``seam`` (registration order)."""
        self._check_seam(seam)
        return tuple(self._impls[seam])

    def implementations(self, seam: str) -> Mapping[str, Any]:
        """A READ-ONLY ``{name: impl}`` view for ``seam`` (a copy, mutation-proof)."""
        self._check_seam(seam)
        return MappingProxyType(dict(self._impls[seam]))


# --------------------------------------------------------------------------- #
# failure isolation (contract rule 1 + 2; AC2)
# --------------------------------------------------------------------------- #
def _verifier_name(verifier: Any) -> str:
    """Resolve a verifier's display name — itself NEVER raising.

    Name resolution is part of the isolation boundary, so it must survive a
    hostile verifier whose ``name`` is a property that raises (any type). Falls
    back to the class name, then a constant, so a :class:`VerifyResult` can always
    be built. A non-string / empty ``name`` also degrades to the class name.
    """
    try:
        n = getattr(verifier, "name", None)
        return n if isinstance(n, str) and n else type(verifier).__name__
    except Exception:  # noqa: BLE001 - a .name property may raise ANY type
        return "verifier"


def _run_verify(verifier: Any, response: Any, name: str) -> VerifyResult:
    """Call ``verifier.verify(response)`` and normalize the outcome — NEVER raises.

    Guards both ends: a RAISE (incl. a missing ``verify`` attribute ->
    ``AttributeError``) becomes a content-free fail naming only the exception TYPE
    (R012 — never the raised message body), and a RETURN that is not a
    :class:`VerifyResult` (a verifier handing back ``None`` / a ``str`` / a tuple)
    becomes a fail too, so no downstream ``.passed`` / ``.score`` access can
    ``AttributeError`` on a bad shape.
    """
    try:
        result = verifier.verify(response)
    except Exception as exc:  # noqa: BLE001 - contract: a seam fault is a fallback trigger
        return VerifyResult(name, False, 0.0, f"{name} raised: {type(exc).__name__}")
    if not isinstance(result, VerifyResult):
        return VerifyResult(
            name, False, 0.0, f"{name} returned non-VerifyResult: {type(result).__name__}"
        )
    return result


def safe_verify(
    verifier: Any,
    response: Any,
    *,
    timeout: Optional[float] = None,
) -> VerifyResult:
    """Run a verifier under FULL fault isolation — never raises, never hangs.

    Every fault shape a seam impl can present collapses to a failing
    :class:`~anvil_serving.router.verify.VerifyResult` (``passed=False``,
    ``score=0.0``) that the T009 fallback walk escalates past: a ``verify`` that
    RAISES, a ``.name`` property that itself raises, a non-VerifyResult RETURN, and
    (with a ``timeout``) a verify that overruns its latency budget or hangs
    outright. The reason is **content-free** — the verifier name + the exception
    TYPE / budget only, never the verifier's raw reason or any response text
    (R012).

    ``timeout`` (seconds) enforces contract rule 2 (latency budget). The verify
    runs on a **daemon** worker thread; on overrun the worker is *abandoned*, not
    joined, so a genuinely-hung verifier can never block interpreter / CLI /
    pytest exit (a daemon thread does not keep the process alive). A budgeted
    verifier SHOULD therefore still be bounded/interruptible: an abandoned worker
    keeps running with its ``response`` reference, a write-after-return hazard for
    an *impure* verifier — bound them (the shipped structural checks are pure and
    ``MAX_SCAN_BYTES``-bounded). ``None`` (default) runs fully-guarded INLINE with
    no thread overhead.

    Distinguishing a HANG from a verifier that itself raised ``TimeoutError`` is
    deliberate: the latter completes through :func:`_run_verify` and lands in
    ``holder`` (reason ``"raised: TimeoutError"``); only a still-running worker
    (``t.is_alive()``) reports a budget overrun — no ``concurrent.futures``
    ambiguity where both surface as the same ``TimeoutError``.
    """
    name = _verifier_name(verifier)
    if timeout is None:
        return _run_verify(verifier, response, name)  # fully-guarded inline path

    holder: dict[str, VerifyResult] = {}

    def _worker() -> None:
        holder["r"] = _run_verify(verifier, response, name)  # _run_verify never raises

    # DAEMON: a hung worker is abandoned, never joined — it cannot block exit.
    t = threading.Thread(target=_worker, name=f"safe-verify-{name}", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        # Real budget overrun: the worker is STILL running (a hang or slow check),
        # distinct from a verifier that raised TimeoutError (that path completes
        # and lands in ``holder`` via _run_verify).
        return VerifyResult(name, False, 0.0, f"{name} exceeded {timeout}s latency budget")
    return holder.get("r") or VerifyResult(
        name, False, 0.0, f"{name}: worker produced no result"
    )


class _SafeVerifier:
    """A :class:`~anvil_serving.router.verify.Verifier` that wraps another in :func:`safe_verify`.

    Carries the inner verifier's ``name`` (so the audit trail and
    ``run_verifiers`` see the real check), and a :meth:`verify` that delegates to
    :func:`safe_verify` and so can never raise. Because it routes through
    ``safe_verify`` it inherits EVERY guard — RAISE, a ``.name`` that raises, a
    non-VerifyResult return, and the latency budget — not just the throw path.
    Satisfies the ``Verifier`` Protocol, so it drops straight into the T009
    ``route_with_fallback`` verifier chain.
    """

    def __init__(self, inner: Any, *, timeout: Optional[float] = None) -> None:
        self._inner = inner
        self._timeout = timeout
        self.name = _verifier_name(inner)  # name resolution that never raises

    def verify(self, response: Any) -> VerifyResult:
        return safe_verify(self._inner, response, timeout=self._timeout)


def wrap_verifier(verifier: Any, *, timeout: Optional[float] = None) -> Any:
    """Return a fault-isolating :class:`~anvil_serving.router.verify.Verifier`.

    The returned object delegates to ``verifier`` via :func:`safe_verify`, so a
    throwing/slow verifier becomes a verify-FAIL instead of an exception — wire it
    into ``route_with_fallback`` and a bad check triggers fallback, never a crash.
    """
    return _SafeVerifier(verifier, timeout=timeout)


def safe_call(
    fn: Callable[..., Any],
    *args: Any,
    on_error: Callable[[BaseException], Any],
    **kwargs: Any,
) -> Any:
    """Call ``fn(*args, **kwargs)``; on ANY exception return ``on_error(exc)``.

    The general-purpose data-plane analogue of :func:`safe_verify`: it isolates a
    seam call (a classifier, a routing policy) so a throwing impl yields a
    caller-chosen fallback value instead of propagating. The caller decides what a
    failure means by supplying ``on_error`` (e.g. "return the safer-tier
    decision"); this keeps the policy here mechanism-only.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - contract: isolate the seam call
        return on_error(exc)


# --------------------------------------------------------------------------- #
# the pre-seeded default registry (AC1)
# --------------------------------------------------------------------------- #
def default_registry() -> Registry:
    """A :class:`Registry` with at least one real implementation per seam (AC1).

    Every seam in the :mod:`.seams` catalog gets a working, in-process impl,
    resolvable BY NAME:

    * ``backend``        -> the local no-network reference backends
      (``static``/``echo``); ``CloudBackend`` is omitted here because it needs a
      cloud tier + a credential at construction (not a default-registry concern).
    * ``verifier``       -> the full T007 structural chain, each by its stable name.
    * ``dialect``        -> the two M0 wire dialects (``anthropic``/``openai``).
    * ``classifier``     -> an adapter over ``classify.classify`` (``heuristic``).
    * ``routing_policy`` -> an adapter over ``policy.route`` (``residency-aware``).
    * ``profile_store``  -> the hand-authored seed profile (``default``).
    * ``availability_store`` -> no-network backwards-compatible readiness
      (``always``); production serving replaces it with cached HTTP health.
    * ``observer``       -> a :class:`~anvil_serving.router.decision_log.DecisionLog`-backed
      audit sink (``decision_log``).
    """
    # Imported lazily so importing the registry never pulls the whole serving
    # stack (and so seams.py stays free of concrete-impl imports).
    from .availability import AlwaysAvailable
    from .backends import EchoBackend, StaticBackend
    from .classify import classify
    from .decision_log import DecisionLog
    from .dialects.anthropic import AnthropicDialect
    from .dialects.openai import OpenAIDialect
    from .policy import route as policy_route
    from .profile_store import default_profile
    from .verify import default_verifiers

    reg = Registry()

    # backend (data) — local, deterministic, no network / GPU.
    reg.register("backend", "static", StaticBackend(["ok"]))
    reg.register("backend", "echo", EchoBackend())

    # verifier (data) — the whole structural chain, keyed by each check's name.
    for v in default_verifiers():
        reg.register("verifier", v.name, v)

    # dialect (data, front door) — the two M0 wire protocols.
    reg.register("dialect", "anthropic", AnthropicDialect())
    reg.register("dialect", "openai", OpenAIDialect())

    # classifier (data) — adapter lifting the Tier-0 classify() function.
    reg.register("classifier", "heuristic", FunctionClassifier(classify))

    # routing_policy (data) — adapter lifting the residency-aware policy.route().
    reg.register("routing_policy", "residency-aware", FunctionRoutingPolicy(policy_route))

    # profile_store (control) — the hand-authored seed quality table.
    reg.register("profile_store", "default", default_profile())

    # availability_store (cross) — no-network compatibility implementation.
    reg.register("availability_store", "always", AlwaysAvailable())

    # observer (cross) — a DecisionLog-backed audit sink.
    reg.register("observer", "decision_log", DecisionLogObserver(DecisionLog()))

    return reg


__all__ = [
    "Registry",
    "RegistryError",
    "UnknownSeamError",
    "UnknownImplementationError",
    "safe_verify",
    "wrap_verifier",
    "safe_call",
    "default_registry",
]
