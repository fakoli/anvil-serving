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
    HostBootstrap,
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
_EXECUTION_POLICIES = frozenset({"resource-owner", "host-bootstrap", "offline"})
_EXIT_CODES = {"usage": 2, "safety": 3}
_TARGET_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_BOOTSTRAP_TARGET_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


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
    execution_host_os: tuple[str, ...] = ()
    execution_policy: str = "resource-owner"

    def __post_init__(self) -> None:
        object.__setattr__(self, "supported_transports", tuple(self.supported_transports))
        object.__setattr__(self, "execution_runtime_roles", tuple(self.execution_runtime_roles))
        object.__setattr__(self, "execution_host_os", tuple(self.execution_host_os))
        if not self.name:
            raise CommandSpecError("command name is required")
        if self.execution_policy not in _EXECUTION_POLICIES:
            raise CommandSpecError(
                f"execution policy must be one of {sorted(_EXECUTION_POLICIES)}"
            )
        if self.execution_policy == "offline":
            if self.resource_role is not None or self.supported_transports or self.execution_runtime_roles or self.execution_host_os:
                raise CommandSpecError(
                    "offline commands must not declare a resource role, transport, or runtime role"
                )
            return
        if self.execution_policy == "host-bootstrap":
            expected = (
                self.name == "controller-bootstrap"
                and self.resource_role is None
                and self.supported_transports == ("controller", "ssh")
                and self.execution_runtime_roles == ("native",)
                and self.mutation_class == "write"
                and self.recovery_capable is True
                and self.gpu_role_required is False
                and self.execution_host_os == ("windows", "linux")
            )
            if not expected:
                raise CommandSpecError("host-bootstrap command declaration is invalid")
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
    transport_auth_env: str | None = None
    transport_allowed_operations: tuple[str, ...] = ()
    # ADR-0033 controller-RPC groundwork: node identity the selected controller
    # must assert on /health before dispatch, when the transport declares it.
    transport_expected_node: str | None = None
    host_bootstrap: HostBootstrap | None = None

    @property
    def endpoint(self) -> str | None:
        """The endpoint used by the selected transport, when one is required."""
        return self.transport_endpoint

    def as_dict(self) -> dict[str, object]:
        """Return stable context suitable for bounded output envelopes."""
        if self.command.execution_policy == "host-bootstrap":
            return {
                "command": self.command.name,
                "topology": self.topology_id,
                "topology_snapshot": self.topology_snapshot,
                "command_host": _host_id(self.command_host),
                "command_runtime": _runtime_id(self.command_runtime),
                "execution_host": _host_id(self.execution_host),
                "execution_runtime": _runtime_id(self.execution_runtime),
                "target": self.selected_target,
                "transport": self.transport,
                "transport_id": self.transport_id,
                "recovery_transport_id": self.recovery_transport_id,
                "expected_node": self.transport_expected_node,
            }
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
            "transport_auth_env": self.transport_auth_env,
            "transport_allowed_operations": list(self.transport_allowed_operations),
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
    resource: Resource | None
    gpu_role: GpuRole | None
    selected_target: str | None
    overlay: str | None
    capacity: CapacityDecision | None
    host_bootstrap: HostBootstrap | None = None


@dataclass(frozen=True)
class ResourceTarget:
    """One explicit resource owner and its one declared controller transport."""

    resource: Resource
    transport: Transport


