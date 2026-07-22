"""``anvil-serving router run`` - config -> per-tier backends -> front door (T012).

This is the pip-install-to-running surface: one verb that turns a validated
router :class:`~anvil_serving.router.config.RouterConfig` into a live,
protocol-standard front door (T001/T004). The wiring, end to end:

1. **Load** the ``[router]`` block (tiers + presets) with
   :func:`anvil_serving.router.config.load`.
2. **Build one Backend per tier** (:func:`build_backend_for_tier`):

   * a ``privacy == "cloud"`` tier -> :class:`~anvil_serving.router.backends.cloud.CloudBackend`
     (creds resolved from the tier's ``auth_env`` env var AT CONSTRUCTION; fails
     fast + named if unset);
   * a ``privacy == "local"`` tier -> :class:`RelayBackend`, a stdlib-``urllib``
     OpenAI/Anthropic relay to the tier's ``base_url`` (reuses CloudBackend's
     tested dialect request/response machinery; auth is OPTIONAL — local
     vLLM/SGLang servers usually need none).

   A cloud tier whose key is unset is **skipped with a warning, not fatal**, so
   ``serve --config`` still starts bound to the tiers it CAN serve.

3. **Compose selection** (:class:`RoutingBackend`): per request, resolve the
   intent (T003 :func:`~anvil_serving.router.intent.resolve`) and run the
   residency-aware policy (T005 :func:`~anvil_serving.router.policy.route`) to
   pick ONE tier, then delegate to that tier's backend. Both are never-raise
   public APIs, so this is a thin composition — NOT a re-implementation of
   routing.
4. **Start** the front door via
   :func:`~anvil_serving.router.front_door.make_server`, passing the canonical
   :data:`~anvil_serving.router.intent.PRESETS` so ``GET /v1/models`` advertises
   the router's intent vocabulary.

Routing scope — composed here (the T012 boundary is complete):

* **Composed here:** per-request tier *selection*. ``resolve()`` then ``route()``
  yield an ordered, quality-gated candidate list; :class:`RoutingBackend` picks
  the FIRST candidate that has a bound backend and delegates to it. If NO gated
  candidate is bound it raises ``NoAvailableTierError`` (rendered as a 503) rather
  than serving from an out-of-gate tier — availability never bypasses the gate.
* **Verify-gated fallback (T009, wired):** an ``allow`` tier is streamed directly
  to the client (TTFT preserved); an ``allow-with-verify`` tier goes through
  :func:`~anvil_serving.router.fallback.route_with_fallback` (buffer → verify chain
  → commit-or-fallback). If all gated candidates fail verification,
  :class:`~anvil_serving.router.internal.NoAvailableTierError` is raised and
  rendered as a 503 — no partial local tokens may reach the client.

Stdlib-only; binds ``127.0.0.1`` (never ``localhost`` — the documented Windows
IPv6 ~21s stall gotcha).
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
import threading
from http.server import ThreadingHTTPServer
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

# RelayBackend now lives in .backends.relay (#46); it is imported (and so
# re-exported from this module's namespace) here because build_backend_for_tier
# constructs it, and to keep the ``anvil_serving.router.serve.RelayBackend``
# import path stable for existing callers.
from .backends import CloudBackend, MissingCredentialError, RelayBackend
from .availability import (
    AlwaysAvailable,
    AvailabilityResult,
    HttpHealthAvailability,
)
from .admission import AdmissionLease, TierAdmission
from .audio import AudioGateway
from .backends.cloud import DiscoveryTransport, Transport, discover_single_model
from .config import (
    ConfigError,
    PRIVACY_CLOUD,
    PRIVACY_LOCAL,
    RouterConfig,
    Tier,
    load,
    load_server_config,
)
from .decision_log import AttemptRecord, DecisionLog, DecisionRecord, compute_cost_usd, request_correlation
from .dialects.translate import has_tool_artifacts
from .fallback import Budget, CircuitBreaker, RoutingDecision as _FallbackDecision, route_with_fallback
from .fingerprint import refresh_fingerprint
from .front_door import make_server
from .intent import PRESETS, resolve
from .internal import (
    Backend,
    InternalRequest,
    NoAvailableTierError,
    StructuredResult,
    estimate_tokens,
)
from .modes import (
    ENV_MODE,
    ENV_MODES_CONFIG,
    KNOWN_MODES,
    resolve_serve_config,
)


from .policy import Needs, route
from .profile_store import ProfileStore, default_profile
from .purpose import PurposeRouter
from .tier_health import build_tier_health
from .verify import (
    NonEmptyContent,
    NotTruncated,
    ResponseView,
    ToolCallContractValid,
    ToolCallJSONValid,
    default_verifiers,
)


class _AdmissionIterator:
    """Iterator that releases an eager admission lease even if never advanced."""

    def __init__(
        self,
        factory: Callable[[], Iterator[str]],
        lease: AdmissionLease,
        on_complete: Callable[[], None],
    ) -> None:
        self._factory = factory
        self._lease = lease
        self._on_complete = on_complete
        self._inner: Optional[Iterator[str]] = None
        self._closed = False

    def __iter__(self) -> "_AdmissionIterator":
        return self

    def __next__(self) -> str:
        if self._closed:
            raise StopIteration
        try:
            if self._inner is None:
                self._inner = iter(self._factory())
            return next(self._inner)
        except StopIteration:
            try:
                self._on_complete()
            finally:
                self.close()
            raise
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            closer = getattr(self._inner, "close", None)
            if callable(closer):
                closer()
        finally:
            self._lease.release()


def _needs_for(request: InternalRequest) -> Needs:
    """Build the hard-constraint :class:`~anvil_serving.router.policy.Needs` for
    one request (fx-sampling gap 2).

    ``needs_tools=True`` whenever the raw wire body carries tool structure —
    a tools-array, a tool_choice, or tool_use/tool_result history from a prior
    turn (:func:`~anvil_serving.router.dialects.translate.has_tool_artifacts`,
    #96) — so ``policy.route`` excludes any ``tool_support=false`` tier for
    that request, instead of only checking tool support at intent-classify time
    (``classify.py``'s ``bounded-edit`` inference is a WORK-CLASS signal, not a
    hard per-request constraint enforced against ``Tier.tool_support``).

    ``min_context`` is wired CONSERVATIVELY, so the hard-constraint filter in
    ``policy.route`` catches a GROSS over-context request (which would 400 at
    the model with "input exceeds maximum context length") without ever
    wrongly rejecting a request merely NEAR a tier's limit.

    The margin comes for free from what the estimator returns. The only stdlib
    estimator, :func:`~anvil_serving.router.internal.estimate_tokens`, counts
    whitespace-separated WORDS, which is a strict LOWER BOUND on the real token
    count: a tokenizer needs >= 1 token per whitespace word, and English runs
    ~1.3 tokens/word (dense code/JSON 2-4x more). So we set ``min_context`` to
    the RAW word count with NO extra discount: the filter then drops a tier
    ONLY when even this underestimate exceeds the tier's real-token
    ``context_limit``, which guarantees the real request genuinely does not fit.

    Concretely, dropping when ``words > limit`` means dropping only when real
    ``tokens > ~1.3 x limit`` — a built-in ~30%+ cushion. That catches the
    live incident (94218 tokens vs a 65536 tier ≈ 1.4x over: ~72k words > 65536
    -> dropped) while a request sitting AT a tier's limit (~50k words for 65536
    real tokens) stays well under and routes normally — no boundary
    false-reject. Deliberately NO ``* 0.85``-style discount on top: that would
    push the drop threshold to ~1.5x the limit and let the very incident this
    gate exists to catch (1.4x) slip through to a 400.

    Non-text blocks (images, tool_use/tool_result) are dropped by
    ``flatten_content`` before ``request.messages`` is built, so the word count
    only UNDER-counts the true payload here — which keeps the gate conservative
    (it never over-rejects), at the cost of missing a request whose bulk is in
    tool/image blocks (an acceptable trade for a gross-over-context floor).
    """
    raw = request.raw if isinstance(request.raw, Mapping) else {}
    # System + every message body; estimate_tokens sums per-string word counts.
    # OpenAI keeps the system message in `messages` (role="system") AND mirrors it into
    # `request.system`, while Anthropic keeps system ONLY in `request.system`. So add
    # `request.system` to the estimate ONLY when no system-role message is already in
    # `messages` — otherwise an OpenAI request double-counts the system prompt, inflating
    # min_context and risking a boundary false-reject (Copilot review).
    texts = [m.content for m in request.messages]
    if request.system and not any(getattr(m, "role", None) == "system" for m in request.messages):
        texts.append(request.system)
    return Needs(
        min_context=estimate_tokens(texts),
        needs_tools=has_tool_artifacts(raw),
    )


def _over_context_candidates(decision) -> Optional[Tuple[str, ...]]:
    """Return the context-dropped tiers when the routing result is empty PURELY
    because the request is over-context, else ``None``.

    This tells a genuinely un-fittable request ("no tier can hold this size" ->
    a clean 413) apart from every other empty-decision reason (quality deny,
    metered-cloud gate, unbound backends -> the existing 503 handoff). It fires
    only when ``policy.route`` returned NO tiers AND at least one was dropped for
    ``min_context`` AND nothing was dropped by the deny or metered gates. Given
    the filter ordering in ``policy.route`` (context -> metered -> deny), if the
    context filter already emptied the survivor set the later filters have
    nothing to drop, so those two lists being empty means context — not quality
    or billing — is what left the request unroutable.
    """
    notes = decision.notes
    if (
        not decision.tiers
        and notes.get("dropped_by_context")
        and not notes.get("dropped_by_deny")
        and not notes.get("dropped_by_metered_gate")
    ):
        return tuple(notes["dropped_by_context"])
    return None


# --------------------------------------------------------------------------- #
# Public-bind safety helpers
# --------------------------------------------------------------------------- #
#: Explicit wildcard-bind strings that ``ipaddress.ip_address()`` does not parse.
_WILDCARD_HOSTS = {"", "0.0.0.0", "::"}


def _warn_if_public_bind(host: str, *, authed: bool = False) -> None:
    """Emit a prominent warning to stderr when ``host`` is NOT loopback.

    With no front-door auth configured, a non-loopback bind exposes the front
    door on the network with NO authentication — any peer can spend the
    operator's cloud credentials and read or inject prompts. With
    ``[server].auth_env`` configured (``authed=True``), the token gate stands
    but the exposure is still worth a (softer) note. The server starts
    regardless (hard-failing is a separate UX decision); the warning is the
    safety net.

    ``""``, ``"0.0.0.0"``, and ``"::"`` are treated as non-loopback (they
    bind all interfaces).  Non-numeric hostnames (e.g. DNS names) are also
    flagged — only a confirmed loopback IP address passes silently.
    """
    if host in _WILDCARD_HOSTS:
        _emit_public_bind_warning(host, authed)
        return
    try:
        if not ipaddress.ip_address(host).is_loopback:
            _emit_public_bind_warning(host, authed)
    except ValueError:
        # Non-numeric hostname — cannot confirm it is loopback.
        _emit_public_bind_warning(host, authed)


def _emit_public_bind_warning(host: str, authed: bool = False) -> None:
    if authed:
        print(
            f"\n[anvil-serving] NOTE: binding to {host!r} exposes the front door "
            f"on the network. Front-door token auth IS configured "
            f"([server].auth_env), so requests require the bearer token — keep "
            f"that token secret, prefer TLS/a private network in front, and see "
            f"SECURITY.md for the threat model.\n",
            file=sys.stderr,
            flush=True,
        )
        return
    print(
        f"\n[anvil-serving] WARNING: binding to {host!r} exposes the front door "
        f"on the network with NO authentication.\n"
        f"  Any peer can send requests that spend your cloud credentials and "
        f"read or inject prompts.\n"
        f"  Set --host 127.0.0.1 (the default), or configure [server].auth_env "
        f"to require a token, unless you have placed your own authentication "
        f"layer in front of this server.\n",
        file=sys.stderr,
        flush=True,
    )


# --------------------------------------------------------------------------- #
# Tier -> Backend
# --------------------------------------------------------------------------- #
def build_backend_for_tier(
    tier: Tier,
    *,
    env: Optional[Mapping[str, str]] = None,
    transport: Optional[Transport] = None,
    timeout: float = 120.0,
    model_discovery_transport: Optional[DiscoveryTransport] = None,
) -> Backend:
    """Build the :class:`~anvil_serving.router.internal.Backend` for one tier.

    * ``privacy == "cloud"`` -> :class:`CloudBackend` (creds from ``auth_env`` at
      construction; raises :class:`MissingCredentialError` if unset).
    * otherwise (``local``) -> :class:`RelayBackend` (urllib relay to ``base_url``;
      auth optional).

    **genericity:T002** — a ``local`` tier with no explicit ``model=`` is first
    run through :func:`~anvil_serving.router.backends.cloud.discover_single_model`,
    which probes ``GET {base_url}/v1/models`` and adopts the single advertised
    id (raising :class:`ConfigError` on an ambiguous 0/>1 catalog; a network
    failure is non-fatal and leaves ``model`` unset). A tier that already sets
    ``model=`` skips the probe entirely — explicit config always wins.
    """
    if tier.privacy == PRIVACY_LOCAL and tier.model is None:
        tier = discover_single_model(tier, transport=model_discovery_transport)
    if tier.privacy == PRIVACY_CLOUD:
        return CloudBackend(tier, env=env, transport=transport, timeout=timeout)
    return RelayBackend(tier, env=env, transport=transport, timeout=timeout)


def build_backends(
    config: RouterConfig,
    *,
    env: Optional[Mapping[str, str]] = None,
    transport: Optional[Transport] = None,
    model_discovery_transport: Optional[DiscoveryTransport] = None,
) -> Tuple[Dict[str, Backend], List[Tuple[str, str]]]:
    """Build one backend per configured tier.

    A cloud tier whose credential env var is unset is **skipped, not fatal**:
    its id + reason are returned in the second element so the caller can warn and
    still start the front door bound to the serviceable tiers. Returns
    ``(backends_by_tier_id, skipped)`` where ``skipped`` is ``[(tier_id, reason), ...]``.

    **genericity:T005** — a LOCAL tier's backend is built with
    ``config.relay_timeout`` as its transport timeout (default 20s, short so a
    hung/cold local serve fails fast to the next tier) instead of the 120s
    cloud-tuned default that :func:`build_backend_for_tier` otherwise applies. A
    cloud tier is unaffected — it keeps the 120s default.

    **flexibility:T007** — a tier that sets its own ``timeout`` overrides the
    global ``config.relay_timeout`` for THAT tier's backend (for a local OR a
    cloud tier). Absent (the default for every existing config) preserves the
    T005 behaviour exactly: local tiers use ``config.relay_timeout``, cloud tiers
    keep :func:`build_backend_for_tier`'s 120s default.

    ``model_discovery_transport`` is an injectable seam for the T002
    ``GET /v1/models`` auto-derive probe (hermetic tests only; production uses
    the real ``urllib`` GET).
    """
    backends: Dict[str, Backend] = {}
    skipped: List[Tuple[str, str]] = []
    for tier in config.tiers:
        try:
            kwargs: Dict[str, object] = {}
            # Per-tier `timeout` (flexibility:T007) overrides the global
            # relay_timeout for THIS tier's backend. Absent -> the T005 default:
            # a LOCAL tier uses config.relay_timeout; a CLOUD tier keeps
            # build_backend_for_tier's 120s cloud-tuned default.
            if tier.timeout is not None:
                kwargs["timeout"] = tier.timeout
            elif tier.privacy != PRIVACY_CLOUD:
                kwargs["timeout"] = config.relay_timeout
            backends[tier.id] = build_backend_for_tier(
                tier, env=env, transport=transport,
                model_discovery_transport=model_discovery_transport, **kwargs
            )
        except MissingCredentialError as e:
            # Cloud tier with no key: don't crash the whole server — bind the
            # rest and record why this one isn't routable.
            skipped.append((tier.id, str(e)))
    return backends, skipped


# --------------------------------------------------------------------------- #
# Per-tier concurrency cap (flexibility:T009 / ADR-0010 Phase 3)
# --------------------------------------------------------------------------- #
class _ConcurrencyLimitedBackend:
    """Bound the number of in-flight requests to ONE tier (flexibility:T009).

    Wraps that tier's :class:`~anvil_serving.router.internal.Backend` with a
    per-tier :class:`threading.BoundedSemaphore(max_concurrency)`. A slot is held
    for the FULL lifetime of each ``generate()`` stream — acquired when the
    returned iterator is first advanced, released when it is exhausted or closed
    (client disconnect, or the fallback buffer completing). Excess concurrent
    requests to THIS tier BLOCK on ``acquire`` until a slot frees, serialising
    them behind the cap rather than rejecting them — the right behaviour for a
    low-throughput specialized-engine tier that must not be overrun (ADR-0010
    Phase 3), distinct from the front door's process-global ``acquire(blocking=
    False)`` -> 503 admission limiter.

    Only a tier whose config sets ``max_concurrency`` is wrapped; every other
    tier keeps its bare backend, so this cap is strictly PER-TIER and never
    touches the process-global front-door limiter (``front_door.py``).

    Implements the :class:`~anvil_serving.router.internal.Backend` protocol
    (``generate``) and transparently delegates the optional structured-result
    side channel (``get_last_structured``) to the wrapped backend so the dialect
    layer still renders the real ``finish_reason`` / ``tool_calls`` (#42 / #52).
    """

    def __init__(self, inner: Backend, max_concurrency: int) -> None:
        self._inner = inner
        self._sem = threading.BoundedSemaphore(max_concurrency)
        self.max_concurrency = max_concurrency

    def generate(self, request: InternalRequest) -> Iterator[str]:
        # Acquire/release INSIDE the generator so they are strictly paired with
        # the stream's lifecycle: the slot is taken on the first advance and
        # always returned on exhaustion OR on close (GeneratorExit runs the
        # finally). A generator that is created but never advanced nor closed
        # therefore never acquires a slot — no leak, no under-count. Both dispatch
        # paths consume it immediately: the allow path streams it through the
        # front door (which also closes it on disconnect), and the fallback path
        # buffers it via _cap_drain — so a slot is held for exactly the tier's
        # real in-flight window.
        def _guarded() -> Iterator[str]:
            self._sem.acquire()
            try:
                yield from self._inner.generate(request)
            finally:
                self._sem.release()

        return _guarded()

    def get_last_structured(self) -> Optional[StructuredResult]:
        fn = getattr(self._inner, "get_last_structured", None)
        return fn() if callable(fn) else None


# --------------------------------------------------------------------------- #
# Routing composition (intent T003 + policy T005)
# --------------------------------------------------------------------------- #
class RoutingBackend:
    """Pick ONE tier per request (intent + policy) and delegate to its backend.

    Implements the :class:`~anvil_serving.router.internal.Backend` protocol so it
    drops straight into the front door. Per request:

    1. :func:`~anvil_serving.router.intent.resolve` -> the intent and its
       config-derived candidate pool;
    2. :func:`~anvil_serving.router.policy.route` -> an ordered, quality-gated
       tier list (the eval gate, e.g. ``planning`` never routes to a local tier);
    3. pick the FIRST tier in that gated list that ALSO has a bound backend —
       skipping, e.g., a cloud candidate we could not credential.

    **The quality gate is never bypassed by availability.** If NONE of the gated
    candidates is bound (e.g. ``planning -> ["cloud"]`` but ``ANTHROPIC_API_KEY``
    is unset, the default dev-machine state, so cloud was skipped at startup),
    selection does NOT fall back to some other bound-but-out-of-gate tier — that
    would silently serve a planning request from an eval-proven-unfit local tier.
    Instead it raises :class:`~anvil_serving.router.internal.NoAvailableTierError`
    (and logs one stderr line); the front door renders a clean 503. The principle:
    availability must never override the quality gate.

    Both ``resolve`` and ``route`` are never-raise public APIs, so the SELECTION
    is a thin composition rather than a re-implementation of routing.

    **Verify-gated fallback (T009, fully wired):** :meth:`generate` distinguishes
    ``allow`` tiers (streamed directly, TTFT preserved) from ``allow-with-verify``
    tiers (fully buffered, then run through the structural verifier chain via
    :func:`~anvil_serving.router.fallback.route_with_fallback`; on pass the
    buffered response is committed; on fail the next candidate is tried). If every
    gated bound candidate fails verify, ``NoAvailableTierError`` is raised before
    any byte reaches the client.
    """

    def __init__(
        self,
        config: RouterConfig,
        backends: Mapping[str, Backend],
        profile: ProfileStore,
        *,
        mode: Optional[str] = None,
        availability: Optional[object] = None,
        admission: Optional[TierAdmission] = None,
    ):
        self._config = config
        # Active serving mode (ADR-0011 / flexibility:T013): stamped onto every
        # DecisionRecord this backend emits (via route_with_fallback), so the SAME
        # model measured under the agentic vs flexibility config is a DISTINCT
        # measured identity. None (a --config boot with no mode) leaves records
        # exactly as pre-T013.
        self._mode = mode
        # flexibility:T009 (ADR-0010 Phase 3) — apply each tier's optional per-tier
        # concurrency cap. A tier that sets ``max_concurrency=N`` has its backend
        # wrapped in a _ConcurrencyLimitedBackend(N) so at most N of ITS requests
        # are in flight at once; every other tier keeps its bare backend, so the
        # cap is strictly per-tier and the process-global front-door limiter is
        # untouched. Absent on every existing config -> this is a no-op.
        self._backends: Dict[str, Backend] = self._apply_concurrency_caps(
            config, dict(backends)
        )
        self._profile = profile
        self._availability = (
            availability if availability is not None else AlwaysAvailable()
        )
        self._admission = admission or TierAdmission(
            tier.id for tier in config.tiers
        )
        self._decision_log = DecisionLog()
        # Session-scoped circuit breaker: owned here, shared across all requests,
        # thread-safe (ThreadingHTTPServer spawns one thread per connection).
        # Default cooldown = 60 s, threshold comes from the shared Budget below.
        self._circuit_breaker = CircuitBreaker()
        # Escalation guards are identical for every request (a frozen parameter
        # set, not per-request state) — construct once, share across requests.
        self._budget = Budget()
        # Per-thread structured-result store: set during generate() and read by the
        # dialect layer after the stream is drained to render real finish_reason /
        # tool_calls in the response body (#42 / #52).
        self._thread_local: threading.local = threading.local()
        # Residency tracking (AC3 anti-thrash): the local tier that last served.
        # policy.route() defers every OTHER local behind the resident one + cloud,
        # so an alternating fast/heavy workload does not swap the multiplexer on
        # every request. Guarded by a lock (ThreadingHTTPServer = concurrent
        # requests); read at route time, written when a LOCAL tier is selected.
        self._residency_lock = threading.Lock()
        self._resident_tier: Optional[str] = None
        self._local_tier_ids = frozenset(
            t.id for t in config.tiers if t.privacy == PRIVACY_LOCAL
        )

    @staticmethod
    def _apply_concurrency_caps(
        config: RouterConfig, backends: Dict[str, Backend]
    ) -> Dict[str, Backend]:
        """Wrap each tier's backend in a per-tier concurrency limiter when its
        config sets ``max_concurrency`` (flexibility:T009 / ADR-0010 Phase 3).

        A tier that does NOT set ``max_concurrency`` keeps its bare backend
        (same instance), so the cap applies ONLY to the tier that asked for it
        and no other tier's throughput is affected. A backend keyed by an id not
        in the config (it would never be selected by routing anyway) is left
        bare.
        """
        caps: Dict[str, Optional[int]] = {t.id: t.max_concurrency for t in config.tiers}
        wrapped: Dict[str, Backend] = {}
        for tier_id, backend in backends.items():
            cap = caps.get(tier_id)
            wrapped[tier_id] = (
                _ConcurrencyLimitedBackend(backend, cap) if cap is not None else backend
            )
        return wrapped

    def _residency(self) -> Optional[str]:
        """The local tier that last served (or ``None``) — thread-safe read."""
        with self._residency_lock:
            return self._resident_tier

    def _note_selected(self, tier_id: Optional[str]) -> None:
        """Record that a LOCAL tier was selected to serve (thread-safe).

        A cloud tier does not change residency: routing through cloud neither
        loads nor evicts a local model, so the last-known local resident stays
        the best anti-thrash signal.
        """
        if tier_id is None or tier_id not in self._local_tier_ids:
            return
        with self._residency_lock:
            self._resident_tier = tier_id

    def get_last_structured(self) -> Optional[StructuredResult]:
        """Return the structured fields from the last ``generate()`` on this thread.

        Thread-safe: ``threading.local`` isolates per-request state across concurrent
        connections.  Returns ``None`` until the first call or when no inner backend
        exposed structured fields.
        """
        return getattr(self._thread_local, "last_result", None)

    def response_model(self, requested_model: str) -> str:
        """Resolve the wire model for the current request-handler thread."""
        if not self._config.transparent_response_model:
            return requested_model
        served_tier = getattr(self._thread_local, "last_served_tier", None)
        return served_tier or requested_model

    def _tier_verdict(self, tier_id: str, work_class: Optional[str]) -> str:
        """Profile verdict for ``(tier_id, work_class)``: allow / allow-with-verify / deny.

        Mirrors the per-tier verdict lookup in ``policy.route`` (step 2) so that
        ``generate`` can distinguish trusted (``allow``) tiers — streamed directly
        — from fail-prone (``allow-with-verify``) tiers that must pass the
        structural verifier chain before any byte reaches the client.
        """
        try:
            is_cloud = self._config.tier(tier_id).privacy == PRIVACY_CLOUD
        except Exception:
            is_cloud = False
        # Delegate to profile_store.decision() for ALL work_class values, including
        # None (custom preset). decision() already handles the None-class default
        # ("allow" when no stored entry) AND applies the stale-allow -> allow-with-verify
        # downgrade (PR #48) for any stored entry, including (tier, None) ones.
        return self._profile.decision(tier_id, work_class, is_cloud=is_cloud)

    def _availability_snapshot(
        self, tier_ids: Sequence[str]
    ) -> Dict[str, AvailabilityResult]:
        """Resolve readiness once per request for a stable fallback walk.

        A faulty availability implementation fails closed for that tier and is
        represented by a content-free reason. The next configured candidate is
        still considered; availability can never bypass the quality gate.
        """
        snapshot: Dict[str, AvailabilityResult] = {}
        for tier_id in tier_ids:
            try:
                result = self._availability.check(self._config.tier(tier_id))
                if not isinstance(result, AvailabilityResult):
                    raise TypeError("non-AvailabilityResult")
                snapshot[tier_id] = result
            except Exception as exc:  # noqa: BLE001 - readiness failure isolates tier
                snapshot[tier_id] = AvailabilityResult(
                    False,
                    "unavailable",
                    f"availability_check_{type(exc).__name__}",
                )
        return snapshot

    def generate(self, request: InternalRequest) -> Iterator[str]:
        # Clear request-thread state before every route so a subsequent request
        # on a reused HTTP worker can never inherit the prior winning tier. The
        # requested routing token itself is never rewritten.
        self._thread_local.last_served_tier = None
        # Eagerly resolve intent + policy BEFORE returning any iterator so that
        # a routing failure raises here — the front door catches NoAvailableTierError
        # before committing a streaming 200 and answers a clean 503.
        intent = resolve(request, self._config)
        # Residency-aware routing (AC3 anti-thrash): pass the local tier that
        # last served so policy.route() defers swap-forcing non-resident locals
        # behind the resident local + cloud. An optimisation, not correctness:
        # with no residency yet (None) the config cost order is untouched.
        decision = route(intent, self._config, self._profile,
                         residency=self._residency(), needs=_needs_for(request))

        # Narrow to tiers for which we hold a live backend (preserve gate order).
        # The policy deny-filter already ran; what remains is allow / allow-with-verify.
        bound_tiers = tuple(tid for tid in decision.tiers if tid in self._backends)
        if not bound_tiers:
            # Over-context: the request exceeds every tier's context_limit, so
            # nothing can hold it. Refuse up front with a 413 (via kind=
            # "over_context") rather than forwarding to a too-small tier that
            # would 400 at the model ("input exceeds maximum context length").
            over_ctx = _over_context_candidates(decision)
            if over_ctx is not None:
                print(
                    f"[anvil-serving] over-context request for work_class="
                    f"{decision.work_class!r}: exceeds context_limit of every "
                    f"candidate tier {list(over_ctx)}; refusing (413)",
                    file=sys.stderr,
                    flush=True,
                )
                raise NoAvailableTierError(
                    decision.work_class, over_ctx, kind="over_context"
                )
            print(
                f"[anvil-serving] no bound tier for work_class="
                f"{decision.work_class!r}: gated candidates {list(decision.tiers)} "
                f"are unbound; refusing to bypass the quality gate",
                file=sys.stderr,
                flush=True,
            )
            raise NoAvailableTierError(decision.work_class, decision.tiers)

        work_class = decision.work_class
        availability = self._availability_snapshot(bound_tiers)
        available_tiers = tuple(
            tid for tid in bound_tiers if availability[tid].available
        )
        if not available_tiers:
            # Run the fallback recorder with the stable snapshot so the decision
            # log distinguishes readiness skips from backend/circuit failures.
            self._route_with_verify(
                request,
                bound_tiers,
                work_class,
                [NonEmptyContent(), NotTruncated()],
                availability,
            )
            raise NoAvailableTierError(
                work_class, bound_tiers, kind="unavailable"
            )

        first_verdict = self._tier_verdict(available_tiers[0], work_class)
        first_tier = self._config.tier(available_tiers[0])
        raw = request.raw if isinstance(request.raw, Mapping) else {}
        # Tool contracts are a correctness boundary, not an optional quality
        # heuristic.  Any request that explicitly declares a tool catalog or a
        # tool choice must pass through the commit window so an invented tool
        # name (or an ignored required/forbidden choice) can never reach the
        # caller.  This remains true when verify_local_min is disabled for
        # latency-sensitive voice traffic, and for trusted cloud tiers too.
        tool_contract_required = "tools" in raw or "tool_choice" in raw

        # genericity:T004 — a privacy=local tier under "allow" is normally the
        # most trusted case (streamed raw, below) but that also means it is the
        # ONE path with zero structural verification at all: an empty/truncated
        # local 200 (thinking-budget starvation, a serve mid-restart) would
        # otherwise reach the harness silently as a "successful" reply. Route it
        # through a MINIMAL commit-window (NonEmptyContent/NotTruncated only —
        # deliberately cheaper than the full allow-with-verify chain below) so
        # that failure mode escalates to the next candidate (or exhausts to
        # exhaustion_status) instead of being served. A cloud/remote "allow" tier
        # is unaffected — it keeps the raw passthrough (TTFT preserved). Operators
        # can opt out via [router].verify_local_min = false.
        min_verify_local_allow = (
            first_verdict == "allow"
            and first_tier.privacy == PRIVACY_LOCAL
            and self._config.verify_local_min
        )

        if (
            first_verdict == "allow"
            and not min_verify_local_allow
            and not tool_contract_required
        ):
            # Trusted tier: stream its deltas to the client directly.
            # No buffering, no verification — TTFT is preserved.
            #
            # Wrap in a thin generator so the inner backend's structured result
            # (finish_reason / tool_calls) is propagated to our thread-local AFTER
            # the stream is exhausted — the dialect layer reads it via
            # get_last_structured() to render the real stop reason / tool calls (#42).
            inner_backend = self._backends[available_tiers[0]]
            lease = self._admission.acquire(available_tiers[0])
            if lease is None:
                # Quiesce won the race after readiness was snapshotted.  Use
                # the explicit walk so this is logged as skipped-quiesced and
                # the next policy-approved ready tier can serve.
                result = self._route_with_verify(
                    request,
                    bound_tiers,
                    work_class,
                    [NonEmptyContent(), NotTruncated()],
                    availability,
                )
                if result.exhausted:
                    raise NoAvailableTierError(
                        work_class, bound_tiers, kind="unavailable"
                    )
                self._note_selected(result.served_tier)
                self._thread_local.last_served_tier = result.served_tier
                self._thread_local.last_result = result.structured
                return iter([result.text])
            # Selected at route time, before the backend call (AC3 residency).
            self._note_selected(available_tiers[0])
            self._thread_local.last_served_tier = available_tiers[0]
            self._thread_local.last_result = None  # cleared; set when stream finishes
            _fragments: List[str] = []

            def _complete_allow() -> None:
                _fn = getattr(inner_backend, "get_last_structured", None)
                structured = _fn() if callable(_fn) else None
                self._thread_local.last_result = structured
                correlation = request_correlation(request)
                # Historically a raw trusted-allow stream has no decision-log
                # record; adding it after a long stream changes chronological
                # ordering for existing operational consumers. Workbench asks
                # explicitly for durable lineage, so only correlated direct
                # streams receive this supplemental record.
                if not any(correlation.values()):
                    return
                text = "".join(_fragments)
                usage = getattr(structured, "usage", None) if structured is not None else None
                prompt_tokens = int(usage.get("input_tokens", 0)) if usage is not None else estimate_tokens([m.content for m in request.messages])
                completion_tokens = int(usage.get("output_tokens", 0)) if usage is not None else estimate_tokens([text])
                try:
                    cost_usd = compute_cost_usd(first_tier, prompt_tokens, completion_tokens)
                except Exception:  # noqa: BLE001 - observability cannot fail a response
                    cost_usd = 0.0
                record = DecisionRecord(
                    work_class=work_class,
                    requested_tiers=bound_tiers,
                    attempts=(AttemptRecord(available_tiers[0], True, "allow", prompt_tokens, completion_tokens, "served"),),
                    served_tier=available_tiers[0],
                    total_prompt_tokens=prompt_tokens,
                    total_completion_tokens=completion_tokens,
                    fell_back=False,
                    cost_usd=cost_usd,
                    mode=self._mode,
                    **correlation,
                )
                self._decision_log.record(record)

            def _generate_allow() -> Iterator[str]:
                for delta in inner_backend.generate(request):
                    _fragments.append(delta)
                    yield delta

            return _AdmissionIterator(
                _generate_allow, lease, _complete_allow
            )

        # allow-with-verify (full chain), a local "allow" under the T004
        # minimal-verify safety net, OR any trusted allow tier handling an
        # explicit tool contract (NonEmptyContent/NotTruncated plus the
        # request-derived tool checks added in _route_with_verify): all enforce
        # the commit-window guarantee — ZERO partial tokens may reach the client
        # on a verify-failure. route_with_fallback (T009) drives
        # the candidate walk over bound_tiers, buffering each tier's response
        # before deciding, exactly as stream_with_commit_window (T008) does for a
        # single tier, generalised across N candidates.
        verifiers = (
            default_verifiers()
            if first_verdict != "allow"
            else [NonEmptyContent(), NotTruncated()]
        )
        result = self._route_with_verify(
            request, bound_tiers, work_class, verifiers, availability
        )
        if result.exhausted:
            # Every gated, bound candidate failed verify (or was guarded out by the
            # budget / circuit-breaker).  Refuse to serve the last attempt's text
            # — it failed verification and must not reach the client. This is the
            # EXHAUSTED case (kind="exhausted", v0.7.1): the tiers WERE bound and
            # reachable — distinct from the bound_tiers-empty raise above, whose
            # "configure credentials/endpoint" message would be actively
            # misleading here (see internal.NoAvailableTierError docstring).
            raise NoAvailableTierError(work_class, bound_tiers, kind="exhausted")

        # The verify walk may have escalated past the first candidate: record
        # the tier that ACTUALLY served as the resident (AC3 residency).
        self._note_selected(result.served_tier)
        self._thread_local.last_served_tier = result.served_tier

        # Propagate the winning tier's structured fields to our thread-local so
        # the dialect layer can render real stop_reason / tool_calls (#42).
        self._thread_local.last_result = result.structured

        # Yield the committed response.  route_with_fallback fully buffered the
        # winner before returning; on any verify-FAIL path the local bytes were
        # discarded and only the winning tier's text is in result.text.
        return iter([result.text])

    def _route_with_verify(
        self,
        request: InternalRequest,
        bound_tiers: Tuple[str, ...],
        work_class: Optional[str],
        verifiers: Sequence,
        availability: Mapping[str, AvailabilityResult],
    ):
        """Buffer + run ``verifiers`` over ``bound_tiers`` via
        :func:`~anvil_serving.router.fallback.route_with_fallback`.

        Shared by the full allow-with-verify chain and the T004 minimal
        local-allow safety net — they differ only in which verifier list runs.
        #52 — injects a response_view_factory that reads finish_reason +
        tool_calls from the backend's thread-local so NotTruncated and
        ToolCallJSONValid fire on real upstream data (not just the joined text).
        """
        _last_backend: List[Optional[Backend]] = [None]

        def _tracking_backend_for(tier: Tier) -> Backend:
            b = self._backends[tier.id]
            _last_backend[0] = b
            return b

        def _structured_view_factory(
            deltas: Sequence[str], req: InternalRequest
        ) -> ResponseView:
            b = _last_backend[0]
            _fn = getattr(b, "get_last_structured", None) if b else None
            text = "".join(deltas)
            # Thread the CALLER's explicit token cap into the view so
            # NotTruncated (verify.py, v0.7.1) can tell "the model obeyed an
            # explicit caller cap" (compliance) apart from "the tier's own
            # default budget was hit" (genuine truncation) — request.max_tokens
            # is None unless the caller sent one (dialects/openai.py parses
            # max_tokens/max_completion_tokens; anthropic.py requires max_tokens).
            caller_cap = req.max_tokens
            if callable(_fn):
                s = _fn()
                if s is not None:
                    return ResponseView(
                        text=text,
                        finish_reason=s.finish_reason,
                        tool_calls=s.tool_calls,
                        caller_max_tokens=caller_cap,
                    )
            return ResponseView(text=text, caller_max_tokens=caller_cap)

        fb_decision = _FallbackDecision(tiers=bound_tiers, work_class=work_class)
        # Tool-call JSON validity alone cannot catch a model inventing a validly
        # encoded but unadvertised name (the OpenClaw incident emitted
        # ``open_file`` and bare ``functions``).  Bind the caller's actual tool
        # catalog/choice into every buffered verify walk, including the minimal
        # local-allow safety net, so a bad local call is discarded before the
        # harness sees it and the next quality-gated tier can be attempted.
        request_verifiers = []
        request_json_verifier = ToolCallJSONValid.from_request_raw(request.raw)
        request_contract_verifier = ToolCallContractValid.from_request_raw(request.raw)
        replaced_json_verifier = False
        for verifier in verifiers:
            if isinstance(verifier, ToolCallJSONValid):
                request_verifiers.extend(
                    (request_json_verifier, request_contract_verifier)
                )
                replaced_json_verifier = True
            else:
                request_verifiers.append(verifier)
        if not replaced_json_verifier:
            request_verifiers.extend(
                (request_json_verifier, request_contract_verifier)
            )
        return route_with_fallback(
            request,
            fb_decision,
            self._config,
            backend_for=_tracking_backend_for,
            verifiers=request_verifiers,
            budget=self._budget,
            log=self._decision_log,
            breaker=self._circuit_breaker,
            verifier_timeout=5.0,
            response_view_factory=_structured_view_factory,
            availability_for=lambda tier_id: availability[tier_id],
            admission_for=self._admission.acquire,
            mode=self._mode,
        )

    def tier_health(self) -> dict:
        """Return a live readiness snapshot for EVERY configured serve (#292).

        Surfaces the SAME cached availability state routing already tracks —
        covering chat ``llm`` tiers, purpose models, and audio routes, not only
        recently-routed ones — as content-free ``{id, role, status, last_check,
        latency_ms, reason}`` rows.  No serve host, URL, token, or model id ever
        appears; a ``reason`` is a bounded category, never a raw message.
        """
        return build_tier_health(self._config, self._availability)

    def transition_status(self, tier_id: Optional[str] = None) -> dict:
        """Return router-owned admission and readiness state."""
        tier_ids = (tier_id,) if tier_id is not None else tuple(
            tier.id for tier in self._config.tiers
        )
        rows = []
        for tid in tier_ids:
            tier = self._config.tier(tid)
            admission = self._admission.snapshot(tid).as_dict()
            readiness = self._availability.check(tier)
            rows.append({
                **admission,
                "ready": readiness.available,
                "readiness_state": readiness.state,
                "readiness_reason": readiness.reason,
                "expected_model": readiness.expected_model,
                "observed_model": readiness.observed_model,
            })
        return {"tiers": rows}

    def quiesce_tier(self, tier_id: str, reason: str = "promotion") -> dict:
        self._config.tier(tier_id)
        snapshot = self._admission.quiesce(tier_id, reason)
        invalidate = getattr(self._availability, "invalidate", None)
        if callable(invalidate):
            invalidate(tier_id)
        return snapshot.as_dict()

    def drain_tier(self, tier_id: str, timeout: float) -> dict:
        self._config.tier(tier_id)
        return self._admission.wait_for_drain(tier_id, timeout)

    def readmit_tier(self, tier_id: str) -> dict:
        tier = self._config.tier(tier_id)
        if not tier.model_identity or not tier.health_path or not tier.model:
            return {
                "readmitted": False,
                "reason": "identity_not_configured",
                "status": self.transition_status(tier_id),
            }
        invalidate = getattr(self._availability, "invalidate", None)
        if callable(invalidate):
            invalidate(tier_id)
        readiness = self._availability.check(tier)
        identity_verified = (
            readiness.available
            and readiness.expected_model == tier.model
            and readiness.observed_model == tier.model
        )
        if not identity_verified:
            return {
                "readmitted": False,
                "reason": (
                    readiness.reason
                    if not readiness.available
                    else "identity_not_verified"
                ),
                "status": self.transition_status(tier_id),
            }
        snapshot = self._admission.readmit(tier_id)
        return {
            "readmitted": True,
            "reason": "readiness_passed",
            "status": {"tiers": [{
                **snapshot.as_dict(),
                "ready": readiness.available,
                "readiness_state": readiness.state,
                "readiness_reason": readiness.reason,
                "expected_model": readiness.expected_model,
                "observed_model": readiness.observed_model,
            }]},
        }

    def decide(self, request: InternalRequest) -> dict:
        """Run the routing brain for *request* without serving (T007).

        Called by ``POST /v1/route``.  Runs the same ``intent.resolve`` +
        ``policy.route`` as :meth:`generate` but **never calls any tier
        backend** — it is the decision endpoint, not the serve path.

        Returns a dict with the T007 contract fields::

            {
                "tier":       "local" | "cloud",
                "model":      "<tier.model or tier.id>",
                "provider":   "<tier id>",
                "work_class": "<resolved work class or ''>",
                "reason":     "<source + quality-gate note>",
                "confidence": <float>,
                "session_id": "rte_<hex>",
            }

        Confidence derivation (deterministic, documented for the T007 wire
        contract):

        * 1.0 — ``declared-preset``: caller named a known routing token.
        * 0.9 — ``pinned``: caller named a concrete tier id.
        * 0.8 — ``inferred``, classifier confident.
        * 0.5 — ``inferred``, ambiguous (collapsed to the safer tier).

        Raises :class:`~anvil_serving.router.internal.NoAvailableTierError`
        when every quality-gated candidate is unbound — same semantics as
        :meth:`generate`; the front door renders a clean 503.
        """
        from .dialects import _new_id

        intent = resolve(request, self._config)
        # Residency passed read-only so /v1/route reflects the real anti-thrash
        # order; decide() never serves, so it never UPDATES residency.
        decision = route(intent, self._config, self._profile,
                         residency=self._residency(), needs=_needs_for(request))

        # Narrow to tiers that are both gated (allow / allow-with-verify) AND
        # have a live backend — mirrors generate()'s bound-tier logic exactly
        # so decide() is faithful to what the serve path would actually pick.
        bound_tiers = tuple(tid for tid in decision.tiers if tid in self._backends)
        if not bound_tiers:
            over_ctx = _over_context_candidates(decision)
            if over_ctx is not None:
                print(
                    f"[anvil-serving] /v1/route: over-context request for "
                    f"work_class={decision.work_class!r}: exceeds context_limit "
                    f"of every candidate tier {list(over_ctx)} (413)",
                    file=sys.stderr,
                    flush=True,
                )
                raise NoAvailableTierError(
                    decision.work_class, over_ctx, kind="over_context"
                )
            print(
                f"[anvil-serving] /v1/route: no bound tier for work_class="
                f"{decision.work_class!r}: gated candidates {list(decision.tiers)} "
                f"are unbound",
                file=sys.stderr,
                flush=True,
            )
            raise NoAvailableTierError(decision.work_class, decision.tiers)

        availability = self._availability_snapshot(bound_tiers)
        admission = {
            tid: self._admission.snapshot(tid) for tid in bound_tiers
        }
        available_tiers = tuple(
            tid for tid in bound_tiers
            if availability[tid].available and not admission[tid].quiesced
        )
        if not available_tiers:
            raise NoAvailableTierError(
                decision.work_class, bound_tiers, kind="unavailable"
            )

        top_tier_id = available_tiers[0]
        top_tier = self._config.tier(top_tier_id)

        # Confidence: deterministic scheme (see docstring).
        source = intent.source
        if source == "declared-preset":
            confidence: float = 1.0
        elif source == "pinned":
            confidence = 0.9
        elif not intent.ambiguous:
            confidence = 0.8
        else:
            confidence = 0.5

        # Reason: source label + quality-gate note from policy + denied tiers.
        notes = decision.notes
        quality_gate = str(notes.get("quality_gate", ""))
        denied = list(notes.get("dropped_by_deny", ()))
        if source == "declared-preset":
            src_label = f"preset={intent.preset!r}"
        elif source == "pinned":
            src_label = "pinned"
        else:  # inferred
            src_label = "inferred"
            if intent.ambiguous:
                src_label += " (ambiguous→safer tier)"
        reason_parts = [f"{src_label}; quality gate: {quality_gate}"]
        if denied:
            reason_parts.append(f"denied: {denied}")
        unavailable = [
            tid for tid in bound_tiers if not availability[tid].available
        ]
        if unavailable:
            reason_parts.append(f"unavailable: {unavailable}")
        quiesced = [
            tid for tid in bound_tiers if admission[tid].quiesced
        ]
        if quiesced:
            reason_parts.append(f"quiesced: {quiesced}")
        reason = "; ".join(reason_parts)

        return {
            "tier": top_tier.privacy,            # "local" | "cloud"
            "model": top_tier.model or top_tier_id,
            "provider": top_tier_id,
            "work_class": decision.work_class or "",
            "reason": reason,
            "confidence": confidence,
            "session_id": _new_id("rte_"),
        }


# --------------------------------------------------------------------------- #
# Server assembly + run
# --------------------------------------------------------------------------- #
def build_server(
    config_path: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    backends: Optional[Mapping[str, Backend]] = None,
    profile: Optional[ProfileStore] = None,
    env: Optional[Mapping[str, str]] = None,
    transport: Optional[Transport] = None,
    audio_transport: Optional[Callable[..., Any]] = None,
    timeout: Optional[float] = 120,
    mode: Optional[str] = None,
    availability: Optional[object] = None,
    admission: Optional[TierAdmission] = None,
) -> ThreadingHTTPServer:
    """Load the config and build (but do NOT start) the bound front-door server.

    Returns the un-started :class:`~http.server.ThreadingHTTPServer` so a caller
    (the CLI :func:`serve`, or a test) controls its lifecycle. Pass ``port=0``
    for an ephemeral port (read back from ``server.server_address[1]``).

    ``backends`` is an injectable seam: pass a ``{tier_id: Backend}`` mapping to
    bypass real backend construction (hermetic tests inject echo/static
    backends). When ``None``, backends are built from the config via
    :func:`build_backends` (cloud tiers missing creds are skipped with a stderr
    warning). The bound tier ids and routing backend are stashed on the returned
    server as ``anvil_tiers`` / ``anvil_routing`` for introspection.

    ``audio_transport`` is a separate test seam for configured normalized-audio
    routes. It deliberately does not reuse ``transport``: production audio
    transport enforces its own byte cap, no-proxy policy, and redirect rejection.

    **Front-door auth (ADR-0004 / T001):** the optional ``[server].auth_env``
    key is resolved to its secret value from ``env`` (``os.environ`` when
    ``env`` is ``None``) exactly ONCE here, then threaded into
    :func:`~anvil_serving.router.front_door.make_server` as ``auth_token`` --
    never re-read from the environment per request. ``[server]`` absent (or
    ``auth_env`` unset within it) means auth stays OFF, matching today's
    loopback default. If ``auth_env`` IS set but the named env var is unset
    or empty, this raises :class:`~anvil_serving.router.config.ConfigError`
    rather than silently starting with auth off -- a configured-but-unresolved
    auth_env is a misconfiguration, not an opt-out (mirrors the tiers'
    :class:`~anvil_serving.router.backends.MissingCredentialError` fail-fast
    stance, except auth is the server's own security boundary so it hard-fails
    instead of skipping).

    **Active mode (ADR-0011 / flexibility:T013):** ``mode`` is the resolved global
    serving mode (``agentic`` / ``flexibility``), known at boot from
    :func:`~anvil_serving.router.modes.resolve_serve_config`. It is folded into
    each tier's serve fingerprint (so the same model measured in a different mode
    goes stale + is re-measured) and stamped onto every :class:`DecisionRecord`
    this server emits. ``None`` (a ``--config`` boot, which bypasses the mode
    resolver) preserves the pre-T013 fingerprints and records exactly.
    """
    config = load(config_path)
    server_config = load_server_config(config_path)
    environ: Mapping[str, str] = os.environ if env is None else env
    auth_token: Optional[str] = None
    if server_config.auth_env:
        auth_token = environ.get(server_config.auth_env) or None
        if not auth_token:
            raise ConfigError(
                f"[server].auth_env names {server_config.auth_env!r} but it is "
                f"not set (or empty) in the environment; export it to the auth "
                f"secret, or remove [server].auth_env to run without front-door "
                f"auth"
            )
    if config.audio_routes and auth_token is None:
        raise ConfigError(
            "[[router.audio_routes]] require a resolved [server].auth_env; "
            "the normalized audio gateway is never available without bearer authentication"
        )

    backends_injected = backends is not None
    if backends is None:
        built, skipped = build_backends(config, env=env, transport=transport)
        for tid, reason in skipped:
            print(f"[anvil-serving] tier {tid!r} not bound: {reason}",
                  file=sys.stderr, flush=True)
        backends = built
    if not backends:
        raise ConfigError(
            "no serviceable tiers: every configured tier failed to build a "
            "backend (e.g. all cloud tiers are missing their credential env "
            "vars). Set the tier auth_env(s) or add a local tier."
        )

    if profile is None:
        if config.profile_path:
            # A configured profile is a routing contract: fail fast if it can't
            # be loaded rather than silently routing on seeds the operator asked
            # to replace (mirrors the auth_env fail-fast stance above).
            from .profile_bootstrap import load_profile_store

            try:
                profile = load_profile_store(config.profile_path)
            except Exception as e:
                raise ConfigError(
                    f"[router].profile_path {config.profile_path!r} could not "
                    f"be loaded ({type(e).__name__}: {e}); regenerate it with "
                    f"`python -m anvil_serving.router.profile_bootstrap --replay "
                    f"...` or remove profile_path to route on the built-in seed "
                    f"profile"
                ) from e
            print(
                f"[anvil-serving] quality profile loaded from "
                f"{config.profile_path}",
                file=sys.stderr,
                flush=True,
            )
        else:
            profile = default_profile()

    # flexibility:T002 (ADR-0009 phase 1) — stamp/refresh each tier's serve
    # fingerprint against the chosen profile BEFORE routing is composed. This
    # wakes the (otherwise dormant) staleness machinery on the existing profile
    # (seed OR loaded): for each tier, refresh_fingerprint computes a pure digest
    # over the tier's DECLARED config identity (model/engine/endpoint/dialect/
    # context/params/reasoning) and compares it to what the tier's profile rows
    # were last measured under. A row whose serve identity DRIFTED since it was
    # measured is marked stale (routing then distrusts it — decision() downgrades
    # a stale 'allow' to 'allow-with-verify' until it is re-measured). A row that
    # carries no fingerprint yet — the case for a freshly-loaded profile OR the
    # built-in seed — ADOPTS the current identity as its baseline and is NOT
    # marked stale (nothing was invalidated), so a seed-only deployment behaves
    # identically. No model/network call: this is a deterministic config-identity
    # digest, not a serve probe.
    # flexibility:T013 (ADR-0011 Phase 2) — fold the active serving MODE into each
    # tier's serve fingerprint, so the SAME model measured under the agentic vs
    # flexibility config is a DISTINCT measured identity: a row measured in one mode
    # goes stale (and is re-measured) when the tier is next served under the other.
    # mode is None on a --config boot (the mode system is bypassed) -> the digest is
    # byte-identical to pre-T013 and nothing extra goes stale.
    for tier in config.tiers:
        staled = refresh_fingerprint(profile, tier.id, tier, mode=mode)
        if staled:
            print(
                f"[anvil-serving] tier {tier.id!r} serve identity changed since "
                f"it was last measured; {len(staled)} profile row(s) marked stale "
                f"(distrusted until re-measured): {staled}",
                file=sys.stderr,
                flush=True,
            )

    if availability is None:
        # Hermetic/custom backends are an explicit replacement for the real
        # configured endpoints, so do not probe unrelated config URLs. Callers
        # that want readiness with injected backends can inject it explicitly.
        availability = (
            AlwaysAvailable()
            if backends_injected
            else HttpHealthAvailability(config, env=env)
        )
    routing = RoutingBackend(
        config, backends, profile, mode=mode, availability=availability,
        admission=admission,
    )

    # Purpose-model surfaces (gpu-reservations:T010 / ADR-0017 §7): when the
    # config declares [[router.purpose_models]], bind a PurposeRouter that
    # routes /v1/embeddings + /v1/rerank BY MODEL NAME to those serves. It
    # shares the RoutingBackend's decision log so purpose decisions appear in
    # the same audit trail (GET /v1/decisions). Absent -> None -> both paths
    # stay 404, exactly the pre-T010 front door.
    purpose: Optional[PurposeRouter] = None
    if config.purpose_models:
        purpose = PurposeRouter(
            config.purpose_models,
            env=env,
            transport=transport,
            default_timeout=config.relay_timeout,
            decision_log=routing._decision_log,
        )

    # One-shot STT/TTS uses an independent normalized gateway rather than the
    # chat policy pipeline or the realtime proxy.  It shares the metadata-only
    # decision log, has no provider fallback, and remains absent unless the
    # operator declares Dark-owned [[router.audio_routes]].
    audio: Optional[AudioGateway] = None
    if config.audio_routes:
        audio = AudioGateway(
            config.audio_routes,
            max_input_bytes=config.audio_max_input_bytes,
            max_output_bytes=config.audio_max_output_bytes,
            max_text_chars=config.audio_max_text_chars,
            max_concurrency=config.audio_max_concurrency,
            default_timeout=config.relay_timeout,
            env=env,
            transport=audio_transport,
            decision_log=routing._decision_log,
        )

    # Advertise the canonical intent vocabulary on GET /v1/models (T004): the
    # presets ARE the "models" a harness model picker addresses.
    # Pass exhaustion_status from config so the front door uses the operator-
    # configured keyless handoff signal (ADR-0001 §Mechanism, T004).
    httpd = make_server(host, port, routing, timeout=timeout, presets=PRESETS,
                        exhaustion_status=config.exhaustion_status,
                        auth_token=auth_token, purpose=purpose, audio=audio,
                        response_model_resolver=routing.response_model)
    # Stash what we bound for introspection (serve()'s banner + tests).
    httpd.anvil_tiers = tuple(backends.keys())  # type: ignore[attr-defined]
    httpd.anvil_routing = routing  # type: ignore[attr-defined]
    httpd.anvil_availability = availability  # type: ignore[attr-defined]
    httpd.anvil_admission = routing._admission  # type: ignore[attr-defined]
    httpd.anvil_purpose = purpose  # type: ignore[attr-defined]
    httpd.anvil_audio = audio  # type: ignore[attr-defined]
    return httpd


def serve(
    config_path: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    mode: Optional[str] = None,
) -> None:
    """Build and run the config-bound front door until interrupted (CLI entry).

    Loads ``config_path``, binds a backend per tier, composes intent+policy
    selection, and serves both wire dialects with SSE streaming. Blocks in
    ``serve_forever`` until ``KeyboardInterrupt``; tears the server down cleanly.

    ``mode`` (ADR-0011 / flexibility:T013) is the resolved active serving mode,
    threaded into :func:`build_server` so it enters the serve fingerprint +
    decision log; ``None`` (a ``--config`` boot) preserves pre-T013 behaviour.
    """
    try:
        _authed = bool(load_server_config(config_path).auth_env)
    except ConfigError:
        _authed = False  # build_server re-raises with the full context below
    _warn_if_public_bind(host, authed=_authed)
    httpd = build_server(config_path, host=host, port=port, mode=mode)
    actual_host, actual_port = httpd.server_address[:2]
    tiers = ", ".join(httpd.anvil_tiers) or "(none)"  # type: ignore[attr-defined]
    routes = "POST /v1/chat/completions, POST /v1/messages, GET /v1/models"
    if getattr(httpd, "anvil_purpose", None) is not None:
        routes += ", POST /v1/embeddings, POST /v1/rerank"
    audio = getattr(httpd, "anvil_audio", None)
    if audio is not None:
        routes += "".join(
            ", POST " + audio_path for audio_path in audio.paths
        )
    print(
        f"anvil-serving front door on http://{actual_host}:{actual_port}\n"
        f"  tiers bound: {tiers}\n"
        f"  routes: {routes}",
        flush=True,  # show the banner promptly even when stdout is redirected
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()


def main(argv: Optional[List[str]] = None) -> int:
    """``anvil-serving router run`` CLI: parse args and run :func:`serve`.

    Two ways to select the config to serve (ADR-0011 / flexibility:T012):

    * ``--config PATH`` — load that exact router config (unchanged, explicit path).
    * ``--mode agentic|flexibility`` — resolve the global MODE to its config file,
      so the operator never spells out a path. The active mode is chosen by
      precedence ``--mode > ANVIL_MODE env > [modes].active_mode > default``, and a
      mode maps to a config via ``ANVIL_CONFIG_<MODE> > [modes] manifest > built-in
      default`` (see :mod:`anvil_serving.router.modes`). Exactly ONE mode's tiers +
      presets are bound at startup.

    The two are mutually exclusive; bare ``serve`` with NO selector is a usage
    error (the router never silently boots a default).
    """
    ap = argparse.ArgumentParser(
        prog="anvil-serving router run",
        description=(
            "Start the protocol-standard front door bound to the tiers in a "
            "router config (config -> per-tier backends -> front door). Select the "
            "config with --config PATH, or the global --mode agentic|flexibility "
            "(ADR-0011); --mode resolves precedence --mode > ANVIL_MODE > "
            "[modes].active_mode > default, and maps a mode to a file via "
            "ANVIL_CONFIG_<MODE> > a [modes] manifest (ANVIL_MODES_CONFIG) > a "
            "built-in default."
        ),
    )
    selector = ap.add_mutually_exclusive_group()
    selector.add_argument(
        "--config",
        metavar="PATH",
        help=(
            "path to the router TOML config whose [router] block declares the "
            "tiers + presets (e.g. configs/example.toml). Loaded verbatim; "
            "bypasses the --mode/ANVIL_MODE resolver."
        ),
    )
    selector.add_argument(
        "--mode",
        choices=KNOWN_MODES,
        help=(
            "global mode of operation (ADR-0011): 'agentic' (SGLang cache-friendly "
            "agent tiers) or 'flexibility' (any-engine single-turn quality tiers). "
            "Resolves to that mode's config WITHOUT --config. Overridden by nothing; "
            "overrides ANVIL_MODE and a [modes].active_mode default. Point a mode at "
            "a specific file with ANVIL_CONFIG_AGENTIC / ANVIL_CONFIG_FLEXIBILITY, or "
            "a [modes] manifest via ANVIL_MODES_CONFIG."
        ),
    )
    ap.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "bind host (default 127.0.0.1; never use localhost -- it triggers a "
            "~21s IPv6 stall on Windows). WARNING: a non-loopback host "
            "(0.0.0.0, a LAN/public IP, etc.) exposes the front door with NO "
            "authentication -- any peer can spend your cloud credentials or "
            "inject prompts. Only use a non-loopback host if you have placed "
            "your own authentication layer in front of this server."
        ),
    )
    ap.add_argument(
        "--port", type=int, default=8000, help="bind port (default 8000)."
    )
    args = ap.parse_args(argv)
    env = os.environ

    # Bare `serve` with NO selector at all is a usage error: never silently boot a
    # default server. A selector is --config, --mode, ANVIL_MODE, or a [modes]
    # manifest (ANVIL_MODES_CONFIG) carrying an active_mode default.
    if not (
        (args.config or "").strip()
        or args.mode
        or (env.get(ENV_MODE) or "").strip()
        or (env.get(ENV_MODES_CONFIG) or "").strip()
    ):
        ap.error(
            "no config selected: pass --config PATH or --mode "
            f"{{{'|'.join(KNOWN_MODES)}}} (or set {ENV_MODE} / point "
            f"{ENV_MODES_CONFIG} at a [modes] manifest)"
        )

    try:
        config_path, mode = resolve_serve_config(
            config_flag=args.config, mode_flag=args.mode, env=env
        )
        if mode is not None:
            print(
                f"anvil-serving: mode={mode!r} -> config {config_path}",
                file=sys.stderr,
                flush=True,
            )
        # Thread the resolved mode (None for a --config boot) into the serve stack
        # so it enters the serve fingerprint + decision log (flexibility:T013).
        serve(config_path, host=args.host, port=args.port, mode=mode)
    except ConfigError as e:
        print(f"anvil-serving router run: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
