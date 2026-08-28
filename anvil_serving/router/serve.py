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
import json
import os
import sys
import threading
import time
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Tuple

from .admission import AdmissionLease, TierAdmission
from .audio import AudioGateway
from .availability import (
    AlwaysAvailable,
    AvailabilityResult,
    HttpHealthAvailability,
    resolve_runtime_tier,
    safe_check,
)
from .backends import RelayBackend
from .backends.relay import DiscoveryTransport, Transport, discover_single_model
from .config import (
    ConfigError,
    CONTEXT_ADMISSION_UPSTREAM,
    METADATA_UPSTREAM,
    PRIVACY_LOCAL,
    RouterConfig,
    Tier,
    load,
    load_server_config,
    normalize_model_alias,
)
from .decision_log import (
    AttemptRecord,
    DecisionLog,
    DecisionLogWriter,
    DecisionRecord,
    request_correlation,
)
from .discovery import models_payload
from .dialects.translate import has_tool_artifacts
from .front_door import make_server
from .gateway import ProtocolGateway
from .internal import Backend, InternalRequest, NoAvailableTierError, StructuredResult, estimate_tokens
from .media_admission import evaluate_media_admission
from .model_capacity import (
    MetricsProvider,
    build_model_capacity,
    fetch_vllm_metrics,
)
from .model_metadata import (
    build_model_capabilities,
    build_model_fingerprints,
    build_router_status,
)
from .purpose import PurposeRouter
from .router_telemetry import (
    aggregate_stats,
    find_request,
    render_capacity_prometheus,
    render_process_prometheus,
    render_prometheus,
)
from .tier_health import build_tier_health
from .. import envfile
from .. import mcp as mcp_facade
from ..a2a.tasks import A2AMediaTasks
from ..control_plane.mcp.controller_client import remote_controller_request
from ..control_plane.mcp.errors import ToolError
from ..control_plane.mcp.protocol import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    PROTOCOL_VERSION_META_KEY,
)
from ..graceful import serve_until_signal
from ..media.artifacts import ArtifactStore
from ..media.cli import DEFAULT_REGISTRY
from ..media.comfyui import ComfyUIClient
from ..media.errors import MediaError
from ..media.jobs import MediaJobStore
from ..media.operations import MediaOperations
from ..media.worker import MediaArtifactCapture, MediaJobReconciler, MediaReconciliationLoop
from ..media.workflows import WorkflowRegistry
from ..paths import config_path as operator_config_path
from ..paths import first_existing