def resolve_resource_target(topology: Topology, role: str) -> ResourceTarget:
    """Resolve an adapter service target without host or transport inference.

    This is the protocol-neutral seam used by media workflow configuration: a
    role must have exactly one resource owner and that host must have exactly
    one declared controller transport.  Zero or multiple matches fail closed.
    """
    resource = topology.resource_owner(role)
    transports = tuple(
        transport
        for transport in topology.transports
        if transport.kind == "controller" and transport.host == resource.host
    )
    if len(transports) != 1:
        raise TopologyResolutionError(
            f"resource role {role!r} owner {resource.host!r} has "
            f"{len(transports)} declared controller transports; declare exactly one"
        )
    return ResourceTarget(resource=resource, transport=transports[0])


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

    if command.execution_policy == "host-bootstrap":
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
    if command.execution_policy == "host-bootstrap":
        return _preflight_host_bootstrap(
            topology,
            command,
            target=target,
            command_identity=command_identity,
            command_host=command_host,
            command_runtime=command_runtime,
            environment=environment,
            overlay=overlay,
        )
    identity = command_identity or _command_identity(
        topology, command_host=command_host, command_runtime=command_runtime, environment=environment
    )
    selected_target = _target_host(topology, target)
    resource = _resource_owner(
        topology, command.resource_role, selected_target, command_host=identity.host
    )
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
    if command.execution_host_os and resource_host.os not in command.execution_host_os:
        declared = resource_host.os or "unspecified"
        raise TargetResolutionError(
            f"command {command.name!r} does not support host OS {declared!r}; "
            f"requires one of {list(command.execution_host_os)!r}",
            exit_class="safety",
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
    if (
        preflight.topology_id != topology.id
        or preflight.topology_snapshot != topology_snapshot_identity(topology)
    ):
        raise TargetResolutionError("execution preflight belongs to a stale or different topology")
    command = preflight.command
    if command.execution_policy == "host-bootstrap":
        return _finalize_host_bootstrap(topology, preflight, transport=transport)
    _validate_requested_transport(transport)
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
        recovery_transport = _optional_transport(
            topology,
            command.name,
            "ssh",
            preflight.execution_host.id,
            preflight.execution_runtime.id,
        )
        if recovery_transport is not None:
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
        transport_auth_env=(
            selected_transport_record.auth_env if selected_transport_record else None
        ),
        transport_allowed_operations=(
            selected_transport_record.allowed_operations if selected_transport_record else ()
        ),
        transport_expected_node=(
            selected_transport_record.expected_node if selected_transport_record else None
        ),
    )


resolve_target = resolve_execution_plan


_BOOTSTRAP_MESSAGES = {
    "bootstrap-target-required": "bootstrap requires one explicit host target",
    "bootstrap-host-missing": "bootstrap target host is not declared",
    "bootstrap-contract-missing": "bootstrap target has no bootstrap declaration",
    "bootstrap-disabled": "bootstrap is disabled for the target host",
    "bootstrap-authorization-denied": "bootstrap is not authorized for the target host",
    "bootstrap-runtime-invalid": "bootstrap execution runtime is invalid",
    "bootstrap-controller-missing": "bootstrap controller transport is missing",
    "bootstrap-controller-ambiguous": "bootstrap controller transport is ambiguous",
    "bootstrap-controller-identity-invalid": "bootstrap controller identity is invalid",
    "bootstrap-recovery-ambiguous": "bootstrap recovery transport is ambiguous",
    "bootstrap-transport-invalid": "bootstrap transport selection is invalid",
    "bootstrap-command-identity-invalid": "bootstrap command identity is invalid",
}


def _bootstrap_refusal(reason_code: str) -> TargetResolutionError:
    return TargetResolutionError(
        _BOOTSTRAP_MESSAGES[reason_code],
        details={"reason_code": reason_code},
    )


def _preflight_host_bootstrap(
    topology: Topology,
    command: CommandSpec,
    *,
    target: str | None,
    command_identity: CommandIdentity | None,
    command_host: str | None,
    command_runtime: str | None,
    environment: Mapping[str, str] | None,
    overlay: str | None,
) -> ExecutionPreflight:
    host = _bootstrap_target_host(topology, target)
    bootstrap = host.bootstrap
    if bootstrap is None:
        raise _bootstrap_refusal("bootstrap-contract-missing")
    if not bootstrap.enabled:
        raise _bootstrap_refusal("bootstrap-disabled")
    if not bootstrap.bootstrap_authorized:
        raise _bootstrap_refusal("bootstrap-authorization-denied")
    try:
        runtime = topology.runtime(bootstrap.execution_runtime)
    except KeyError:
        raise _bootstrap_refusal("bootstrap-runtime-invalid") from None
    if runtime.host != host.id or runtime.role != "native" or host.os not in command.execution_host_os:
        raise _bootstrap_refusal("bootstrap-runtime-invalid")
    identity = _bootstrap_command_identity(
        topology,
        command_identity=command_identity,
        command_host=command_host,
        command_runtime=command_runtime,
        environment=environment,
    )
    return ExecutionPreflight(
        command=command,
        topology_id=topology.id,
        topology_snapshot=topology_snapshot_identity(topology),
        command_identity=identity,
        execution_host=host,
        execution_runtime=runtime,
        resource=None,
        gpu_role=None,
        selected_target=target,
        overlay=overlay,
        capacity=None,
        host_bootstrap=bootstrap,
    )


