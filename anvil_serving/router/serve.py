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
import sys
from http.server import ThreadingHTTPServer
from typing import Dict, Iterator, List, Mapping, Optional, Tuple

from .backends import CloudBackend, MissingCredentialError
from .backends.cloud import _ANTHROPIC_VERSION, Transport
from .config import ConfigError, RouterConfig, Tier, load
from .decision_log import DecisionLog
from .fallback import Budget, RoutingDecision as _FallbackDecision, route_with_fallback
from .front_door import make_server
from .intent import PRESETS, resolve
from .internal import Backend, InternalRequest, NoAvailableTierError
from .policy import route
from .profile_store import ProfileStore, default_profile
from .verify import default_verifiers


# --------------------------------------------------------------------------- #
# Public-bind safety helpers
# --------------------------------------------------------------------------- #
#: Explicit wildcard-bind strings that ``ipaddress.ip_address()`` does not parse.
_WILDCARD_HOSTS = {"", "0.0.0.0", "::"}


def _warn_if_public_bind(host: str) -> None:
    """Emit a prominent warning to stderr when ``host`` is NOT loopback.

    A non-loopback bind exposes the front door on the network with NO
    authentication — any peer can spend the operator's cloud credentials and
    read or inject prompts.  The server starts regardless (hard-failing is a
    separate UX decision); the warning is the safety net.

    ``""``, ``"0.0.0.0"``, and ``"::"`` are treated as non-loopback (they
    bind all interfaces).  Non-numeric hostnames (e.g. DNS names) are also
    flagged — only a confirmed loopback IP address passes silently.
    """
    if host in _WILDCARD_HOSTS:
        _emit_public_bind_warning(host)
        return
    try:
        if not ipaddress.ip_address(host).is_loopback:
            _emit_public_bind_warning(host)
    except ValueError:
        # Non-numeric hostname — cannot confirm it is loopback.
        _emit_public_bind_warning(host)


def _emit_public_bind_warning(host: str) -> None:
    print(
        f"\n[anvil-serving] WARNING: binding to {host!r} exposes the front door "
        f"on the network with NO authentication.\n"
        f"  Any peer can send requests that spend your cloud credentials and "
        f"read or inject prompts.\n"
        f"  Set --host 127.0.0.1 (the default) unless you have placed your own "
        f"authentication layer in front of this server.\n",
        file=sys.stderr,
        flush=True,
    )


# --------------------------------------------------------------------------- #
# Tier -> Backend
# --------------------------------------------------------------------------- #
class RelayBackend(CloudBackend):
    """Relay an :class:`~anvil_serving.router.internal.InternalRequest` to a
    LOCAL tier's OpenAI/Anthropic-compatible endpoint.

    Reuses :class:`~anvil_serving.router.backends.cloud.CloudBackend`'s tested
    dialect machinery (``_endpoint`` / ``_build_body`` / ``_extract_text`` /
    ``generate``) by subclassing it, and changes only the credential policy:

    * It serves a ``privacy == "local"`` tier (CloudBackend refuses non-cloud
      tiers by design — it authenticates against a remote provider).
    * **Auth is optional.** A local vLLM/SGLang server usually needs none, so a
      missing ``auth_env`` is NOT fatal here (unlike CloudBackend). If the env
      var IS set we forward it (``Authorization: Bearer`` / ``x-api-key``); if
      not, the relay is unauthenticated.

    Construction delegates to ``CloudBackend.__init__`` with the private
    ``_require_key=False`` opt-out, so RelayBackend INHERITS the base's attribute
    set (``_tier`` / ``_key`` / ``_timeout`` / ``_transport``) and the env/transport
    resolution rather than hand-copying them — a future attribute added to
    ``CloudBackend.__init__`` carries over automatically. The only override is
    :meth:`_headers` (auth-optional).

    The cloud call is non-streaming upstream; the reply is split into deltas so
    the front door's streaming path stays genuinely multi-chunk (inherited).
    """

    def __init__(
        self,
        tier: Tier,
        *,
        env: Optional[Mapping[str, str]] = None,
        transport: Optional[Transport] = None,
        timeout: float = 120.0,
    ):
        # Relay mode: no credential requirement and no cloud-only privacy gate
        # (local tier). super() resolves the optional key from ``auth_env`` (may
        # be empty -> no auth header, see _headers) and the default transport.
        super().__init__(
            tier, env=env, transport=transport, timeout=timeout, _require_key=False
        )

    def _headers(self) -> Dict[str, str]:
        """Outbound headers; the auth header is included ONLY if a key resolved."""
        headers = {"Content-Type": "application/json"}
        if self._tier.dialect == "anthropic":
            headers["anthropic-version"] = _ANTHROPIC_VERSION
            if self._key:
                headers["x-api-key"] = self._key
        else:  # openai-compatible
            if self._key:
                headers["Authorization"] = f"Bearer {self._key}"
        return headers


def build_backend_for_tier(
    tier: Tier,
    *,
    env: Optional[Mapping[str, str]] = None,
    transport: Optional[Transport] = None,
    timeout: float = 120.0,
) -> Backend:
    """Build the :class:`~anvil_serving.router.internal.Backend` for one tier.

    * ``privacy == "cloud"`` -> :class:`CloudBackend` (creds from ``auth_env`` at
      construction; raises :class:`MissingCredentialError` if unset).
    * otherwise (``local``) -> :class:`RelayBackend` (urllib relay to ``base_url``;
      auth optional).
    """
    if tier.privacy == "cloud":
        return CloudBackend(tier, env=env, transport=transport, timeout=timeout)
    return RelayBackend(tier, env=env, transport=transport, timeout=timeout)