class _AdmissionIterator:
    """Release a per-tier admission lease when a stream ends or is closed."""

    def __init__(
        self,
        factory: Callable[[], Iterator[str]],
        lease: AdmissionLease,
        on_complete: Callable[[], None],
        *,
        resources: Tuple[object, ...] = (),
    ) -> None:
        self._factory = factory
        self._lease = lease
        self._on_complete = on_complete
        self._resources = resources
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
            try:
                for resource in self._resources:
                    closer = getattr(resource, "close", None)
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
    if tier.model is None and tier.metadata_source != METADATA_UPSTREAM:
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
        self._sem.acquire()
        try:
            inner = self._inner.generate(request)
        except BaseException:
            self._sem.release()
            raise

        def guarded() -> Iterator[str]:
            try:
                yield from inner
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
        capacity_metrics: Optional[MetricsProvider] = None,
        decision_log: Optional[DecisionLog] = None,
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
        self._capacity_metrics = capacity_metrics or fetch_vllm_metrics
        self._started_at = time.time()
        # `is None`, not truthiness: DecisionLog defines __len__, so an empty
        # (sink-enabled) log is falsy and `or` would silently replace it.
        self._decision_log = DecisionLog() if decision_log is None else decision_log
        self._thread_local: threading.local = threading.local()

    def get_last_structured(self) -> Optional[StructuredResult]:
        return getattr(self._thread_local, "last_result", None)

    def _availability_for(self, tier: Tier) -> AvailabilityResult:
        return safe_check(self._availability, tier)

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
        latency_ms: int = 0,
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
            latency_ms=max(0, int(latency_ms)),
            **request_correlation(request),
        ))

    @staticmethod
    def _apply_output_cap(request: InternalRequest, tier: Tier) -> None:
        """Apply an opt-in tier completion ceiling before any relay work."""
        cap = tier.max_output_tokens
        if cap is None:
            return
        requested = request.max_tokens
        if requested is None:
            request.max_tokens = cap
            return
        if isinstance(requested, bool) or not isinstance(requested, int):
            return
        if requested <= cap:
            return

        request.raw["_anvil_output_clamp"] = {
            "requested": requested,
            "applied": cap,
        }
        request.max_tokens = cap
        for key in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
            if key in request.raw:
                request.raw[key] = cap
        print(
            "[anvil] warning output limit clamped "
            f"route={normalize_model_alias(request.model)} tier={tier.id} "
            f"requested={requested} applied={cap}",
            file=sys.stderr,
            flush=True,
        )

    def generate(self, request: InternalRequest) -> Iterator[str]:
        """Resolve once, check local constraints, then relay with no fallback."""
        self._thread_local.last_result = None
        self._thread_local.last_served_tier = None
        started = time.monotonic()

        def _elapsed_ms() -> int:
            return max(0, int((time.monotonic() - started) * 1000))

        configured_tier = self._config.route_tier(request.model)
        if configured_tier is None:
            raise NoAvailableTierError(request.model, (), kind="unknown_model")

        tier = configured_tier
        readiness: Optional[AvailabilityResult] = None
        if tier.metadata_source == METADATA_UPSTREAM:
            readiness = self._availability_for(tier)
            effective_tier = resolve_runtime_tier(tier, readiness)
            if effective_tier is None:
                self._record(
                    request,
                    tier,
                    served=False,
                    reason="upstream_metadata_unavailable",
                    outcome="skipped",
                )
                raise NoAvailableTierError(
                    request.model, (tier.id,), kind="unavailable"
                )
            tier = effective_tier

        self._apply_output_cap(request, tier)

        if (
            tier.context_admission != CONTEXT_ADMISSION_UPSTREAM
            and self._prompt_tokens(request) > tier.context_limit
        ):
            self._record(request, tier, served=False, reason="over_context", outcome="skipped")
            raise NoAvailableTierError(request.model, (tier.id,), kind="over_context")
        media = evaluate_media_admission(
            tier.params,
            request.raw,
            prompt_tokens=self._prompt_tokens(request),
            context_limit=tier.context_limit,
        )
        if not media.allowed:
            reason = (
                "over_context"
                if media.reason == "context_limit"
                else "media_admission_%s" % media.reason
            )
            kind = "over_context" if media.reason == "context_limit" else "media_limit"
            self._record(request, tier, served=False, reason=reason, outcome="skipped")
            raise NoAvailableTierError(request.model, (tier.id,), kind=kind)
        if has_tool_artifacts(request.raw) and not tier.tool_support:
            self._record(request, tier, served=False, reason="tools_unsupported", outcome="skipped")
            raise NoAvailableTierError(request.model, (tier.id,), kind="unsupported_tools")
        backend = self._backends.get(tier.id)
        if backend is None:
            self._record(request, tier, served=False, reason="backend_unbound")
            raise NoAvailableTierError(request.model, (tier.id,))
        if readiness is None:
            readiness = self._availability_for(tier)
        if not readiness.available:
            self._record(request, tier, served=False, reason="unavailable", outcome="skipped")
            raise NoAvailableTierError(request.model, (tier.id,), kind="unavailable")
        lease = self._admission.acquire(tier.id)
        if lease is None:
            self._record(request, tier, served=False, reason="quiesced", outcome="skipped")
            raise NoAvailableTierError(request.model, (tier.id,), kind="unavailable")

        relay_request = request
        if configured_tier.metadata_source == METADATA_UPSTREAM:
            # Preserve the stable public alias on the original request and in
            # decision/response metadata. Only the private relay copy carries
            # the inference service's currently observed model id.
            relay_request = replace(request, model=tier.model)
        try:
            upstream = backend.generate(relay_request)
        except BaseException as exc:
            self._record(
                request,
                tier,
                served=False,
                reason=f"backend_error_{type(exc).__name__}",
                latency_ms=_elapsed_ms(),
            )
            lease.release()
            raise

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
                request,
                tier,
                served=True,
                reason=(
                    "served_output_clamped"
                    if "_anvil_output_clamp" in request.raw
                    else "served"
                ),
                completion_tokens=completion_tokens, outcome="served",
                latency_ms=_elapsed_ms(),
            )

        def relay() -> Iterator[str]:
            try:
                for delta in upstream:
                    fragments.append(delta)
                    yield delta
            except GeneratorExit:
                self._record(
                    request, tier, served=False, reason="client_disconnected",
                    latency_ms=_elapsed_ms(),
                )
                raise
            except BaseException as exc:
                self._record(
                    request, tier, served=False,
                    reason=f"backend_error_{type(exc).__name__}",
                    latency_ms=_elapsed_ms(),
                )
                raise

        return _AdmissionIterator(relay, lease, on_complete, resources=(upstream,))

    def tier_health(self) -> dict:
        return build_tier_health(self._config, self._availability)

    def model_capacity(self, query: Mapping[str, list[str]]) -> dict:
        return build_model_capacity(
            self._config,
            self._availability,
            self._capacity_metrics,
            query,
        )

    def model_discovery(self) -> dict:
        return models_payload(self._config, self._availability)

    def model_capabilities(self, query: Mapping[str, list[str]]) -> dict:
        return build_model_capabilities(self._config, self._availability, query)

    def model_fingerprints(self, query: Mapping[str, list[str]]) -> dict:
        return build_model_fingerprints(self._config, self._availability, query)

    def router_status(self) -> dict:
        return build_router_status(self._config, started_at=self._started_at)

    def _validate_stats_model(self, query: Mapping[str, list[str]]) -> None:
        values = query.get("model")
        if values is not None and len(values) == 1:
            if self._config.route_tier(values[0]) is None:
                raise KeyError(values[0])

    def router_stats(self, query: Mapping[str, list[str]]) -> dict:
        self._validate_stats_model(query)
        return aggregate_stats(self._decision_log.records, query)

    def request_trace(self, request_id: str) -> dict:
        return find_request(self._decision_log.records, request_id)

    def prometheus_metrics(self, query: Mapping[str, list[str]]) -> str:
        self._validate_stats_model(query)
        capacity_query = (
            {"model": query["model"]} if "model" in query else {}
        )
        capacity = self.model_capacity(capacity_query)
        return (
            render_prometheus(self._decision_log.records, query)
            + render_capacity_prometheus(capacity)
            + render_process_prometheus(self._started_at, self._decision_log.capacity)
        )

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
                "observed_context_limit": (
                    readiness.runtime_metadata.context_limit
                    if readiness.runtime_metadata is not None
                    else None
                ),
                "metadata_source": tier.metadata_source,
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
        dynamic_metadata = tier.metadata_source == METADATA_UPSTREAM
        if not dynamic_metadata and (
            not tier.model_identity or not tier.health_path or not tier.model
        ):
            return {"readmitted": False, "reason": "identity_not_configured", "status": self.transition_status(tier_id)}
        invalidate = getattr(self._availability, "invalidate", None)
        if callable(invalidate):
            invalidate(tier_id)
        readiness = self._availability_for(tier)
        identity_verified = (
            readiness.available
            and (
                resolve_runtime_tier(tier, readiness) is not None
                if dynamic_metadata
                else (
                    readiness.expected_model == tier.model
                    and readiness.observed_model == tier.model
                )
            )
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
                "observed_context_limit": (
                    readiness.runtime_metadata.context_limit
                    if readiness.runtime_metadata is not None
                    else None
                ),
                "metadata_source": tier.metadata_source,
            }]},
        }