def _bootstrap_target_host(topology: Topology, target: str | None) -> Host:
    if type(target) is not str:
        raise _bootstrap_refusal("bootstrap-target-required")
    prefix, separator, value = target.partition(":")
    if prefix != "host" or not separator or _BOOTSTRAP_TARGET_ID_RE.fullmatch(value) is None:
        raise _bootstrap_refusal("bootstrap-target-required")
    try:
        return topology.host(value)
    except KeyError:
        raise _bootstrap_refusal("bootstrap-host-missing") from None


def _bootstrap_command_identity(
    topology: Topology,
    *,
    command_identity: CommandIdentity | None,
    command_host: str | None,
    command_runtime: str | None,
    environment: Mapping[str, str] | None,
) -> CommandIdentity:
    try:
        if command_identity is not None:
            declared_host = topology.host(command_identity.host.id)
            declared_runtime = topology.runtime(command_identity.runtime.id)
            if (
                declared_host != command_identity.host
                or declared_runtime != command_identity.runtime
                or declared_runtime.host != declared_host.id
            ):
                raise _bootstrap_refusal("bootstrap-command-identity-invalid")
            return command_identity
        return _command_identity(
            topology,
            command_host=command_host,
            command_runtime=command_runtime,
            environment=environment,
        )
    except Exception:
        raise _bootstrap_refusal("bootstrap-command-identity-invalid") from None


def _finalize_host_bootstrap(
    topology: Topology,
    preflight: ExecutionPreflight,
    *,
    transport: str,
) -> ExecutionPlan:
    if transport not in {"auto", "controller"}:
        raise _bootstrap_refusal("bootstrap-transport-invalid")
    host = preflight.execution_host
    runtime = preflight.execution_runtime
    bootstrap = preflight.host_bootstrap
    if bootstrap is None or host is None or runtime is None:
        raise _bootstrap_refusal("bootstrap-runtime-invalid")
    controllers = _bootstrap_transports(topology, "controller", host.id, runtime.id)
    if not controllers:
        raise _bootstrap_refusal("bootstrap-controller-missing")
    if len(controllers) != 1:
        raise _bootstrap_refusal("bootstrap-controller-ambiguous")
    controller = controllers[0]
    if (
        controller.expected_node != host.id
        or not controller.auth_env
        or controller.allow_unauthenticated_loopback
    ):
        raise _bootstrap_refusal("bootstrap-controller-identity-invalid")
    try:
        _validate_transport_owner(controller, preflight.command_identity.host, host)
    except TargetResolutionError:
        raise _bootstrap_refusal("bootstrap-transport-invalid") from None
    recovery_matches = _bootstrap_transports(topology, "ssh", host.id, runtime.id)
    if len(recovery_matches) > 1:
        raise _bootstrap_refusal("bootstrap-recovery-ambiguous")
    recovery = recovery_matches[0] if recovery_matches else None
    if recovery is not None:
        if (
            not recovery.host_key_fingerprint
            or not recovery.known_hosts_path
            or recovery.allow_unauthenticated_loopback
        ):
            raise _bootstrap_refusal("bootstrap-transport-invalid")
        try:
            _validate_transport_owner(recovery, preflight.command_identity.host, host)
        except TargetResolutionError:
            raise _bootstrap_refusal("bootstrap-transport-invalid") from None
    return ExecutionPlan(
        command=preflight.command,
        topology_id=topology.id,
        topology_snapshot=preflight.topology_snapshot,
        command_host=preflight.command_identity.host,
        command_runtime=preflight.command_identity.runtime,
        execution_host=host,
        execution_runtime=runtime,
        resource_host=None,
        resource_runtime=None,
        resource=None,
        transport="controller",
        transport_id=controller.id,
        transport_endpoint=controller.endpoint,
        transport_host_key_fingerprint=controller.host_key_fingerprint,
        transport_known_hosts_path=controller.known_hosts_path,
        recovery_transport_id=recovery.id if recovery else None,
        recovery_transport_endpoint=recovery.endpoint if recovery else None,
        recovery_host_key_fingerprint=recovery.host_key_fingerprint if recovery else None,
        recovery_known_hosts_path=recovery.known_hosts_path if recovery else None,
        resource_endpoint=None,
        gpu_role=None,
        selected_target=preflight.selected_target,
        overlay=preflight.overlay,
        capacity=None,
        transport_auth_env=controller.auth_env,
        transport_allowed_operations=controller.allowed_operations,
        transport_expected_node=controller.expected_node,
        host_bootstrap=bootstrap,
    )


