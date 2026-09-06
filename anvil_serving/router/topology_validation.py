"""Pure, exact-byte topology validation for managed router configurations."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field

from ..topology import Topology, load_topology
from .config import (
    MAX_ROUTER_CONFIG_BYTES,
    ConfigError,
    RouterConfig,
    _normalized_replica_endpoint,
    load_bytes,
)


SCHEMA_VERSION = "replica-topology-validation/v1"
DEPLOYMENT_IDENTITY_SOURCE = "declared"
RUNTIME_DEPLOYMENT_IDENTITY_VERIFIED = False

ERROR_CONFIG_TOO_LARGE = "config_too_large"
ERROR_ROUTER_CONFIG_INVALID = "router_config_invalid"
ERROR_TOPOLOGY_INVALID = "topology_invalid"
ERROR_REPLICA_RESOURCE_MISSING = "replica_resource_missing"
ERROR_REPLICA_RESOURCE_REUSED = "replica_resource_reused"
ERROR_REPLICA_HOST_MISMATCH = "replica_host_mismatch"
ERROR_REPLICA_ENDPOINT_MISSING = "replica_endpoint_missing"
ERROR_REPLICA_ENDPOINT_MISMATCH = "replica_endpoint_mismatch"
ERROR_REPLICA_HOST_SPLIT = "replica_host_split"

VALID_ERROR_CODES = frozenset(
    {
        ERROR_CONFIG_TOO_LARGE,
        ERROR_ROUTER_CONFIG_INVALID,
        ERROR_TOPOLOGY_INVALID,
        ERROR_REPLICA_RESOURCE_MISSING,
        ERROR_REPLICA_RESOURCE_REUSED,
        ERROR_REPLICA_HOST_MISMATCH,
        ERROR_REPLICA_ENDPOINT_MISSING,
        ERROR_REPLICA_ENDPOINT_MISMATCH,
        ERROR_REPLICA_HOST_SPLIT,
    }
)


class ReplicaTopologyValidationError(ValueError):
    """A fixed, input-free failure at the managed topology boundary."""

    def __init__(self, code: str):
        if code not in VALID_ERROR_CODES:
            raise ValueError("invalid replica topology error code")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ReplicaTopologySummary:
    """Allowlisted declaration-only facts from one pure topology validation."""

    tier_count: int
    replica_tier_count: int
    replica_member_count: int
    deployment_identity_source: str = field(
        default=DEPLOYMENT_IDENTITY_SOURCE, init=False
    )
    runtime_deployment_identity_verified: bool = field(
        default=RUNTIME_DEPLOYMENT_IDENTITY_VERIFIED, init=False
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "tier_count": self.tier_count,
            "replica_tier_count": self.replica_tier_count,
            "replica_member_count": self.replica_member_count,
            "deployment_identity_source": self.deployment_identity_source,
            "runtime_deployment_identity_verified": (
                self.runtime_deployment_identity_verified
            ),
        }


@dataclass(frozen=True)
class ValidatedRouterConfigSnapshot:
    """One immutable config/topology validation result for later activation."""

    config_bytes: bytes = field(repr=False)
    config_sha256: str
    config: RouterConfig = field(repr=False)
    topology: Topology = field(repr=False)
    tier_count: int
    replica_tier_count: int
    replica_member_count: int
    deployment_identity_source: str = field(
        default=DEPLOYMENT_IDENTITY_SOURCE, init=False
    )
    runtime_deployment_identity_verified: bool = field(
        default=RUNTIME_DEPLOYMENT_IDENTITY_VERIFIED, init=False
    )

    def to_dict(self) -> dict[str, object]:
        """Return the closed success projection consumed by the later CLI task."""
        return {
            "schema_version": SCHEMA_VERSION,
            "valid": True,
            "error_code": None,
            "config_sha256": self.config_sha256,
            "tier_count": self.tier_count,
            "replica_tier_count": self.replica_tier_count,
            "replica_member_count": self.replica_member_count,
            "deployment_identity_source": self.deployment_identity_source,
            "runtime_deployment_identity_verified": (
                self.runtime_deployment_identity_verified
            ),
        }


def _fail(code: str) -> None:
    raise ReplicaTopologyValidationError(code) from None


def validate_replica_topology(
    config: RouterConfig, topology: Topology
) -> ReplicaTopologySummary:
    """Validate replica ownership and endpoints without probing any resource."""
    if not isinstance(config, RouterConfig):
        _fail(ERROR_ROUTER_CONFIG_INVALID)
    if not isinstance(topology, Topology):
        _fail(ERROR_TOPOLOGY_INVALID)

    resources = {resource.id: resource for resource in topology.resources}
    replica_tier_count = 0
    replica_member_count = 0
    # Each declared endpoint resource has one replica admission owner. Reusing
    # it across logical tiers would double-count its independently owned leases.
    used_resources: set[str] = set()

    for tier in config.tiers:
        if not tier.replicas:
            continue
        replica_tier_count += 1
        replica_member_count += len(tier.replicas)
        host_ids = {member.host_id for member in tier.replicas}
        if len(host_ids) != 1:
            _fail(ERROR_REPLICA_HOST_SPLIT)

        for member in tier.replicas:
            if member.resource_id in used_resources:
                _fail(ERROR_REPLICA_RESOURCE_REUSED)
            used_resources.add(member.resource_id)

            resource = resources.get(member.resource_id)
            if resource is None:
                _fail(ERROR_REPLICA_RESOURCE_MISSING)
            if resource.host != member.host_id:
                _fail(ERROR_REPLICA_HOST_MISMATCH)
            if resource.endpoint is None:
                _fail(ERROR_REPLICA_ENDPOINT_MISSING)
            try:
                configured_endpoint = _normalized_replica_endpoint(
                    member.base_url, "replica endpoint"
                )
                topology_endpoint = _normalized_replica_endpoint(
                    resource.endpoint, "topology endpoint"
                )
            except ConfigError:
                configured_endpoint = None
                topology_endpoint = None
            if configured_endpoint is None or topology_endpoint is None:
                _fail(ERROR_REPLICA_ENDPOINT_MISMATCH)
            if configured_endpoint != topology_endpoint:
                _fail(ERROR_REPLICA_ENDPOINT_MISMATCH)

    return ReplicaTopologySummary(
        tier_count=len(config.tiers),
        replica_tier_count=replica_tier_count,
        replica_member_count=replica_member_count,
    )


def _read_config_bytes(config_path: str | os.PathLike[str]) -> bytes:
    raw: bytes | None
    try:
        filesystem_path = os.path.expanduser(os.fspath(config_path))
        with open(filesystem_path, "rb") as handle:
            raw = handle.read(MAX_ROUTER_CONFIG_BYTES + 1)
    except Exception:
        raw = None
    if raw is None:
        _fail(ERROR_ROUTER_CONFIG_INVALID)
    if len(raw) > MAX_ROUTER_CONFIG_BYTES:
        _fail(ERROR_CONFIG_TOO_LARGE)
    return raw


def load_validated_router_snapshot(
    config_path: str | os.PathLike[str],
    topology_path: str | os.PathLike[str],
    topology_overlay_path: str | os.PathLike[str] | None = None,
) -> ValidatedRouterConfigSnapshot:
    """Capture once, parse, and validate one immutable managed config snapshot."""
    raw = _read_config_bytes(config_path)
    config: RouterConfig | None
    try:
        config = load_bytes(raw)
    except Exception:
        # tomllib may also raise ValueError for oversized integer literals or
        # RecursionError for deeply nested input, even inside the byte limit.
        # Raise the fixed boundary error outside this handler to retain no
        # raw exception context from private configuration.
        config = None
    if config is None:
        _fail(ERROR_ROUTER_CONFIG_INVALID)
    topology: Topology | None
    try:
        topology = load_topology(topology_path, topology_overlay_path)
    except Exception:
        topology = None
    if topology is None:
        _fail(ERROR_TOPOLOGY_INVALID)
    summary = validate_replica_topology(config, topology)
    return ValidatedRouterConfigSnapshot(
        config_bytes=raw,
        config_sha256=hashlib.sha256(raw).hexdigest(),
        config=config,
        topology=topology,
        tier_count=summary.tier_count,
        replica_tier_count=summary.replica_tier_count,
        replica_member_count=summary.replica_member_count,
    )
