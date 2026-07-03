"""``anvil-serving serve`` — config -> per-tier backends -> front door (T012).

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
from typing import Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

# RelayBackend now lives in .backends.relay (#46); it is imported (and so
# re-exported from this module's namespace) here because build_backend_for_tier
# constructs it, and to keep the ``anvil_serving.router.serve.RelayBackend``
# import path stable for existing callers.
from .backends import CloudBackend, MissingCredentialError, RelayBackend
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
from .decision_log import DecisionLog
from .dialects.translate import has_tool_artifacts
from .fallback import Budget, CircuitBreaker, RoutingDecision as _FallbackDecision, route_with_fallback
from .fingerprint import refresh_fingerprint
from .front_door import make_server
from .intent import PRESETS, resolve
from .internal import Backend, InternalRequest, NoAvailableTierError, StructuredResult
from .modes import (
    ENV_MODE,
    ENV_MODES_CONFIG,
    KNOWN_MODES,
    resolve_serve_config,
)
from .policy import Needs, route
from .profile_store import ProfileStore, default_profile
from .verify import NonEmptyContent, NotTruncated, ResponseView, default_verifiers


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

    ``min_context`` is deliberately left at its default (0 = no constraint):
    the only estimator available (``internal.estimate_tokens``) is an explicit,
    documented word-count approximation, not a real tokenizer, and comparing it
    against a tier's real ``context_limit`` (a token count) would be an unsound
    apples-to-oranges gate that could wrongly admit or reject a tier near the
    boundary. Wiring a real per-request context estimate is a separate,
    bigger piece of work (a real tokenizer or a calibrated fudge factor).
    """
    raw = request.raw if isinstance(request.raw, Mapping) else {}
    return Needs(needs_tools=has_tool_artifacts(raw))


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

    def generate(self, request: InternalRequest) -> Iterator[str]:
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
            print(
                f"[anvil-serving] no bound tier for work_class="
                f"{decision.work_class!r}: gated candidates {list(decision.tiers)} "
                f"are unbound; refusing to bypass the quality gate",
                file=sys.stderr,
                flush=True,
            )
            raise NoAvailableTierError(decision.work_class, decision.tiers)

        work_class = decision.work_class
        first_verdict = self._tier_verdict(bound_tiers[0], work_class)
        first_tier = self._config.tier(bound_tiers[0])

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

        if first_verdict == "allow" and not min_verify_local_allow:
            # Trusted tier: stream its deltas to the client directly.
            # No buffering, no verification — TTFT is preserved.
            #
            # Wrap in a thin generator so the inner backend's structured result
            # (finish_reason / tool_calls) is propagated to our thread-local AFTER
            # the stream is exhausted — the dialect layer reads it via
            # get_last_structured() to render the real stop reason / tool calls (#42).
            inner_backend = self._backends[bound_tiers[0]]
            # Selected at route time, before the backend call (AC3 residency).
            self._note_selected(bound_tiers[0])
            self._thread_local.last_result = None  # cleared; set when stream finishes

            def _allow_wrap() -> Iterator[str]:
                yield from inner_backend.generate(request)
                _fn = getattr(inner_backend, "get_last_structured", None)
                self._thread_local.last_result = _fn() if callable(_fn) else None

            return _allow_wrap()

        # allow-with-verify (full chain), OR a local "allow" under the T004
        # minimal-verify safety net (NonEmptyContent/NotTruncated only): both
        # enforce the commit-window guarantee — ZERO partial local tokens may
        # reach the client on a verify-failure. route_with_fallback (T009) drives
        # the candidate walk over bound_tiers, buffering each tier's response
        # before deciding, exactly as stream_with_commit_window (T008) does for a
        # single tier, generalised across N candidates.
        verifiers = default_verifiers() if not min_verify_local_allow else [
            NonEmptyContent(), NotTruncated(),
        ]
        result = self._route_with_verify(request, bound_tiers, work_class, verifiers)
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
        return route_with_fallback(
            request,
            fb_decision,
            self._config,
            backend_for=_tracking_backend_for,
            verifiers=verifiers,
            budget=self._budget,
            log=self._decision_log,
            breaker=self._circuit_breaker,
            verifier_timeout=5.0,
            response_view_factory=_structured_view_factory,
            mode=self._mode,
        )

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
            print(
                f"[anvil-serving] /v1/route: no bound tier for work_class="
                f"{decision.work_class!r}: gated candidates {list(decision.tiers)} "
                f"are unbound",
                file=sys.stderr,
                flush=True,
            )
            raise NoAvailableTierError(decision.work_class, decision.tiers)

        top_tier_id = bound_tiers[0]
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
    timeout: Optional[float] = 120,
    mode: Optional[str] = None,
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

    routing = RoutingBackend(config, backends, profile, mode=mode)

    # Advertise the canonical intent vocabulary on GET /v1/models (T004): the
    # presets ARE the "models" a harness model picker addresses.
    # Pass exhaustion_status from config so the front door uses the operator-
    # configured keyless handoff signal (ADR-0001 §Mechanism, T004).
    httpd = make_server(host, port, routing, timeout=timeout, presets=PRESETS,
                        exhaustion_status=config.exhaustion_status,
                        auth_token=auth_token)
    # Stash what we bound for introspection (serve()'s banner + tests).
    httpd.anvil_tiers = tuple(backends.keys())  # type: ignore[attr-defined]
    httpd.anvil_routing = routing  # type: ignore[attr-defined]
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
    print(
        f"anvil-serving front door on http://{actual_host}:{actual_port}\n"
        f"  tiers bound: {tiers}\n"
        f"  routes: POST /v1/chat/completions, POST /v1/messages, GET /v1/models",
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
    """``anvil-serving serve`` CLI: parse args and run :func:`serve`.

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
        prog="anvil-serving serve",
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
        print(f"anvil-serving serve: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
