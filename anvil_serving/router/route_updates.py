"""Bounded, explicit live-route transactions; model lifecycle stays with its owner.

The protected document is the only durable route authority. The boot TOML is
never rewritten. A pending publication is a committed revision, not a proposal.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import fields, is_dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
import tomllib
from types import MappingProxyType
from typing import Mapping

from .config import (
    MAX_ROUTER_CONFIG_BYTES, RouterConfig, _normalized_replica_endpoint,
    _parse_tier, load_bytes,
)
from .keys import _private_created_descriptor, _secure_database, _secure_directory

MAX_STATE_BYTES = 1024 * 1024
PREVIEW_TTL_SECONDS = 300
MAX_PREVIEWS = 4
_HOT_FIELDS = frozenset({
    "base_url", "model", "context_limit", "max_output_tokens", "context_admission",
    "max_concurrency", "health_path", "model_identity", "tool_support",
    "engine", "quantization", "params",
})
_METADATA_KEYS = {
    "fingerprint": {"model_revision", "engine_version", "image_digest", "config_fingerprint"},
    "capacity": {"gpu_role", "gpu_roles", "gpu_name", "gpu_memory_total_mib", "image_limit", "video_limit",
                 "image_tokens_estimate", "video_tokens_estimate", "kv_cache_capacity_tokens",
                 "scheduler_max_num_seqs", "model_memory_gib", "media_admission_enabled"},
    "capabilities": {"modalities", "thinking", "compat", "images_per_request", "video_per_request"},
}
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[0-9a-f]{32}")


class RouteUpdateError(ValueError):
    """Content-free management error shared by HTTP, CLI and controller callers."""

    def __init__(self, code, *, status=409):
        self.code, self.status = code, status
        super().__init__(code)


def _require(condition, code="route_state_invalid", *, status=409):
    if not condition:
        raise RouteUpdateError(code, status=status)


def _plain(value):
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def freeze_config(config: RouterConfig) -> RouterConfig:
    """Detach nested JSON from the caller before a request can capture it."""
    return replace(config, model_routes=_freeze(config.model_routes), tiers=tuple(
        replace(tier, params=_freeze(tier.params), extra_body=_freeze(tier.extra_body),
                extra_body_defaults=_freeze(tier.extra_body_defaults)) for tier in config.tiers
    ))


def _encoded(value):
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def route_projection(config):
    return {"model_routes": dict(config.model_routes), "tiers": {
        tier.id: {key: _plain(getattr(tier, key)) for key in sorted(_HOT_FIELDS)}
        for tier in sorted(config.tiers, key=lambda tier: tier.id)
    }}


def route_digest(config):
    return _digest(route_projection(config))


def _tier_document(tier):
    value = {key: item for key, item in _plain(tier).items() if item is not None}
    if not tier.replicas:
        for key in ("replicas", "replica_identity", "replica_strategy"):
            value.pop(key, None)
    else:
        value.pop("base_url")
    if tier.metadata_source == "upstream":
        value.pop("model", None)
        value.pop("context_limit", None)
    return value


def _validate_text(value):
    if isinstance(value, str):
        _require(not any(ord(char) < 32 or ord(char) == 127 for char in value),
                 "route_control_character", status=400)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _validate_text(key)
            _validate_text(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _validate_text(item)
    elif isinstance(value, float):
        _require(math.isfinite(value), "route_metadata_invalid", status=400)


def _validate_metadata(tier):
    params = tier.params or {}
    for section, allowed in _METADATA_KEYS.items():
        value = params.get(section, {})
        _require(isinstance(value, Mapping) and set(value) <= allowed,
                 "route_metadata_invalid", status=400)
    capabilities = params.get("capabilities", {})
    for value in params.get("fingerprint", {}).values():
        _require(type(value) is str and bool(value), "route_metadata_invalid", status=400)
    for key, allowed in (
        ("thinking", {"supported", "default", "caller_override", "max_tokens"}),
        ("compat", {"supportsUsageInStreaming", "supportsStrictMode", "supportedReasoningEfforts"}),
    ):
        value = capabilities.get(key, {})
        _require(isinstance(value, Mapping) and set(value) <= allowed,
                 "route_metadata_invalid", status=400)
    for section in (params.get("capacity", {}), capabilities):
        for key, value in section.items():
            if key.endswith(("_tokens", "_mib", "_gib", "_estimate", "_limit", "_request", "_seqs")):
                _require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
                         "route_metadata_invalid", status=400)
    _validate_text(_tier_document(tier))
    _normalized_replica_endpoint(tier.base_url, "live route endpoint")


def _config_from_projection(base, projection):
    _require(type(projection) is dict and set(projection) == {"tiers", "model_routes"})
    _require(type(projection["tiers"]) is dict and set(projection["tiers"]) == {tier.id for tier in base.tiers})
    routes = projection["model_routes"]
    _require(type(routes) is dict and set(routes) == set(base.model_routes)
             and all(type(value) is str and value in projection["tiers"] for value in routes.values()))
    _require(set(routes.values()) == set(projection["tiers"]))
    tiers = []
    for tier in base.tiers:
        values = projection["tiers"][tier.id]
        _require(type(values) is dict and set(values) == _HOT_FIELDS)
        changed = replace(tier, **values)
        if changed != tier:
            changed = _parse_tier(_tier_document(changed))
        tiers.append(changed)
    candidate = freeze_config(replace(base, tiers=tuple(tiers), model_routes=routes))
    validate_change(base, candidate)
    return candidate


def validate_change(current, candidate, requested_mode=None):
    """Compare parsed semantics, including all restart-only fields and references."""
    _require(set(current.model_routes) == set(candidate.model_routes), "route_alias_set_restart_required")
    _require({tier.id for tier in current.tiers} == {tier.id for tier in candidate.tiers},
             "route_tier_set_restart_required")
    for field in fields(current):
        if field.name not in {"tiers", "model_routes"}:
            _require(getattr(current, field.name) == getattr(candidate, field.name), "route_field_restart_required")
    changed_tiers = []
    for tier in current.tiers:
        new = candidate.tier(tier.id)
        if tier == new:
            continue
        _require(not tier.replicas and not new.replicas
                 and tier.metadata_source == new.metadata_source == "configured"
                 and tier.max_concurrency != "auto" and new.max_concurrency != "auto",
                 "route_target_restart_required")
        for field in fields(tier):
            if field.name not in _HOT_FIELDS:
                _require(getattr(tier, field.name) == getattr(new, field.name), "route_field_restart_required")
        old_params, new_params = tier.params or {}, new.params or {}
        _require({key: value for key, value in old_params.items() if key not in _METADATA_KEYS}
                 == {key: value for key, value in new_params.items() if key not in _METADATA_KEYS},
                 "route_metadata_restart_required")
        _require(new.model_identity and bool(new.model) and bool(new.health_path),
                 "route_exact_identity_required")
        _validate_metadata(new)
        changed_tiers.append(tier.id)
    moved_aliases = sorted(alias for alias in current.model_routes
                           if current.model_routes[alias] != candidate.model_routes[alias])
    for alias in moved_aliases:
        for config in (current, candidate):
            tier = config.route_tier(alias)
            _require(not tier.replicas and tier.metadata_source == "configured"
                     and tier.max_concurrency != "auto" and tier.model_identity,
                     "route_target_restart_required")
    mode = "tier_replacement" if changed_tiers else "alias_only" if moved_aliases else "noop"
    _require(requested_mode is None or requested_mode == mode, "route_mode_mismatch")
    if changed_tiers:
        _validate_release_scope(current, changed_tiers)
        _validate_release_scope(candidate, changed_tiers)
    aliases = sorted(set(moved_aliases) | {
        alias for alias, tier_id in current.model_routes.items() if tier_id in changed_tiers
    } | {alias for alias, tier_id in candidate.model_routes.items() if tier_id in changed_tiers})
    return {"mode": mode, "tier_ids": sorted(changed_tiers), "aliases": aliases,
            "target_tier_ids": sorted(set(changed_tiers) | {candidate.model_routes[alias] for alias in moved_aliases})}


def _validate_release_scope(config, tier_ids):
    """A chat drain is never proof that a shared non-chat owner is idle."""
    affected = {_normalized_replica_endpoint(config.tier(tier_id).base_url, "route")[:3]
                for tier_id in tier_ids}
    for tier in config.tiers:
        if tier.id in tier_ids:
            continue
        endpoints = [member.base_url for member in tier.replicas] if tier.replicas else [tier.base_url]
        _require(not any(_normalized_replica_endpoint(endpoint, "route")[:3] in affected for endpoint in endpoints),
                 "route_shared_owner_release_unsupported")
    for route in (*config.purpose_models, *config.audio_routes, *config.memory_routes):
        _require(_normalized_replica_endpoint(route.base_url, "route")[:3] not in affected,
                 "route_shared_owner_release_unsupported")


def _owner_evidence(value):
    if value is None:
        return None
    _require(type(value) is dict and set(value) == {"source", "binding_sha256", "container_id", "served_model"},
             "route_owner_evidence_invalid", status=400)
    _require(value["source"] == "campaign_activation/v1"
             and type(value["binding_sha256"]) is str and _DIGEST.fullmatch(value["binding_sha256"])
             and type(value["container_id"]) is str and _DIGEST.fullmatch(value["container_id"])
             and type(value["served_model"]) is str and 0 < len(value["served_model"]) <= 256,
             "route_owner_evidence_invalid", status=400)
    _validate_text(value)
    return dict(value)


def _document(config_bytes):
    _require(type(config_bytes) is bytes and len(config_bytes) <= MAX_ROUTER_CONFIG_BYTES,
             "route_candidate_too_large", status=413)
    try:
        config = load_bytes(config_bytes)
        document = tomllib.loads(config_bytes.decode("utf-8"))
    except Exception:
        raise RouteUpdateError("route_candidate_invalid", status=400) from None
    return freeze_config(config), document


def _static_document(document):
    value = _plain(document)
    value["router"] = {key: item for key, item in value["router"].items() if key not in {"tiers", "model_routes"}}
    return value


def _read_state(path):
    _secure_database(path, exists=True)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _private_created_descriptor(descriptor)
        _secure_database(path, exists=True, identity=os.fstat(descriptor))
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            raw = source.read(MAX_STATE_BYTES + 1)
        _require(len(raw) <= MAX_STATE_BYTES, "route_state_too_large")
        from ..observability.dashboard.contracts import strict_json
        return strict_json(raw)
    finally:
        os.close(descriptor)


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_state(path, expected):
    _require(_digest(_read_state(path)) == expected, "route_authority_changed")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _private_created_descriptor(descriptor)
        _secure_database(path, exists=True, identity=os.fstat(descriptor))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _sync_directory(path.parent)
    _require(_digest(_read_state(path)) == expected, "route_authority_changed")


def _write_state(path, value, *, expected):
    raw = _encoded(value)
    _require(len(raw) <= MAX_STATE_BYTES, "route_state_too_large", status=413)
    _secure_directory(path.parent, create=False)
    if expected is None:
        _require(not os.path.lexists(path), "route_state_already_initialized")
    else:
        _require(_digest(_read_state(path)) == expected, "route_authority_changed")
    descriptor, temporary = tempfile.mkstemp(prefix=".route-state-", dir=path.parent)
    try:
        _private_created_descriptor(descriptor)
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        if expected is None:
            os.link(temporary, path, follow_symlinks=False)
        else:
            _require(_digest(_read_state(path)) == expected, "route_authority_changed")
            os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def initialize_route_state(config_bytes, state_path):
    """Explicit, exclusive first-install operation; never called during startup."""
    config, _ = _document(config_bytes)
    path = Path(state_path)
    _require(path.is_absolute(), "route_state_path_invalid")
    _secure_directory(path.parent, create=True)
    value = {"schema": "router-route-state/v1", "base_config_sha256": hashlib.sha256(config_bytes).hexdigest(),
             "route_revision": 0, "effective_route_sha256": route_digest(config),
             "projection": route_projection(config), "previous": None, "operation": None}
    descriptor = _lock_authority(path)
    try:
        try:
            _write_state(path, value, expected=None)
        except Exception:
            # A lost directory-fsync result may have installed exactly R0.
            # Only that exact first-install state is safe to acknowledge again.
            _require(_read_state(path) == value, "route_state_already_initialized")
            _sync_state(path, _digest(value))
    finally:
        os.close(descriptor)
    return {"initialized": True, "route_revision": 0, "effective_route_sha256": value["effective_route_sha256"]}


def _lock_authority(path):
    _require(os.name == "posix", "route_state_platform_unsupported", status=503)
    import fcntl
    _secure_directory(path.parent, create=False)
    descriptor = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        _private_created_descriptor(descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


class RouteUpdateManager:
    """One writer and a bounded preview cache for one already-owned router."""

    def __init__(self, routing, config_bytes, state_path, *, clock=time.monotonic):
        self.routing = routing
        self.base, self.base_document = _document(config_bytes)
        self.base_sha256 = hashlib.sha256(config_bytes).hexdigest()
        self.path = Path(state_path)
        self._clock, self._writer = clock, threading.Lock()
        self._previews, self._holds = {}, {}
        self._state, self._uncertain = None, False
        self._attempted_state = None
        self._authority_lock = None
        self._closing = False

    def close(self):
        self._closing = True
        # Keep the stable authority lock until an in-flight mutation has
        # finished. A successor must never overlap a draining old writer.
        with self._writer:
            if self._authority_lock is not None:
                os.close(self._authority_lock)
                self._authority_lock = None

    def _validate_state(self, value):
        _require(type(value) is dict and set(value) == {"schema", "base_config_sha256", "route_revision",
                 "effective_route_sha256", "projection", "previous", "operation"})
        _require(value["schema"] == "router-route-state/v1" and value["base_config_sha256"] == self.base_sha256,
                 "route_base_config_changed")
        _require(type(value["route_revision"]) is int and 0 <= value["route_revision"] < 2**53)
        config = _config_from_projection(self.base, value["projection"])
        _require(value["effective_route_sha256"] == route_digest(config))
        if value["route_revision"] == 0:
            _require(value["projection"] == route_projection(self.base) and value["previous"] is None)
        else:
            _require(value["previous"] is not None and value["operation"] is not None)
        previous = value["previous"]
        if previous is not None:
            _require(type(previous) is dict and set(previous) == {"route_revision", "projection", "effective_route_sha256"})
            _require(type(previous["route_revision"]) is int
                     and previous["route_revision"] == value["route_revision"] - 1)
            prior = _config_from_projection(self.base, previous["projection"])
            _require(previous["effective_route_sha256"] == route_digest(prior))
        operation = value["operation"]
        if operation is not None:
            _require(type(operation) is dict and set(operation) == {
                "transaction_id", "preview_id", "mode", "phase", "publish_pending", "candidate_projection",
                "candidate_sha256", "tier_ids", "aliases", "target_tier_ids", "drain_timeout", "actor",
                "owner_evidence_required", "owner_evidence"})
            _require(type(operation["transaction_id"]) is str and _ID.fullmatch(operation["transaction_id"])
                     and type(operation["preview_id"]) is str and _ID.fullmatch(operation["preview_id"]))
            _require(operation["mode"] in {"alias_only", "tier_replacement"}
                     and operation["phase"] in {"prepared", "committed", "readmitted", "complete", "cancelled"}
                     and type(operation["publish_pending"]) is bool)
            _require(operation["publish_pending"] == (operation["phase"] == "committed"))
            candidate = _config_from_projection(self.base, operation["candidate_projection"])
            _require(route_digest(candidate) == operation["candidate_sha256"])
            predecessor = config if operation["phase"] in {"prepared", "cancelled"} else (
                _config_from_projection(self.base, previous["projection"]) if previous else None)
            _require(predecessor is not None)
            scope = validate_change(predecessor, candidate, operation["mode"])
            _require(all(operation[key] == scope[key] for key in ("tier_ids", "aliases", "target_tier_ids")))
            _require(operation["phase"] in {"prepared", "cancelled"} or value["projection"] == operation["candidate_projection"])
            _require(type(operation["drain_timeout"]) in (int, float) and 0 < operation["drain_timeout"] <= 300)
            _require(operation["actor"] == "operator")
            _require(type(operation["owner_evidence_required"]) is bool)
            if operation["owner_evidence_required"]:
                _require(len(operation["tier_ids"]) == 1, "route_owner_scope_unsupported")
            _owner_evidence(operation["owner_evidence"])
            if operation["owner_evidence_required"] and operation["phase"] not in {"prepared", "cancelled"}:
                _require(operation["owner_evidence"] is not None, "route_owner_evidence_required")
        return value

    def restore(self):
        """Validate authority before serving; readiness failures isolate its scope."""
        descriptor = None
        try:
            _require(not self._closing and self._authority_lock is None, "route_manager_closed")
            descriptor = _lock_authority(self.path)
            self._state = self._validate_state(_read_state(self.path))
            _sync_state(self.path, _digest(self._state))
        except Exception:
            if descriptor is not None:
                os.close(descriptor)
            raise RouteUpdateError("route_state_unavailable", status=503) from None
        self._authority_lock = descriptor
        snapshot = self.routing.active_route_snapshot()
        candidate = _config_from_projection(self.base, self._state["projection"])
        operation = self._state["operation"]
        if self._state["route_revision"] == 0 and operation is None:
            return self.status()
        scope = validate_change(snapshot.config, candidate)
        self._require_durable_drain_accounting(
            operation if operation and operation["mode"] == "tier_replacement" else scope
        )
        # Reconstruct the durable pointer before admitting requests. This is a
        # startup-only bootstrap hold; it must not become the retained hold for
        # a later prepared operation, whose scope is relative to this pointer.
        bootstrap_hold = None
        if scope["mode"] != "noop":
            bootstrap_hold = self.routing.begin_route_update_hold(
                update_id=secrets.token_hex(16), expected_revision=snapshot.route_revision,
                tier_ids=tuple(scope["tier_ids"]), aliases=tuple(scope["aliases"]),
            )
        runtime = self.routing.prepare_route_runtime(candidate, revision=self._state["route_revision"],
                                                     digest=self._state["effective_route_sha256"])
        self.routing.publish_route_runtime(runtime, expected_revision=snapshot.route_revision,
                                           hold=bootstrap_hold, alias_only=scope["mode"] == "noop")
        if bootstrap_hold is not None:
            self.routing.open_route_update_hold(bootstrap_hold)
        hold = None
        if operation and operation["phase"] in {"prepared", "committed"}:
            hold = self.routing.begin_route_update_hold(
                update_id=operation["transaction_id"], expected_revision=runtime.route_revision,
                tier_ids=tuple(operation["tier_ids"]), aliases=tuple(operation["aliases"]),
            )
            self._holds[operation["transaction_id"]] = hold
        if operation and operation["phase"] in {"prepared", "committed"} and operation["mode"] == "tier_replacement":
            return self.status()
        try:
            targets = operation["target_tier_ids"] if operation else scope["target_tier_ids"]
            self.routing.probe_route_update_targets(runtime, targets,
                                                    require_admitting=bool(operation and operation["mode"] == "alias_only"))
            if operation and operation["phase"] == "committed":
                updated = _plain(self._state)
                updated["operation"].update(phase="readmitted", publish_pending=False)
                self._persist(updated)
            if hold is not None:
                self.routing.open_route_update_hold(hold)
                self._holds.pop(operation["transaction_id"])
            if operation and operation["phase"] in {"committed", "readmitted"}:
                updated = _plain(self._state)
                updated["operation"]["phase"] = "complete"
                self._persist(updated)
        except Exception:
            # A validated authority can serve unrelated routes while this scope
            # awaits explicit recovery. Never roll a committed revision back.
            pass
        return self.status()

    @contextmanager
    def _mutation(self):
        _require(self._writer.acquire(blocking=False), "route_update_busy")
        permit = None
        try:
            _require(not self._closing and self._authority_lock is not None, "route_state_unavailable", status=503)
            owner = getattr(self.routing, "_router_admission", None)
            if owner is not None:
                permit = owner.acquire("maintenance")
            yield
        finally:
            if permit is not None:
                permit.release()
            self._writer.release()

    def _persist(self, value):
        value = _plain(value)
        self._validate_state(value)
        self._attempted_state = value
        try:
            _write_state(self.path, value, expected=_digest(self._state))
        except Exception:
            self._uncertain = True
            # Read-back alone does not prove durability. Re-sync before an
            # explicit resume can reopen admission after a lost fsync result.
            try:
                observed = self._validate_state(_read_state(self.path))
                if observed == self._state or observed == self._attempted_state:
                    self._state = observed
            except Exception:
                pass
            raise RouteUpdateError("route_reconciliation_required", status=503) from None
        self._state, self._uncertain = value, False
        self._attempted_state = None

    def _fresh_authority(self):
        state = self._validate_state(_read_state(self.path))
        _require(state == self._state or self._uncertain and state == self._attempted_state,
                 "route_authority_changed")
        _sync_state(self.path, _digest(state))
        attempted = self._attempted_state
        self._state = state
        if attempted and attempted["operation"]:
            identifier = attempted["operation"]["transaction_id"]
            if state["operation"] is None or state["operation"]["transaction_id"] != identifier:
                hold = self._holds.pop(identifier, None)
                if hold is not None:
                    self.routing.open_route_update_hold(hold)
        self._uncertain = False
        self._attempted_state = None

    def _require_durable_drain_accounting(self, scope):
        """Tier replacement needs a crash-durable unresolved-start reader."""
        if scope["mode"] != "tier_replacement":
            return
        try:
            available = self.routing.route_drain_accounting_ready()
        except Exception:
            available = False
        _require(available is True, "route_drain_accounting_required", status=503)

    def _preview(self, candidate, scope, *, drain_timeout=30, resume_id=None, owner_evidence=None, rollback=False, cancel=False):
        _require(type(drain_timeout) in (int, float) and 0 < drain_timeout <= 300,
                 "route_drain_timeout_invalid", status=400)
        # Refuse before caching a candidate or persisting a prepared operation:
        # a route counter alone cannot survive a start/terminal crash window.
        self._require_durable_drain_accounting(scope)
        now = self._clock()
        self._previews = {key: value for key, value in self._previews.items() if value["expires"] > now}
        _require(len(self._previews) < MAX_PREVIEWS, "route_preview_capacity", status=429)
        snapshot = self.routing.active_route_snapshot()
        identifier = secrets.token_hex(16)
        entry = {"candidate": candidate, "scope": scope, "expires": now + PREVIEW_TTL_SECONDS,
                 "route_revision": snapshot.route_revision, "effective_route_sha256": snapshot.effective_route_sha256,
                 "admission": self.routing.route_admission_fingerprint(), "drain_timeout": drain_timeout,
                 "resume_id": resume_id, "owner_evidence": _owner_evidence(owner_evidence), "rollback": rollback, "cancel": cancel}
        _require(len(_encoded(route_projection(candidate))) <= MAX_ROUTER_CONFIG_BYTES, "route_candidate_too_large", status=413)
        # Bound the complete authority before a caller can confirm a hold.
        size_probe = _plain(self._state)
        size_probe.update(route_revision=self._state["route_revision"] + 1,
                          projection=route_projection(candidate), effective_route_sha256=route_digest(candidate))
        size_probe["previous"] = {key: self._state[key] for key in ("route_revision", "projection", "effective_route_sha256")}
        size_probe["operation"] = {**scope, "transaction_id": "0" * 32, "preview_id": identifier,
                                   "phase": "committed", "publish_pending": True,
                                   "candidate_projection": route_projection(candidate), "candidate_sha256": route_digest(candidate),
                                   "drain_timeout": drain_timeout, "actor": "operator",
                                   "owner_evidence_required": resume_id is not None,
                                   "owner_evidence": owner_evidence}
        _require(scope["mode"] == "noop" or len(_encoded(size_probe)) <= MAX_STATE_BYTES, "route_state_too_large", status=413)
        preparation_probe = _plain(self._state)
        preparation_probe["operation"] = size_probe["operation"]
        _require(scope["mode"] == "noop" or len(_encoded(preparation_probe)) <= MAX_STATE_BYTES, "route_state_too_large", status=413)
        self._previews[identifier] = entry
        return {"preview_id": identifier, "expires_in_seconds": PREVIEW_TTL_SECONDS, **scope,
                "route_revision": snapshot.route_revision, "effective_route_sha256": snapshot.effective_route_sha256,
                "candidate_sha256": route_digest(candidate), "drain_timeout": drain_timeout,
                "transaction_id": resume_id, "readmission_included": scope["mode"] != "noop",
                "operation_phase": self._state["operation"]["phase"] if resume_id else None,
                "authority_route_revision": self._state["route_revision"],
                "action": "cancel_prepared_update" if cancel else "rollback" if rollback else "apply",
                "owner_evidence_sha256": _digest(owner_evidence) if owner_evidence else None,
                "changes": [{"tier_id": tier_id, "old_model": snapshot.config.tier(tier_id).model,
                             "new_model": candidate.tier(tier_id).model,
                             "old_context": snapshot.config.tier(tier_id).context_limit,
                             "new_context": candidate.tier(tier_id).context_limit}
                            for tier_id in scope["tier_ids"]]}

    def preview(self, config_bytes, *, mode=None, drain_timeout=30):
        with self._mutation():
            self._fresh_authority()
            _require(not self._pending(), "route_update_pending")
            candidate, document = _document(config_bytes)
            _require(_static_document(document) == _static_document(self.base_document), "route_field_restart_required")
            scope = validate_change(self.routing.active_route_snapshot().config, candidate, mode)
            return self._preview(candidate, scope, drain_timeout=drain_timeout)

    def _pending(self):
        operation = self._state["operation"]
        return self._uncertain or bool(self._holds) or bool(operation and operation["phase"] not in {"complete", "cancelled"})

    def rollback(self, revision):
        with self._mutation():
            self._fresh_authority()
            operation = self._state["operation"]
            if (type(revision) is int and revision == self._state["route_revision"]
                    and operation is not None and operation["phase"] == "prepared"):
                candidate = _config_from_projection(self.base, self._state["projection"])
                scope = {key: operation[key] for key in ("mode", "tier_ids", "aliases", "target_tier_ids")}
                return self._preview(candidate, scope, resume_id=operation["transaction_id"], cancel=True)
            previous = self._state["previous"]
            _require(type(revision) is int and previous is not None and previous["route_revision"] == revision,
                     "route_rollback_not_retained")
            candidate = _config_from_projection(self.base, previous["projection"])
            pending = self._pending()
            _require(not pending or operation is not None and operation["phase"] in {"committed", "readmitted", "complete"},
                     "route_prepared_recovery_required")
            current = _config_from_projection(self.base, self._state["projection"])
            return self._preview(candidate, validate_change(current, candidate),
                                 resume_id=operation["transaction_id"] if pending else None, rollback=True)

    def resume(self, transaction_id, *, owner_evidence=None):
        _require(type(transaction_id) is str and _ID.fullmatch(transaction_id), "route_transaction_invalid", status=400)
        with self._mutation():
            self._fresh_authority()
            operation = self._state["operation"]
            _require(operation is not None and operation["transaction_id"] == transaction_id, "route_transaction_unknown")
            _require(self._pending(), "route_transaction_complete")
            projection = self._state["projection"] if operation["phase"] == "cancelled" else operation["candidate_projection"]
            candidate = _config_from_projection(self.base, projection)
            scope = {key: operation[key] for key in ("mode", "tier_ids", "aliases", "target_tier_ids")}
            evidence = _owner_evidence(owner_evidence) if owner_evidence is not None else operation["owner_evidence"]
            if evidence is not None:
                _require({evidence["served_model"]} == {candidate.tier(tier_id).model for tier_id in scope["tier_ids"]},
                         "route_owner_evidence_mismatch")
            return self._preview(candidate, scope, drain_timeout=operation["drain_timeout"],
                                 resume_id=transaction_id, owner_evidence=evidence)

    def _hold(self, operation):
        self._require_durable_drain_accounting(operation)
        identifier = operation["transaction_id"]
        if identifier not in self._holds:
            self._holds[identifier] = self.routing.begin_route_update_hold(update_id=identifier,
                expected_revision=self.routing.active_route_snapshot().route_revision,
                tier_ids=tuple(operation["tier_ids"]), aliases=tuple(operation["aliases"]))
        return self._holds[identifier]

    def apply(self, preview_id, *, confirm=False, prepare_only=False):
        _require(type(preview_id) is str and _ID.fullmatch(preview_id), "route_preview_invalid", status=400)
        _require(confirm is True and type(prepare_only) is bool, "route_confirmation_required", status=400)
        with self._mutation():
            self._fresh_authority()
            operation = self._state["operation"]
            if (operation and operation["preview_id"] == preview_id
                    and operation["phase"] in {"complete", "cancelled"} and not self._holds):
                return self.status()
            entry = self._previews.get(preview_id)
            _require(entry is not None and entry["expires"] > self._clock(), "route_preview_expired")
            snapshot = self.routing.active_route_snapshot()
            _require(snapshot.route_revision == entry["route_revision"]
                     and snapshot.effective_route_sha256 == entry["effective_route_sha256"]
                     and self.routing.route_admission_fingerprint() == entry["admission"], "route_preview_stale")
            candidate, scope = entry["candidate"], entry["scope"]
            _require(not prepare_only or scope["mode"] == "tier_replacement", "route_prepare_requires_replacement")
            if scope["mode"] == "noop":
                self._previews.pop(preview_id)
                return {**self.status(), "no_op": True}
            if prepare_only:
                _require(len(scope["tier_ids"]) == 1, "route_owner_scope_unsupported")
                _validate_release_scope(snapshot.config, scope["tier_ids"])
            if entry["cancel"] or (entry["resume_id"] is not None and not entry["rollback"]
                                    and operation["phase"] in {"complete", "cancelled"}):
                _require(operation["transaction_id"] == entry["resume_id"], "route_preview_stale")
                hold = self._holds.get(operation["transaction_id"])
                if hold is not None:
                    _require(self.routing.wait_route_update_drain(hold, entry["drain_timeout"]).get("drained") is True,
                             "route_drain_timeout")
                runtime = self.routing.prepare_route_runtime(candidate, revision=self._state["route_revision"],
                                                             digest=self._state["effective_route_sha256"])
                targets = sorted(set(scope["target_tier_ids"]) | set(getattr(hold, "tier_ids", ())))
                self.routing.probe_route_update_targets(runtime, targets,
                                                        require_admitting=scope["mode"] == "alias_only")
                if entry["cancel"]:
                    value = _plain(self._state)
                    value["operation"].update(phase="cancelled", publish_pending=False, preview_id=preview_id)
                    self._persist(value)
                if hold is not None:
                    self.routing.open_route_update_hold(hold)
                    self._holds.pop(operation["transaction_id"])
                self._previews.pop(preview_id)
                return self.status()
            if entry["resume_id"] is None:
                _require(not self._pending(), "route_update_pending")
                operation = {**scope, "transaction_id": secrets.token_hex(16), "preview_id": preview_id,
                             "phase": "prepared", "publish_pending": False,
                             "candidate_projection": route_projection(candidate), "candidate_sha256": route_digest(candidate),
                             "drain_timeout": entry["drain_timeout"], "actor": "operator",
                             "owner_evidence_required": prepare_only, "owner_evidence": None}
            else:
                _require(operation is not None and operation["transaction_id"] == entry["resume_id"], "route_preview_stale")
                operation = _plain(operation)
                operation["preview_id"] = preview_id
                operation["owner_evidence"] = entry["owner_evidence"]
                if entry["rollback"]:
                    operation.update(**scope, phase="prepared", publish_pending=False,
                                     candidate_projection=route_projection(candidate), candidate_sha256=route_digest(candidate),
                                     owner_evidence_required=False, owner_evidence=None)
            hold = self._holds.get(operation["transaction_id"])
            try:
                if scope["mode"] == "tier_replacement" and operation["phase"] == "prepared":
                    value = _plain(self._state)
                    value["operation"] = operation
                    self._persist(value)
                    hold = self._hold(operation)
                    drained = self.routing.wait_route_update_drain(hold, entry["drain_timeout"])
                    _require(drained.get("drained") is True, "route_drain_timeout")
                    if prepare_only:
                        return self.status()
                _require(not prepare_only, "route_prepare_requires_replacement")
                _require(not operation["owner_evidence_required"] or operation["owner_evidence"] is not None,
                         "route_owner_evidence_required")
                current = self.routing.active_route_snapshot()
                committing = operation["phase"] == "prepared"
                revision = self._state["route_revision"] + 1 if committing else self._state["route_revision"]
                runtime = self.routing.prepare_route_runtime(candidate, revision=revision, digest=route_digest(candidate))
                if scope["mode"] == "alias_only":
                    self.routing.probe_route_update_targets(runtime, scope["target_tier_ids"], require_admitting=True)
                if committing:
                    value = _plain(self._state)
                    value["previous"] = {key: value[key] for key in ("route_revision", "projection", "effective_route_sha256")}
                    value.update(route_revision=revision, projection=route_projection(candidate), effective_route_sha256=route_digest(candidate))
                    operation.update(phase="committed", publish_pending=True)
                    value["operation"] = operation
                    self._persist(value)
                if current.route_revision != revision or current.effective_route_sha256 != runtime.effective_route_sha256:
                    self.routing.publish_route_runtime(runtime, expected_revision=current.route_revision, hold=hold,
                                                       alias_only=scope["mode"] == "alias_only")
                targets = sorted(set(scope["target_tier_ids"]) | set(getattr(hold, "tier_ids", ())))
                self.routing.probe_route_update_targets(runtime, targets,
                                                        require_admitting=scope["mode"] == "alias_only")
                value = _plain(self._state)
                value["operation"].update(phase="readmitted", publish_pending=False, preview_id=preview_id,
                                          owner_evidence=operation["owner_evidence"])
                self._persist(value)
                if hold is not None:
                    self.routing.open_route_update_hold(hold)
                    self._holds.pop(operation["transaction_id"], None)
                value = _plain(self._state)
                value["operation"]["phase"] = "complete"
                self._persist(value)
                self._previews.pop(preview_id, None)
                return self.status()
            except Exception:
                # Once readmission is durably committed, do not invent a new
                # hold after a failed cleanup write. Report reconciliation.
                if self._uncertain:
                    try:
                        self._fresh_authority()
                    except Exception:
                        pass
                authoritative = self._state.get("operation")
                ours = authoritative and authoritative["transaction_id"] == operation["transaction_id"]
                if (self._uncertain or ours) and not (ours and authoritative["phase"] in {"readmitted", "complete", "cancelled"}):
                    self._hold(operation)
                raise

    def status(self):
        state = self._state
        if state is None:
            return {"enabled": True, "state": "unavailable"}
        try:
            authority_available = (self._validate_state(_read_state(self.path)) == state
                                   and not self._uncertain and self._attempted_state is None
                                   and self._authority_lock is not None and not self._closing)
        except Exception:
            authority_available = False
        operation = state["operation"]
        if self._uncertain and self._attempted_state is not None:
            operation = self._attempted_state["operation"]
        return {"enabled": True, "authority_available": authority_available, "route_revision": state["route_revision"],
                "effective_route_sha256": state["effective_route_sha256"], "base_config_sha256": self.base_sha256,
                "state": "reconciliation_required" if not authority_available or self._holds and operation and operation["phase"] in {"readmitted", "cancelled"} else "held" if self._holds else (
                    operation["phase"] if operation else "ready"),
                "transaction_id": operation["transaction_id"] if operation else None,
                "mode": operation["mode"] if operation else None,
                "operation_phase": operation["phase"] if operation else None,
                "drain_scope": "chat_route_bindings", "owner_release_authorized": False,
                "identity_verification": "served_model_name", "artifact_identity_verified": False,
                "owner_evidence": _plain(operation["owner_evidence"]) if operation else None,
                "publish_pending": bool(operation and operation["publish_pending"]),
                "previous_revision": state["previous"]["route_revision"] if state["previous"] else None,
                "runtime": self.routing.route_update_status()}