def _load_admission_intent(path: str, tier_ids: tuple[str, ...]) -> dict[str, str]:
    """Read persisted quiesce intent: ``{tier_id: reason_code}`` (ADR-0033).

    A missing file means no intent. A corrupt file refuses to serve — silently
    dropping operator intent is the failure this feature exists to prevent.
    Tiers no longer in the config are skipped with a warning: quiesce state for
    a removed tier is meaningless after a topology change.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ConfigError(
            f"admission intent file {path!r} is unreadable or corrupt ({exc}); "
            f"fix or delete it before starting the router"
        ) from exc
    tiers = raw.get("tiers") if isinstance(raw, dict) else None
    if not isinstance(tiers, dict):
        raise ConfigError(
            f"admission intent file {path!r} must contain a 'tiers' object; "
            f"fix or delete it before starting the router"
        )
    intent: dict[str, str] = {}
    for tier_id, entry in tiers.items():
        state = entry.get("state") if isinstance(entry, dict) else None
        reason = entry.get("reason") if isinstance(entry, dict) else None
        if state != "quiesced":
            continue
        if tier_id not in tier_ids:
            print(
                f"[anvil] warning admission intent for unknown tier {tier_id!r} ignored",
                file=sys.stderr,
                flush=True,
            )
            continue
        # "promotion" quiescence belongs to the promotion transaction, whose
        # own router restart is the expected end of that quiescence and whose
        # crash recovery (`--resume`, ADR-0018) re-asserts it. Restoring it
        # here would leave every successful promotion's tier refusing traffic.
        if reason == "promotion":
            print(
                f"[anvil] admission intent for {tier_id!r} (promotion) not restored: "
                f"the promotion transaction owns that quiescence",
                file=sys.stderr,
                flush=True,
            )
            continue
        intent[tier_id] = reason if isinstance(reason, str) and reason else "restored"
    return intent


def _write_admission_intent(path: str, admission: TierAdmission) -> None:
    """Atomically persist the quiesced side of the admission state (ADR-0033).

    Only quiesced tiers are recorded; reasons are the bounded content-free
    codes admission already enforces. The file never records "admit" — after a
    restart, readmission always re-passes the health+identity gate.
    """
    payload = {
        "version": 1,
        "tiers": {
            snapshot.tier_id: {"state": "quiesced", "reason": snapshot.reason}
            for snapshot in admission.snapshots()
            if snapshot.quiesced
        },
    }
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def _durable_admission(path: str, config: RouterConfig) -> TierAdmission:
    """Build a TierAdmission that restores and persists quiesce intent."""
    tier_ids = tuple(tier.id for tier in config.tiers)
    intent = _load_admission_intent(path, tier_ids)
    holder: dict[str, TierAdmission] = {}

    def _persist(_tier_id: str) -> None:
        admission = holder.get("admission")
        if admission is None:
            return
        try:
            _write_admission_intent(path, admission)
        except OSError as exc:
            print(
                "[anvil] warning admission intent write failed: %s" % type(exc).__name__,
                file=sys.stderr,
                flush=True,
            )

    admission = TierAdmission(tier_ids, on_state_change=_persist)
    holder["admission"] = admission
    for tier_id, reason in intent.items():
        try:
            admission.quiesce(tier_id, reason)
        except ValueError:
            admission.quiesce(tier_id, "restored")
    # Probe writability now: a configured intent path that cannot be written
    # is a boot error, not a silent downgrade.
    try:
        _write_admission_intent(path, admission)
    except OSError as exc:
        raise ConfigError(
            f"admission intent path {path!r} is not writable: {exc}"
        ) from exc
    return admission


def _media_lifecycle_preview(
    controller_url: str,
    token: str,
) -> Callable[[str, str, str], Mapping[str, Any]]:
    """Build a bounded operator-controller preview used only for cold workers."""

    def preview(job_id: str, principal: str, service: str) -> Mapping[str, Any]:
        request = {
            "jsonrpc": "2.0",
            "id": "media-lifecycle-preview",
            "method": "tools/call",
            "params": {
                "name": "media_worker_prepare",
                "arguments": {
                    "job_id": job_id,
                    "principal": principal,
                    "service": service,
                    "dry_run": True,
                    "confirm": False,
                    "human_approved": False,
                },
                "_meta": {
                    PROTOCOL_VERSION_META_KEY: mcp_facade.PROTOCOL_VERSION,
                    CLIENT_CAPABILITIES_META_KEY: {},
                    CLIENT_INFO_META_KEY: {
                        "name": "anvil-media-gateway",
                        "version": mcp_facade.SERVER_INFO["version"],
                    },
                },
            },
        }
        try:
            response = remote_controller_request(controller_url, request, token)
        except ToolError as exc:
            raise MediaError(exc.code, exc.message, status=503, details=exc.details) from exc
        rpc_error = response.get("error")
        if isinstance(rpc_error, Mapping):
            data = rpc_error.get("data")
            code = data.get("code") if isinstance(data, Mapping) else None
            raise MediaError(
                code if isinstance(code, str) else "media_lifecycle_preview_failed",
                "the controller rejected the managed media worker preview",
                status=503,
            )
        result = response.get("result")
        envelope = result.get("structuredContent") if isinstance(result, Mapping) else None
        if not isinstance(envelope, Mapping) or envelope.get("ok") is not True:
            raise MediaError(
                "media_lifecycle_preview_invalid",
                "controller returned an invalid media worker preview envelope",
                status=502,
            )
        data = envelope.get("data")
        if not isinstance(data, Mapping):
            raise MediaError(
                "media_lifecycle_preview_invalid",
                "controller returned invalid media worker preview data",
                status=502,
            )
        return data

    return preview


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
    capacity_metrics: Optional[MetricsProvider] = None,
) -> ThreadingHTTPServer:
    """Load direct routes and build an un-started authenticated front door."""
    config = load(config_path)
    server_config = load_server_config(config_path)
    environ: Mapping[str, str] = os.environ if env is None else env
    auth_token: Optional[str] = None
    if server_config.auth_env:
        # ADR-0033: the process environment wins; the real environment (no
        # injected mapping) falls back to the operator dotenv chain so a
        # rebooted host can authenticate without a live shell export.
        auth_token, _source = envfile.resolve_env_value(
            server_config.auth_env, env=env
        )
        if not auth_token:
            raise ConfigError(
                f"[server].auth_env names {server_config.auth_env!r} but it is not set "
                f"(or empty); checked "
                + ", ".join(envfile.env_sources(server_config.auth_env))
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
    if capacity_metrics is None:
        def capacity_metrics(tier: Tier):
            return fetch_vllm_metrics(tier, env=environ)
    if admission is None and server_config.admission_state_path:
        admission = _durable_admission(server_config.admission_state_path, config)
    decision_log: Optional[DecisionLog] = None
    if server_config.decision_log_path:
        try:
            sink = DecisionLogWriter(server_config.decision_log_path)
        except (OSError, ValueError) as exc:
            raise ConfigError(
                f"[server].decision_log_path {server_config.decision_log_path!r} "
                f"is not writable: {exc}"
            ) from exc
        decision_log = DecisionLog(sink=sink)
    routing = RoutingBackend(
        config,
        backends,
        availability=availability,
        admission=admission,
        capacity_metrics=capacity_metrics,
        decision_log=decision_log,
    )

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
    gateway: Optional[ProtocolGateway] = None
    media_worker: Optional[MediaReconciliationLoop] = None
    if server_config.media_principal is not None:
        backend_url = environ.get("ANVIL_MEDIA_BACKEND_URL")
        if not backend_url:
            raise ConfigError(
                "media gateway is enabled but ANVIL_MEDIA_BACKEND_URL is not set"
            )
        state_path = environ.get(
            "ANVIL_MEDIA_STATE_DB",
            str(Path.home() / ".anvil-serving" / "media-jobs.sqlite3"),
        )
        artifact_root = environ.get(
            "ANVIL_MEDIA_ARTIFACT_ROOT",
            str(Path.home() / ".anvil-serving" / "media-artifacts"),
        )
        registry_path = environ.get(
            "ANVIL_MEDIA_WORKFLOW_REGISTRY", str(DEFAULT_REGISTRY)
        )
        controller_url = (environ.get("ANVIL_MEDIA_CONTROLLER_URL") or "").strip()
        controller_token = (environ.get("ANVIL_MEDIA_CONTROLLER_TOKEN") or "").strip()
        if bool(controller_url) != bool(controller_token):
            raise ConfigError(
                "ANVIL_MEDIA_CONTROLLER_URL and ANVIL_MEDIA_CONTROLLER_TOKEN must be configured together"
            )
        operations = MediaOperations(
            WorkflowRegistry(registry_path),
            MediaJobStore(state_path),
            ArtifactStore(artifact_root),
            lifecycle_preview=(
                _media_lifecycle_preview(controller_url, controller_token)
                if controller_url
                else None
            ),
        )
        media_backend = ComfyUIClient(backend_url)
        gateway = ProtocolGateway(
            caller={
                "principal": server_config.media_principal,
                "scopes": server_config.media_scopes,
            },
            tasks=A2AMediaTasks(operations, media_backend),
            registry=operations.registry,
            artifacts=operations.artifacts,
            public_origin=server_config.media_public_origin or "",
        )
        media_worker = MediaReconciliationLoop(
            MediaJobReconciler(
                operations.jobs,
                media_backend.history,
                MediaArtifactCapture(
                    operations.registry,
                    operations.artifacts,
                    media_backend,
                ),
                getattr(media_backend, "find_prompt", None),
            ),
            maintenance=operations.artifacts.prune,
        )
    httpd = make_server(
        host, port, routing, timeout=timeout, model_routes=config.model_routes,
        exhaustion_status=config.exhaustion_status, auth_token=auth_token,
        purpose=purpose, audio=audio, gateway=gateway,
    )
    httpd.anvil_tiers = tuple(backends.keys())  # type: ignore[attr-defined]
    httpd.anvil_routing = routing  # type: ignore[attr-defined]
    httpd.anvil_availability = availability  # type: ignore[attr-defined]
    httpd.anvil_admission = routing._admission  # type: ignore[attr-defined]
    httpd.anvil_purpose = purpose  # type: ignore[attr-defined]
    httpd.anvil_audio = audio  # type: ignore[attr-defined]
    httpd.anvil_gateway = gateway  # type: ignore[attr-defined]
    httpd.anvil_media_worker = media_worker  # type: ignore[attr-defined]
    if media_worker is not None:
        original_server_close = httpd.server_close
        close_lock = threading.Lock()
        closed = False

        def close_media_server() -> None:
            nonlocal closed
            with close_lock:
                if closed:
                    return
                closed = True
            media_worker.stop()
            original_server_close()

        httpd.server_close = close_media_server  # type: ignore[method-assign]
        media_worker.start()
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
    routes = (
        "POST /v1/chat/completions, POST /v1/messages, GET /v1/models, "
        "GET /v1/models/capacity, GET /v1/models/capabilities, "
        "GET /v1/models/fingerprints, GET /v1/router/status, GET /v1/stats, "
        "GET /v1/requests/{request_id}, GET /metrics"
    )
    if getattr(httpd, "anvil_purpose", None) is not None:
        routes += ", POST /v1/embeddings, POST /v1/rerank"
    audio = getattr(httpd, "anvil_audio", None)
    if audio is not None:
        routes += "".join(", POST " + path for path in audio.paths)
    if getattr(httpd, "anvil_gateway", None) is not None:
        routes += (
            ", POST /mcp, POST /a2a, GET /.well-known/agent-card.json, "
            "GET /artifacts/{opaque-id}"
        )
    print(
        f"anvil-serving front door on http://{actual_host}:{actual_port}\n"
        f"  tiers bound: {', '.join(httpd.anvil_tiers) or '(none)'}\n"  # type: ignore[attr-defined]
        f"  routes: {routes}",
        flush=True,
    )
    serve_until_signal(httpd)


def default_config_candidates() -> list[str]:
    """Machine-wide router config, then the legacy current-directory file."""
    return [operator_config_path("router.toml"), "./router.toml"]


def resolve_config_path(path: str | None = None) -> str:
    """Resolve an explicit router config or the first configured default."""
    if path:
        return os.path.expanduser(path)
    resolved = first_existing(default_config_candidates())
    if resolved:
        return resolved
    raise ConfigError(
        "no router config found in $ANVIL_SERVING_HOME/router.toml, ./router.toml, or "
        "~/.anvil-serving/router.toml; run `anvil-serving init` or pass --config PATH"
    )


def main(argv: Optional[List[str]] = None) -> int:
    """Run ``anvil-serving router run`` with an explicit or discovered config."""
    ap = argparse.ArgumentParser(prog="anvil-serving router run")
    ap.add_argument(
        "--config", metavar="PATH",
        help="direct-router TOML config (default: config home, then ./router.toml)",
    )
    ap.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1; never localhost)")
    ap.add_argument("--port", type=int, default=8000, help="bind port (default 8000)")
    args = ap.parse_args(argv)
    try:
        serve(resolve_config_path(args.config), host=args.host, port=args.port)
    except ConfigError as exc:
        print(f"anvil-serving router run: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
