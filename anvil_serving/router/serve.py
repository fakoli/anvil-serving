"""Direct capability gateway assembly and ``router run`` CLI.

Chat routing is intentionally small: a caller-selected alias in
``[router.model_routes]`` resolves to one local tier.  The gateway retains the
transport boundaries that matter in operation (authentication, dialect
translation, readiness, admission, SSE, transition control, and metadata-only
decision records) but has no classifier, profile, fallback, or cloud path.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import sys
import threading
from http.server import ThreadingHTTPServer
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Tuple

from .admission import AdmissionLease, TierAdmission
from .audio import AudioGateway
from .availability import AlwaysAvailable, AvailabilityResult, HttpHealthAvailability
from .backends import RelayBackend
from .backends.relay import DiscoveryTransport, Transport, discover_single_model
from .config import (
    ConfigError,
    PRIVACY_LOCAL,
    RouterConfig,
    Tier,
    load,
    load_server_config,
    normalize_model_alias,
)
from .decision_log import AttemptRecord, DecisionLog, DecisionRecord, request_correlation
from .dialects.translate import has_tool_artifacts
from .front_door import make_server
from .internal import Backend, InternalRequest, NoAvailableTierError, StructuredResult, estimate_tokens
from .purpose import PurposeRouter
from .tier_health import build_tier_health


class _AdmissionIterator:
    """Release a per-tier admission lease when a stream ends or is closed."""

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


_WILDCARD_HOSTS = {"", "0.0.0.0", "::"}


def _warn_if_public_bind(host: str, *, authed: bool = False) -> None:
    """Warn when the front door is exposed beyond loopback."""
    public = host in _WILDCARD_HOSTS
    if not public:
        try:
            public = not ipaddress.ip_address(host).is_loopback
        except ValueError:
            public = True
    if not public:
        return
    if authed:
        message = (
            f"\n[anvil-serving] NOTE: binding to {host!r} exposes the front door "
            "on the network; bearer authentication is configured. Prefer TLS or "
            "a private network in front of the gateway.\n"
        )
    else:
        message = (
            f"\n[anvil-serving] WARNING: binding to {host!r} exposes the front "
            "door on the network with no authentication. Use 127.0.0.1 or "
            "configure [server].auth_env.\n"
        )
    print(message, file=sys.stderr, flush=True)


def build_backend_for_tier(
    tier: Tier,
    *,
    env: Optional[Mapping[str, str]] = None,
    transport: Optional[Transport] = None,
    timeout: float = 120.0,
    model_discovery_transport: Optional[DiscoveryTransport] = None,
) -> Backend:
    """Build the relay for one configured local chat tier."""
    if tier.privacy != PRIVACY_LOCAL:
        raise ConfigError(
            f"direct model route tier {tier.id!r} must have privacy='local'"
        )
    if tier.model is None:
        tier = discover_single_model(tier, transport=model_discovery_transport)
    return RelayBackend(tier, env=env, transport=transport, timeout=timeout)


def build_backends(
    config: RouterConfig,
    *,
    env: Optional[Mapping[str, str]] = None,
    transport: Optional[Transport] = None,
    model_discovery_transport: Optional[DiscoveryTransport] = None,
) -> Tuple[Dict[str, Backend], List[Tuple[str, str]]]:
    """Build every configured direct chat-tier relay.

    The second tuple member is retained for the CLI's stable introspection
    shape; a direct-local configuration has no credential-skipped cloud tier.
    """
    backends: Dict[str, Backend] = {}
    for tier in config.tiers:
        timeout = tier.timeout if tier.timeout is not None else config.relay_timeout
        backends[tier.id] = build_backend_for_tier(
            tier,
            env=env,
            transport=transport,
            timeout=timeout,
            model_discovery_transport=model_discovery_transport,
        )
    return backends, []


class _ConcurrencyLimitedBackend:
    """Apply an optional configured in-flight cap to one direct tier."""

    def __init__(self, inner: Backend, max_concurrency: int) -> None:
        self._inner = inner
        self._sem = threading.BoundedSemaphore(max_concurrency)

    def generate(self, request: InternalRequest) -> Iterator[str]:
        def guarded() -> Iterator[str]:
            self._sem.acquire()
            try:
                yield from self._inner.generate(request)
            finally:
                self._sem.release()
        return guarded()

    def get_last_structured(self) -> Optional[StructuredResult]:
        fn = getattr(self._inner, "get_last_structured", None)
        return fn() if callable(fn) else None


class RoutingBackend:
    """Resolve one configured capability alias and relay to its one tier."""

    def __init__(
        self,
        config: RouterConfig,
        backends: Mapping[str, Backend],
        *,
        availability: Optional[object] = None,
        admission: Optional[TierAdmission] = None,
    ) -> None:
        self._config = config
        self._backends: Dict[str, Backend] = {
            tier_id: (
                _ConcurrencyLimitedBackend(backend, config.tier(tier_id).max_concurrency)
                if config.tier(tier_id).max_concurrency is not None
                else backend
            )
            for tier_id, backend in backends.items()
        }
        self._availability = availability if availability is not None else AlwaysAvailable()
        self._admission = admission or TierAdmission(tier.id for tier in config.tiers)
        self._decision_log = DecisionLog()
        self._thread_local: threading.local = threading.local()

    def get_last_structured(self) -> Optional[StructuredResult]:
        return getattr(self._thread_local, "last_result", None)

    def _availability_for(self, tier: Tier) -> AvailabilityResult:
        try:
            result = self._availability.check(tier)
            if not isinstance(result, AvailabilityResult):
                raise TypeError("non-AvailabilityResult")
            return result
        except Exception as exc:  # readiness failures are isolated to this tier
            return AvailabilityResult(False, "unavailable", f"availability_check_{type(exc).__name__}")

    @staticmethod
    def _prompt_tokens(request: InternalRequest) -> int:
        texts = [message.content for message in request.messages]
        if request.system and not any(message.role == "system" for message in request.messages):
            texts.append(request.system)
        return estimate_tokens(texts)

    def _record(
        self,
        request: InternalRequest,
        tier: Tier,
        *,
        served: bool,
        reason: str,
        completion_tokens: int = 0,
        outcome: str = "error",
    ) -> None:
        prompt_tokens = self._prompt_tokens(request)
        self._decision_log.record(DecisionRecord(
            kind="chat",
            requested_tier=tier.id,
            attempts=(AttemptRecord(
                tier.id, served, reason, prompt_tokens, completion_tokens, outcome,
            ),),
            served_tier=tier.id if served else None,
            total_prompt_tokens=prompt_tokens,
            total_completion_tokens=completion_tokens,
            route=normalize_model_alias(request.model),
            **request_correlation(request),
        ))

    def generate(self, request: InternalRequest) -> Iterator[str]:
        """Resolve once, check local constraints, then relay with no fallback."""
        self._thread_local.last_result = None
        self._thread_local.last_served_tier = None
        tier = self._config.route_tier(request.model)
        if tier is None:
            raise NoAvailableTierError(request.model, (), kind="unknown_model")

        if self._prompt_tokens(request) > tier.context_limit:
            self._record(request, tier, served=False, reason="over_context", outcome="skipped")
            raise NoAvailableTierError(request.model, (tier.id,), kind="over_context")
        if has_tool_artifacts(request.raw) and not tier.tool_support:
            self._record(request, tier, served=False, reason="tools_unsupported", outcome="skipped")
            raise NoAvailableTierError(request.model, (tier.id,), kind="unsupported_tools")
        backend = self._backends.get(tier.id)
        if backend is None:
            self._record(request, tier, served=False, reason="backend_unbound")
            raise NoAvailableTierError(request.model, (tier.id,))
        readiness = self._availability_for(tier)
        if not readiness.available:
            self._record(request, tier, served=False, reason="unavailable", outcome="skipped")
            raise NoAvailableTierError(request.model, (tier.id,), kind="unavailable")
        lease = self._admission.acquire(tier.id)
        if lease is None:
            self._record(request, tier, served=False, reason="quiesced", outcome="skipped")
            raise NoAvailableTierError(request.model, (tier.id,), kind="unavailable")

        fragments: List[str] = []

        def on_complete() -> None:
            structured_fn = getattr(backend, "get_last_structured", None)
            structured = structured_fn() if callable(structured_fn) else None
            self._thread_local.last_result = structured
            self._thread_local.last_served_tier = tier.id
            text = "".join(fragments)
            usage = getattr(structured, "usage", None) if structured is not None else None
            completion_tokens = (
                int(usage.get("output_tokens", 0))
                if isinstance(usage, Mapping) else estimate_tokens([text])
            )
            self._record(
                request, tier, served=True, reason="served",
                completion_tokens=completion_tokens, outcome="served",
            )

        def relay() -> Iterator[str]:
            try:
                for delta in backend.generate(request):
                    fragments.append(delta)
                    yield delta
            except GeneratorExit:
                self._record(request, tier, served=False, reason="client_disconnected")
                raise
            except BaseException as exc:
                self._record(request, tier, served=False, reason=f"backend_error_{type(exc).__name__}")
                raise

        return _AdmissionIterator(relay, lease, on_complete)

    def tier_health(self) -> dict:
        return build_tier_health(self._config, self._availability)

    def transition_status(self, tier_id: Optional[str] = None) -> dict:
        tier_ids = (tier_id,) if tier_id is not None else tuple(tier.id for tier in self._config.tiers)
        rows = []
        for tid in tier_ids:
            tier = self._config.tier(tid)
            admission = self._admission.snapshot(tid).as_dict()
            readiness = self._availability_for(tier)
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
            return {"readmitted": False, "reason": "identity_not_configured", "status": self.transition_status(tier_id)}
        invalidate = getattr(self._availability, "invalidate", None)
        if callable(invalidate):
            invalidate(tier_id)
        readiness = self._availability_for(tier)
        identity_verified = (
            readiness.available
            and readiness.expected_model == tier.model
            and readiness.observed_model == tier.model
        )
        if not identity_verified:
            return {
                "readmitted": False,
                "reason": readiness.reason if not readiness.available else "identity_not_verified",
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


def build_server(
    config_path: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    backends: Optional[Mapping[str, Backend]] = None,
    env: Optional[Mapping[str, str]] = None,
    transport: Optional[Transport] = None,
    audio_transport: Optional[Callable[..., Any]] = None,
    timeout: Optional[float] = 120,
    availability: Optional[object] = None,
    admission: Optional[TierAdmission] = None,
) -> ThreadingHTTPServer:
    """Load direct routes and build an un-started authenticated front door."""
    config = load(config_path)
    server_config = load_server_config(config_path)
    environ: Mapping[str, str] = os.environ if env is None else env
    auth_token: Optional[str] = None
    if server_config.auth_env:
        auth_token = environ.get(server_config.auth_env) or None
        if not auth_token:
            raise ConfigError(
                f"[server].auth_env names {server_config.auth_env!r} but it is not set (or empty)"
            )
    if config.audio_routes and auth_token is None:
        raise ConfigError(
            "[[router.audio_routes]] require a resolved [server].auth_env"
        )

    injected = backends is not None
    if backends is None:
        backends, _skipped = build_backends(config, env=env, transport=transport)
    if not backends:
        raise ConfigError("no serviceable direct model-route tiers")
    if availability is None:
        availability = AlwaysAvailable() if injected else HttpHealthAvailability(config, env=env)
    routing = RoutingBackend(config, backends, availability=availability, admission=admission)

    purpose: Optional[PurposeRouter] = None
    if config.purpose_models:
        purpose = PurposeRouter(
            config.purpose_models,
            env=env,
            transport=transport,
            default_timeout=config.relay_timeout,
            decision_log=routing._decision_log,
        )
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
    httpd = make_server(
        host, port, routing, timeout=timeout, model_routes=config.model_routes,
        exhaustion_status=config.exhaustion_status, auth_token=auth_token,
        purpose=purpose, audio=audio,
    )
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
) -> None:
    """Run a configured direct-capability gateway until interrupted."""
    try:
        authed = bool(load_server_config(config_path).auth_env)
    except ConfigError:
        authed = False
    _warn_if_public_bind(host, authed=authed)
    httpd = build_server(config_path, host=host, port=port)
    actual_host, actual_port = httpd.server_address[:2]
    routes = "POST /v1/chat/completions, POST /v1/messages, GET /v1/models"
    if getattr(httpd, "anvil_purpose", None) is not None:
        routes += ", POST /v1/embeddings, POST /v1/rerank"
    audio = getattr(httpd, "anvil_audio", None)
    if audio is not None:
        routes += "".join(", POST " + path for path in audio.paths)
    print(
        f"anvil-serving front door on http://{actual_host}:{actual_port}\n"
        f"  tiers bound: {', '.join(httpd.anvil_tiers) or '(none)'}\n"  # type: ignore[attr-defined]
        f"  routes: {routes}",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()


def main(argv: Optional[List[str]] = None) -> int:
    """Run ``anvil-serving router run`` with one explicit config."""
    ap = argparse.ArgumentParser(prog="anvil-serving router run")
    ap.add_argument("--config", required=True, metavar="PATH", help="direct-router TOML config")
    ap.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1; never localhost)")
    ap.add_argument("--port", type=int, default=8000, help="bind port (default 8000)")
    args = ap.parse_args(argv)
    try:
        serve(os.path.expanduser(args.config), host=args.host, port=args.port)
    except ConfigError as exc:
        print(f"anvil-serving router run: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
