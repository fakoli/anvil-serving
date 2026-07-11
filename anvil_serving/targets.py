"""Pure target and operation resolution for topology-aware CLI commands.

Resolution turns a declared command and topology identity into an immutable
execution plan.  It intentionally performs no endpoint, credential, GPU, or
host discovery: transports consume the resulting plan in a later layer.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
from types import MappingProxyType
from typing import Mapping
import urllib.parse

from .guard import CapacityDecision, evaluate_capacity_policy
from .topology import (
    CommandIdentity,
    GpuRole,
    Host,
    Resource,
    Runtime,
    Topology,
    TopologyResolutionError,
    Transport,
    resolve_command_identity,
    topology_snapshot_identity,
)


_TRANSPORTS = frozenset({"local", "controller", "ssh"})
_REQUESTED_TRANSPORTS = _TRANSPORTS | {"auto"}
_EXECUTION_POLICIES = frozenset({"resource-owner", "offline"})
_EXIT_CODES = {"usage": 2, "safety": 3}
_TARGET_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


class CommandSpecError(ValueError):
    """A command declaration is incomplete or internally inconsistent."""


class TargetResolutionError(ValueError):
    """A target or execution plan cannot be resolved before side effects.

    ``metadata`` is deliberately small and immutable so CLI and controller
    envelopes can preserve the exit class without parsing error prose.
    """

    def __init__(
        self,
        message: str,
        *,
        exit_class: str = "safety",
        details: Mapping[str, object] | None = None,
    ) -> None:
        if exit_class not in _EXIT_CODES:
            raise ValueError(f"unknown exit class {exit_class!r}")
        self.exit_class = exit_class
        self.exit_code = _EXIT_CODES[exit_class]
        metadata: dict[str, object] = {"exit_class": exit_class, "exit_code": self.exit_code}
        metadata.update(details or {})
        self.metadata: Mapping[str, object] = MappingProxyType(metadata)
        super().__init__(message)


@dataclass(frozen=True)
class CommandSpec:
    """Bounded declaration for one CLI/controller operation (R010)."""

    name: str
    resource_role: str | None
    supported_transports: tuple[str, ...]
    execution_runtime_roles: tuple[str, ...]
    mutation_class: str
    recovery_capable: bool
    gpu_role_required: bool
    execution_policy: str = "resource-owner"

    def __post_init__(self) -> None:
        object.__setattr__(self, "supported_transports", tuple(self.supported_transports))
        object.__setattr__(self, "execution_runtime_roles", tuple(self.execution_runtime_roles))
        if not self.name:
            raise CommandSpecError("command name is required")
        if self.execution_policy not in _EXECUTION_POLICIES:
            raise CommandSpecError(
                f"execution policy must be one of {sorted(_EXECUTION_POLICIES)}"
            )
        if self.execution_policy == "offline":
            if self.resource_role is not None or self.supported_transports or self.execution_runtime_roles:
                raise CommandSpecError(
                    "offline commands must not declare a resource role, transport, or runtime role"
                )
            return
        if not self.resource_role:
            raise CommandSpecError("resource-owner commands require a resource role")
        if not self.supported_transports:
            raise CommandSpecError("resource-owner commands require supported transports")
        if not self.execution_runtime_roles:
            raise CommandSpecError("resource-owner commands require execution runtime roles")
        unknown = set(self.supported_transports) - _TRANSPORTS
        if unknown:
            raise CommandSpecError(f"unsupported declared transports: {sorted(unknown)}")
        if self.recovery_capable and "ssh" not in self.supported_transports:
            raise CommandSpecError("recovery-capable commands must declare ssh support")


OperationSpec = CommandSpec


@dataclass(frozen=True)
class ExecutionPlan:
    """Fully resolved, side-effect-free execution context (R009)."""

    command: CommandSpec
    topology_id: str
    topology_snapshot: str
    command_host: Host | None
    command_runtime: Runtime | None
    execution_host: Host | None
    execution_runtime: Runtime | None
    resource_host: Host | None
    resource_runtime: Runtime | None
    resource: Resource | None
    transport: str | None
    transport_id: str | None
    transport_endpoint: str | None
    transport_host_key_fingerprint: str | None
    transport_known_hosts_path: str | None
    recovery_transport_id: str | None
    recovery_transport_endpoint: str | None
    recovery_host_key_fingerprint: str | None
    recovery_known_hosts_path: str | None
    resource_endpoint: str | None
    gpu_role: GpuRole | None
    selected_target: str | None
    overlay: str | None = None
    capacity: CapacityDecision | None = None

    @property
    def endpoint(self) -> str | None:
        """The endpoint used by the selected transport, when one is required."""
        return self.transport_endpoint

    def as_dict(self) -> dict[str, object]:
        """Return stable context suitable for bounded output envelopes."""
        controller_endpoint = self.transport_endpoint if self.transport == "controller" else None
        context = {
            "command": self.command.name,
            "topology": self.topology_id,
            "topology_snapshot": self.topology_snapshot,
            "overlay": self.overlay,
            "command_host": _host_id(self.command_host),
            "command_runtime": _runtime_id(self.command_runtime),
            "execution_host": _host_id(self.execution_host),
            "execution_runtime": _runtime_id(self.execution_runtime),
            "resource_host": _host_id(self.resource_host),
            "resource_runtime": _runtime_id(self.resource_runtime),
            "resource": self.resource.id if self.resource else None,
            "transport": self.transport,
            "transport_id": self.transport_id,
            "endpoint": self.endpoint,
            "transport_endpoint": self.transport_endpoint,
            "transport_endpoint_kind": self.transport,
            "recovery_transport_endpoint": self.recovery_transport_endpoint,
            "recovery_transport_id": self.recovery_transport_id,
            "recovery_transport_endpoint_kind": (
                "ssh" if self.recovery_transport_endpoint else None
            ),
            "controller_endpoint": controller_endpoint,
            "controller_endpoint_kind": "controller" if controller_endpoint else None,
            "resource_endpoint": self.resource_endpoint,
            "resource_endpoint_kind": self.resource.endpoint_kind if self.resource else None,
            "gpu_role": self.gpu_role.id if self.gpu_role else None,
            "gpu_uuid": self.gpu_role.uuid if self.gpu_role else None,
            "target": self.selected_target,
        }
        if self.capacity is not None:
            context.update(self.capacity.as_dict())
        return context

    @property
    def warnings(self) -> tuple[str, ...]:
        """Warnings that callers must surface and preserve in audit output."""
        if self.capacity is None or self.capacity.warning is None:
            return ()
        return (self.capacity.warning,)


@dataclass(frozen=True)
class ExecutionPreflight:
    """Validated resource ownership and capacity before transport selection."""

    command: CommandSpec
    topology_id: str
    topology_snapshot: str
    command_identity: CommandIdentity
    execution_host: Host
    execution_runtime: Runtime
    resource: Resource
    gpu_role: GpuRole | None
    selected_target: str | None
    overlay: str | None
    capacity: CapacityDecision


def resolve_execution_plan(
    topology: Topology,
    command: CommandSpec,
    *,
    target: str | None = None,
    transport: str = "auto",
    command_identity: CommandIdentity | None = None,
    command_host: str | None = None,
    command_runtime: str | None = None,
    environment: Mapping[str, str] | None = None,
    overlay: str | None = None,
    experimental_model_workload: bool = False,
) -> ExecutionPlan:
    """Resolve command, target, owner, and transport without performing I/O."""
    if command.execution_policy == "offline":
        if target is not None or transport not in {"auto", "local"}:
            raise TargetResolutionError(
                "offline commands do not accept a target or remote transport", exit_class="usage"
            )
        return ExecutionPlan(
            command=command,
            topology_id=topology.id,
            topology_snapshot=topology_snapshot_identity(topology),
            command_host=None,
            command_runtime=None,
            execution_host=None,
            execution_runtime=None,
            resource_host=None,
            resource_runtime=None,
            resource=None,
            transport=None,
            transport_id=None,
            transport_endpoint=None,
            transport_host_key_fingerprint=None,
            transport_known_hosts_path=None,
            recovery_transport_id=None,
            recovery_transport_endpoint=None,
            recovery_host_key_fingerprint=None,
            recovery_known_hosts_path=None,
            resource_endpoint=None,
            gpu_role=None,
            selected_target=target,
            overlay=overlay,
        )

    _validate_requested_transport(transport)
    preflight = preflight_execution_plan(
        topology,
        command,
        target=target,
        command_identity=command_identity,
        command_host=command_host,
        command_runtime=command_runtime,
        environment=environment,
        overlay=overlay,
        experimental_model_workload=experimental_model_workload,
    )
    return finalize_execution_plan(topology, preflight, transport=transport)


def preflight_execution_plan(
    topology: Topology,
    command: CommandSpec,
    *,
    target: str | None = None,
    command_identity: CommandIdentity | None = None,
    command_host: str | None = None,
    command_runtime: str | None = None,
    environment: Mapping[str, str] | None = None,
    overlay: str | None = None,
    experimental_model_workload: bool = False,
) -> ExecutionPreflight:
    """Validate a resource-owner command completely without selecting transport."""
    if command.execution_policy == "offline":
        raise TargetResolutionError(
            "offline commands do not have a resource-owner preflight", exit_class="usage"
        )
    identity = command_identity or _command_identity(
        topology, command_host=command_host, command_runtime=command_runtime, environment=environment
    )
    resource = _resource_owner(topology, command.resource_role)
    resource_host = _host(topology, resource.host, "resource host")
    resource_runtime = _runtime(topology, resource.runtime, "resource runtime")
    if resource_runtime.host != resource_host.id:
        raise TargetResolutionError(
            f"resource {resource.id!r} runtime does not belong to its resource host"
        )
    if resource_runtime.role not in command.execution_runtime_roles:
        raise TargetResolutionError(
            f"command {command.name!r} does not support execution runtime role {resource_runtime.role!r}"
        )
    capacity = _capacity_decision(
        topology,
        resource_host,
        resource,
        experimental_model_workload=experimental_model_workload,
    )
    if not capacity.allowed:
        assert capacity.reason is not None
        raise TargetResolutionError(capacity.reason, details=capacity.as_dict())
    selected_target = _target_host(topology, target)
    if selected_target is not None and selected_target.id != resource_host.id:
        raise TargetResolutionError(
            f"target {selected_target.id!r} does not own resource role {command.resource_role!r}"
        )
    gpu_role = _gpu_role(topology, resource, command)
    return ExecutionPreflight(
        command=command,
        topology_id=topology.id,
        topology_snapshot=topology_snapshot_identity(topology),
        command_identity=identity,
        execution_host=resource_host,
        execution_runtime=resource_runtime,
        resource=resource,
        gpu_role=gpu_role,
        selected_target=target,
        overlay=overlay,
        capacity=capacity,
    )


def finalize_execution_plan(
    topology: Topology,
    preflight: ExecutionPreflight,
    *,
    transport: str = "auto",
) -> ExecutionPlan:
    """Select transport and build a plan from an already validated preflight."""
    _validate_requested_transport(transport)
    if (
        preflight.topology_id != topology.id
        or preflight.topology_snapshot != topology_snapshot_identity(topology)
    ):
        raise TargetResolutionError("execution preflight belongs to a stale or different topology")
    command = preflight.command
    selected_transport, selected_transport_record = _select_transport(
        topology,
        command,
        preflight.command_identity,
        preflight.execution_host,
        preflight.execution_runtime,
        transport,
    )
    recovery_transport = None
    if command.recovery_capable:
        recovery_transport = _transport(
            topology,
            command.name,
            "ssh",
            preflight.execution_host.id,
            preflight.execution_runtime.id,
        )
        _validate_transport_owner(
            recovery_transport,
            preflight.command_identity.host,
            preflight.execution_host,
        )
    return ExecutionPlan(
        command=command,
        topology_id=topology.id,
        topology_snapshot=preflight.topology_snapshot,
        command_host=preflight.command_identity.host,
        command_runtime=preflight.command_identity.runtime,
        execution_host=preflight.execution_host,
        execution_runtime=preflight.execution_runtime,
        resource_host=preflight.execution_host,
        resource_runtime=preflight.execution_runtime,
        resource=preflight.resource,
        transport=selected_transport,
        transport_id=(selected_transport_record.id if selected_transport_record else None),
        transport_endpoint=(
            selected_transport_record.endpoint
            if selected_transport_record
            else preflight.resource.endpoint
        ),
        transport_host_key_fingerprint=(
            selected_transport_record.host_key_fingerprint
            if selected_transport_record
            else None
        ),
        transport_known_hosts_path=(
            selected_transport_record.known_hosts_path
            if selected_transport_record
            else None
        ),
        recovery_transport_id=(recovery_transport.id if recovery_transport else None),
        recovery_transport_endpoint=(recovery_transport.endpoint if recovery_transport else None),
        recovery_host_key_fingerprint=(
            recovery_transport.host_key_fingerprint if recovery_transport else None
        ),
        recovery_known_hosts_path=(recovery_transport.known_hosts_path if recovery_transport else None),
        resource_endpoint=preflight.resource.endpoint,
        gpu_role=preflight.gpu_role,
        selected_target=preflight.selected_target,
        capacity=preflight.capacity,
        overlay=preflight.overlay,
    )


resolve_target = resolve_execution_plan


def _validate_requested_transport(transport: str) -> None:
    if transport not in _REQUESTED_TRANSPORTS:
        raise TargetResolutionError(
            f"transport must be one of {sorted(_REQUESTED_TRANSPORTS)}", exit_class="usage"
        )


def _capacity_decision(
    topology: Topology,
    resource_host: Host,
    resource: Resource,
    *,
    experimental_model_workload: bool,
) -> CapacityDecision:
    policy = None
    if resource_host.capacity_policy is not None:
        try:
            policy = topology.capacity_policy(resource_host.capacity_policy)
        except KeyError as exc:
            raise TargetResolutionError(f"resource host capacity policy: {exc}") from None
    return evaluate_capacity_policy(
        host_id=resource_host.id,
        workload=resource.workload,
        capacity_policy=policy.id if policy else None,
        allow_model_workloads=policy.allow_model_workloads if policy else False,
        allow_experimental_model_workloads=(
            policy.allow_experimental_model_workloads if policy else False
        ),
        experimental_model_workload=experimental_model_workload,
    )


def _command_identity(
    topology: Topology,
    *,
    command_host: str | None,
    command_runtime: str | None,
    environment: Mapping[str, str] | None,
) -> CommandIdentity:
    try:
        identity = resolve_command_identity(
            topology,
            command_host=command_host,
            command_runtime=command_runtime,
            environment=environment,
        )
    except TopologyResolutionError as exc:
        raise TargetResolutionError(str(exc)) from None
    assert identity is not None
    return identity


def _resource_owner(topology: Topology, role: str | None) -> Resource:
    assert role is not None
    try:
        return topology.resource_owner(role)
    except TopologyResolutionError as exc:
        raise TargetResolutionError(str(exc)) from None


def _target_host(topology: Topology, target: str | None) -> Host | None:
    if target is None:
        return None
    if not isinstance(target, str):
        raise TargetResolutionError("target must be host:<id> or host-role:<role>", exit_class="usage")
    prefix, separator, value = target.partition(":")
    if not separator or not value:
        raise TargetResolutionError("target must be host:<id> or host-role:<role>", exit_class="usage")
    if not _TARGET_ID_RE.fullmatch(value):
        raise TargetResolutionError("target must be host:<id> or host-role:<role>", exit_class="usage")
    if prefix == "host":
        return _host(topology, value, "target host")
    if prefix != "host-role":
        raise TargetResolutionError("target must be host:<id> or host-role:<role>", exit_class="usage")
    matches = tuple(host for host in topology.hosts if value in host.roles)
    if len(matches) != 1:
        raise TargetResolutionError(f"host role {value!r} has {len(matches)} declared owners")
    return matches[0]


def _gpu_role(topology: Topology, resource: Resource, command: CommandSpec) -> GpuRole | None:
    if resource.gpu_role is None:
        if command.gpu_role_required:
            raise TargetResolutionError(f"command {command.name!r} requires a GPU role")
        return None
    try:
        role = topology.gpu_role(resource.gpu_role)
    except KeyError as exc:
        raise TargetResolutionError(str(exc)) from None
    if role.host != resource.host or role.runtime != resource.runtime:
        raise TargetResolutionError(f"GPU role {role.id!r} does not belong to resource {resource.id!r}")
    return role


def _select_transport(
    topology: Topology,
    command: CommandSpec,
    identity: CommandIdentity,
    execution_host: Host,
    execution_runtime: Runtime,
    requested: str,
) -> tuple[str, Transport | None]:
    local = identity.host.id == execution_host.id and identity.runtime.id == execution_runtime.id
    selected = requested
    if requested == "auto":
        selected = "local" if local else "controller"
    if selected not in command.supported_transports:
        raise TargetResolutionError(
            f"command {command.name!r} does not support {selected!r} transport"
        )
    if selected == "local":
        if not local:
            raise TargetResolutionError("local transport requires the command host and runtime to own the target")
        return selected, None
    if selected == "ssh" and not command.recovery_capable:
        raise TargetResolutionError(f"command {command.name!r} is not recovery-capable")
    kind = "controller" if selected == "controller" else "ssh"
    transport = _transport(topology, command.name, kind, execution_host.id, execution_runtime.id)
    _validate_transport_owner(transport, identity.host, execution_host)
    return selected, transport


def _validate_transport_owner(
    transport: Transport,
    command_host: Host,
    execution_host: Host,
) -> None:
    """Bind a selected endpoint to its declared execution-host ownership."""
    try:
        endpoint_host = urllib.parse.urlparse(transport.endpoint).hostname
    except ValueError:
        endpoint_host = None
    if endpoint_host is None:
        raise TargetResolutionError(f"transport {transport.id!r} has no endpoint host")
    try:
        endpoint_address = ipaddress.ip_address(endpoint_host)
    except ValueError:
        endpoint_address = None
    if endpoint_address is not None and endpoint_address.is_loopback:
        if command_host.id != execution_host.id:
            raise TargetResolutionError(
                "loopback transport endpoints require the command host to be the execution host"
            )
        return
    if execution_host.address is None:
        return
    if not _host_addresses_match(endpoint_host, execution_host.address):
        raise TargetResolutionError(
            f"transport {transport.id!r} endpoint does not match execution host {execution_host.id!r}"
        )


def _host_addresses_match(endpoint_host: str, declared_address: str) -> bool:
    try:
        return ipaddress.ip_address(endpoint_host) == ipaddress.ip_address(declared_address)
    except ValueError:
        return endpoint_host.rstrip(".").lower() == declared_address.rstrip(".").lower()


def _transport(
    topology: Topology, command_name: str, kind: str, host_id: str, runtime_id: str
) -> Transport:
    matches = tuple(
        transport
        for transport in topology.transports
        if transport.kind == kind
        and transport.host == host_id
        and transport.runtime == runtime_id
        and command_name in transport.allowed_operations
    )
    if len(matches) != 1:
        raise TargetResolutionError(
            f"{kind} transport for operation {command_name!r} on {host_id!r}/{runtime_id!r} "
            f"has {len(matches)} declared owners"
        )
    return matches[0]


def _host(topology: Topology, host_id: str, label: str) -> Host:
    try:
        return topology.host(host_id)
    except KeyError as exc:
        raise TargetResolutionError(f"{label}: {exc}") from None


def _runtime(topology: Topology, runtime_id: str, label: str) -> Runtime:
    try:
        return topology.runtime(runtime_id)
    except KeyError as exc:
        raise TargetResolutionError(f"{label}: {exc}") from None


def _host_id(host: Host | None) -> str | None:
    return host.id if host else None


def _runtime_id(runtime: Runtime | None) -> str | None:
    return runtime.id if runtime else None
