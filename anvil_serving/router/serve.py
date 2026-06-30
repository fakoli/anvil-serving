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

Routing scope — composed vs deferred (the T012 boundary):

* **Composed here:** per-request tier *selection*. ``resolve()`` then ``route()``
  yield an ordered, quality-gated candidate list; :class:`RoutingBackend` picks
  the FIRST candidate that has a bound backend and delegates to it. If NO gated
  candidate is bound it raises ``NoAvailableTierError`` (rendered as a 503) rather
  than serving from an out-of-gate tier — availability never bypasses the gate.
* **Deferred to T009:** verify-gated *fallback*. We commit to the first selected
  tier and do NOT retry the next candidate when a response fails verification.
  The retry loop plugs in where the ``# T009:`` comment marks
  :meth:`RoutingBackend.generate` — that is the only routing piece left out.

Stdlib-only; binds ``127.0.0.1`` (never ``localhost`` — the documented Windows
IPv6 ~21s stall gotcha).
"""

from __future__ import annotations

import argparse
import sys
from http.server import ThreadingHTTPServer
from typing import Dict, Iterator, List, Mapping, Optional, Tuple

from .backends import CloudBackend, MissingCredentialError
from .backends.cloud import _ANTHROPIC_VERSION, Transport
from .config import ConfigError, RouterConfig, Tier, load
from .front_door import make_server
from .intent import PRESETS, resolve
from .internal import Backend, InternalRequest, NoAvailableTierError
from .policy import route
from .profile_store import ProfileStore, default_profile


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

    DEFERRED to T009 — verify-gated FALLBACK: we commit to the first selected
    tier and do NOT retry the next candidate when a response fails verification.
    See the ``# T009:`` comment in :meth:`generate` for where that loop plugs in.
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

    def select_tier(self, request: InternalRequest) -> str:
        """Resolve + route ``request`` to a single BOUND, gate-allowed tier id.

        Raises :class:`~anvil_serving.router.internal.NoAvailableTierError` when
        every gated candidate is unbound — it never falls back to an out-of-gate
        tier (the quality gate must not be bypassed by availability).
        """
        intent = resolve(request, self._config)
        decision = route(intent, self._config, self._profile)
        for tid in decision.tiers:
            if tid in self._backends:
                return tid
        # Every gate-allowed candidate is unbound (or the gate denied all). Refuse
        # — do NOT serve from an out-of-gate tier just because it happens to be
        # bound. Fail loud + typed; the front door turns this into a 503.
        print(
            f"[anvil-serving] no bound tier for work_class="
            f"{decision.work_class!r}: gated candidates {list(decision.tiers)} "
            f"are unbound; refusing to bypass the quality gate",
            file=sys.stderr,
            flush=True,
        )
        raise NoAvailableTierError(decision.work_class, decision.tiers)

    def generate(self, request: InternalRequest) -> Iterator[str]:
        # Select EAGERLY (before returning the delegate's iterator) so a routing
        # failure surfaces at generate()-call time — the front door catches it
        # there, before committing a streaming 200, and answers a clean 503.
        tier_id = self.select_tier(request)  # may raise NoAvailableTierError
        backend = self._backends[tier_id]
        # T009: verify-gated fallback wires in HERE — wrap this delegation in a
        #       loop over the gated candidate list (stream_with_commit_window;
        #       on a FallbackEvent, advance to the next candidate tier). T012
        #       commits to the first selected tier only.
        return backend.generate(request)


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
    httpd = make_server(host, port, routing, timeout=timeout, presets=PRESETS)
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
            "~21s IPv6 stall on Windows)."
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