def build_backends(
    config: RouterConfig,
    *,
    env: Optional[Mapping[str, str]] = None,
    transport: Optional[Transport] = None,
) -> Tuple[Dict[str, Backend], List[Tuple[str, str]]]:
    """Build one backend per configured tier.

    A cloud tier whose credential env var is unset is **skipped, not fatal**:
    its id + reason are returned in the second element so the caller can warn and
    still start the front door bound to the serviceable tiers. Returns
    ``(backends_by_tier_id, skipped)`` where ``skipped`` is ``[(tier_id, reason), ...]``.
    """
    backends: Dict[str, Backend] = {}
    skipped: List[Tuple[str, str]] = []
    for tier in config.tiers:
        try:
            backends[tier.id] = build_backend_for_tier(
                tier, env=env, transport=transport
            )
        except MissingCredentialError as e:
            # Cloud tier with no key: don't crash the whole server — bind the
            # rest and record why this one isn't routable.
            skipped.append((tier.id, str(e)))
    return backends, skipped


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
    ):
        self._config = config
        self._backends: Dict[str, Backend] = dict(backends)
        self._profile = profile
        self._decision_log = DecisionLog()

    def _tier_verdict(self, tier_id: str, work_class: Optional[str]) -> str:
        """Profile verdict for ``(tier_id, work_class)``: allow / allow-with-verify / deny.

        Mirrors the per-tier verdict lookup in ``policy.route`` (step 2) so that
        ``generate`` can distinguish trusted (``allow``) tiers — streamed directly
        — from fail-prone (``allow-with-verify``) tiers that must pass the
        structural verifier chain before any byte reaches the client.
        """
        try:
            is_cloud = self._config.tier(tier_id).privacy == "cloud"
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
        decision = route(intent, self._config, self._profile)

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

        if first_verdict == "allow":
            # Trusted tier: stream its deltas to the client directly.
            # No buffering, no verification — TTFT is preserved.
            return self._backends[bound_tiers[0]].generate(request)

        # allow-with-verify: enforce the commit-window guarantee —
        # ZERO partial local tokens may reach the client on a verify-failure.
        #
        # route_with_fallback (T009) drives the candidate walk over bound_tiers:
        # it fully materialises each tier's response (list()) *before* running the
        # structural verifier chain (T007), then either commits the winner or
        # advances to the next candidate.  That buffer-then-decide cycle is the
        # same guarantee provided by stream_with_commit_window (T008), applied
        # generically across N candidates.  The decision is appended to
        # self._decision_log for transparency (T010 / AC2).
        fb_decision = _FallbackDecision(tiers=bound_tiers, work_class=work_class)
        result = route_with_fallback(
            request,
            fb_decision,
            self._config,
            backend_for=lambda tier: self._backends[tier.id],
            verifiers=default_verifiers(),
            budget=Budget(),
            log=self._decision_log,
        )
        if result.exhausted:
            # Every gated, bound candidate failed verify (or was guarded out by the
            # budget / circuit-breaker).  Refuse to serve the last attempt's text
            # — it failed verification and must not reach the client.
            raise NoAvailableTierError(work_class, bound_tiers)

        # Yield the committed response.  route_with_fallback fully buffered the
        # winner before returning; on any verify-FAIL path the local bytes were
        # discarded and only the winning tier's text is in result.text.
        return iter([result.text])

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
        decision = route(intent, self._config, self._profile)

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
    """
    config = load(config_path)

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
        profile = default_profile()

    routing = RoutingBackend(config, backends, profile)

    # Advertise the canonical intent vocabulary on GET /v1/models (T004): the
    # presets ARE the "models" a harness model picker addresses.
    # Pass exhaustion_status from config so the front door uses the operator-
    # configured keyless handoff signal (ADR-0001 §Mechanism, T004).
    httpd = make_server(host, port, routing, timeout=timeout, presets=PRESETS,
                        exhaustion_status=config.exhaustion_status)
    # Stash what we bound for introspection (serve()'s banner + tests).
    httpd.anvil_tiers = tuple(backends.keys())  # type: ignore[attr-defined]
    httpd.anvil_routing = routing  # type: ignore[attr-defined]
    return httpd


def serve(config_path: str, *, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Build and run the config-bound front door until interrupted (CLI entry).

    Loads ``config_path``, binds a backend per tier, composes intent+policy
    selection, and serves both wire dialects with SSE streaming. Blocks in
    ``serve_forever`` until ``KeyboardInterrupt``; tears the server down cleanly.
    """
    _warn_if_public_bind(host)
    httpd = build_server(config_path, host=host, port=port)
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
    """``anvil-serving serve`` CLI: parse args and run :func:`serve`."""
    ap = argparse.ArgumentParser(
        prog="anvil-serving serve",
        description=(
            "Start the protocol-standard front door bound to the tiers in a "
            "router config (config -> per-tier backends -> front door)."
        ),
    )
    ap.add_argument(
        "--config",
        required=True,
        metavar="PATH",
        help=(
            "path to the router TOML config whose [router] block declares the "
            "tiers + presets (e.g. configs/example.toml)."
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
    try:
        serve(args.config, host=args.host, port=args.port)
    except ConfigError as e:
        print(f"anvil-serving serve: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
