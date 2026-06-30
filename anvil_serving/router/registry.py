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
T009 fallback walk gates on — :func:`safe_verify` runs a verifier and converts a
RAISE (or a latency-budget overrun) into a *failing*
:class:`~anvil_serving.router.verify.VerifyResult` (``passed=False``, a
content-free reason). :func:`wrap_verifier` returns a drop-in
:class:`~anvil_serving.router.verify.Verifier` that does this, so a throwing
verifier handed to ``route_with_fallback`` simply makes that tier fail verify and
the router escalates. :func:`safe_call` is the same idea for any data-plane seam.

:func:`default_registry` pre-seeds at least one real implementation for EVERY
seam in the catalog (AC1), resolved BY NAME.

Stdlib-only; mirrors the house style of the rest of the router package.
"""

from __future__ import annotations

from concurrent import futures
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
        self._seams = names
        # seam -> {name: impl}; one bucket per known seam, created up front so
        # names()/implementations() never KeyError on a valid-but-empty seam.
        self._impls: dict[str, dict[str, Any]] = {s: {} for s in names}

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
def safe_verify(
    verifier: Any,
    response: Any,
    *,
    timeout: Optional[float] = None,
) -> VerifyResult:
    """Run ``verifier.verify(response)``; a RAISE or timeout becomes a verify-FAIL.

    Failure isolation: a verifier that throws (a buggy/third-party check) or
    exceeds its latency budget must not crash the request — it becomes a failing
    :class:`~anvil_serving.router.verify.VerifyResult` (``passed=False``,
    ``score=0.0``), which the T009 fallback walk then escalates past. The reason
    is **content-free** (the verifier name + the exception TYPE / budget) — never
    the verifier's raw reason or any response text (R012).

    ``timeout`` (seconds) enforces contract rule 2 (latency budget): the verify is
    run on a worker thread and a budget overrun returns a fail promptly. A verify
    that ignores cancellation keeps running to completion in the background; the
    structural verifiers are pure and bounded (``MAX_SCAN_BYTES``), so the leaked
    work is bounded and the CALLER always returns within the budget. ``None``
    (default) runs inline with no thread overhead and only catches the RAISE path.
    """
    name = getattr(verifier, "name", verifier.__class__.__name__)
    if timeout is None:
        try:
            return verifier.verify(response)
        except Exception as exc:  # noqa: BLE001 - contract: a seam fault is a fallback trigger
            return VerifyResult(name, False, 0.0, f"{name} raised: {type(exc).__name__}")

    pool = futures.ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(verifier.verify, response)
    try:
        return fut.result(timeout=timeout)
    except futures.TimeoutError:
        return VerifyResult(name, False, 0.0, f"{name} exceeded {timeout}s latency budget")
    except Exception as exc:  # noqa: BLE001 - same contract as the inline path
        return VerifyResult(name, False, 0.0, f"{name} raised: {type(exc).__name__}")
    finally:
        # Do NOT block on a runaway worker (wait=False); a hung verify must not
        # make the budget meaningless. Pure/bounded verifiers finish on their own.
        pool.shutdown(wait=False)


class _SafeVerifier:
    """A :class:`~anvil_serving.router.verify.Verifier` that wraps another in :func:`safe_verify`.

    Carries the inner verifier's ``name`` (so the audit trail and
    ``run_verifiers`` see the real check), and a :meth:`verify` that can never
    raise. Satisfies the ``Verifier`` Protocol, so it drops straight into the
    T009 ``route_with_fallback`` verifier chain.
    """

    def __init__(self, inner: Any, *, timeout: Optional[float] = None) -> None:
        self._inner = inner
        self._timeout = timeout
        self.name = getattr(inner, "name", inner.__class__.__name__)

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
    * ``observer``       -> a :class:`~anvil_serving.router.decision_log.DecisionLog`-backed
      audit sink (``decision_log``).
    """
    # Imported lazily so importing the registry never pulls the whole serving
    # stack (and so seams.py stays free of concrete-impl imports).
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