def _bootstrap_transports(
    topology: Topology,
    kind: str,
    host_id: str,
    runtime_id: str,
) -> tuple[Transport, ...]:
    return tuple(
        item
        for item in topology.transports
        if item.kind == kind
        and item.host == host_id
        and item.runtime == runtime_id
        and "controller-bootstrap" in item.allowed_operations
    )


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


def _resource_owner(
    topology: Topology,
    role: str | None,
    target: Host | None = None,
    *,
    command_host: Host | None = None,
) -> Resource:
    assert role is not None
    matches = tuple(
        resource
        for resource in topology.resources
        if resource.role == role and (target is None or resource.host == target.id)
    )
    if len(matches) > 1 and target is None and command_host is not None:
        # ADR-0033: resource roles are unique per host, not globally. With no
        # explicit --target, the command identity's own host disambiguates —
        # and only that host; this never guesses among remote owners.
        local_matches = tuple(
            resource for resource in matches if resource.host == command_host.id
        )
        if len(local_matches) == 1:
            return local_matches[0]
    if len(matches) != 1:
        target_suffix = f" on target {target.id!r}" if target is not None else ""
        owners = sorted({resource.host for resource in matches})
        owner_suffix = (
            f"; owners on hosts {owners!r}, select with --target host:<id>"
            if len(matches) > 1
            else ""
        )
        raise TargetResolutionError(
            f"resource role {role!r}{target_suffix} has {len(matches)} declared owners"
            + owner_suffix
        )
    return matches[0]


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


# A host's native shell drives its own docker daemon; that is the only
# cross-runtime pair that is genuinely local (ADR-0033;
# .tickets/closed/2026-08-07-local-transport-runtime-equality-friction.md).
_NATIVE_OPERATES_RUNTIME_ROLES = frozenset({"docker"})


def _select_transport(
    topology: Topology,
    command: CommandSpec,
    identity: CommandIdentity,
    execution_host: Host,
    execution_runtime: Runtime,
    requested: str,
) -> tuple[str, Transport | None]:
    same_host = identity.host.id == execution_host.id
    local = same_host and (
        identity.runtime.id == execution_runtime.id
        or (
            identity.runtime.role == "native"
            and execution_runtime.role in _NATIVE_OPERATES_RUNTIME_ROLES
        )
    )
    selected = requested
    if requested == "auto":
        selected = "local" if local else "controller"
    if selected not in command.supported_transports:
        raise TargetResolutionError(
            f"command {command.name!r} does not support {selected!r} transport"
        )
    if selected == "local":
        if not local:
            raise TargetResolutionError(
                "local transport requires the command host to own the target and "
                "the runtimes to match (a native shell may operate its own "
                "host's docker runtime)"
            )
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


def _optional_transport(
    topology: Topology, command_name: str, kind: str, host_id: str, runtime_id: str
) -> Transport | None:
    matches = tuple(
        transport
        for transport in topology.transports
        if transport.kind == kind
        and transport.host == host_id
        and transport.runtime == runtime_id
        and command_name in transport.allowed_operations
    )
    if len(matches) > 1:
        raise TargetResolutionError(
            f"{kind} transport for operation {command_name!r} on {host_id!r}/{runtime_id!r} "
            f"has {len(matches)} declared owners"
        )
    return matches[0] if matches else None


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
