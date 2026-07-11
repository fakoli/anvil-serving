from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from anvil_serving import targets
from anvil_serving.targets import (
    CommandSpec,
    TargetResolutionError,
    finalize_execution_plan,
    preflight_execution_plan,
    resolve_execution_plan,
)
from anvil_serving.topology import SCHEMA_VERSION, Topology, parse_topology, validate_topology


def _topology_data() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "id": "synthetic-targets",
        "command_host": "host:operator",
        "command_runtime": "runtime:operator-native",
        "capacity_policies": [
            {"id": "model-free", "allow_model_workloads": False},
            {"id": "model-capable", "allow_model_workloads": True},
        ],
        "hosts": [
            {"id": "operator", "roles": ["operator"], "address": "127.0.0.1"},
            {
                "id": "dark",
                "roles": ["router", "serve"],
                "address": "100.64.0.10",
                "capacity_policy": "model-capable",
            },
            {
                "id": "mini",
                "roles": ["gateway", "proxy"],
                "address": "100.64.0.11",
                "capacity_policy": "model-free",
            },
        ],
        "runtimes": [
            {"id": "operator-native", "host": "operator", "role": "native"},
            {"id": "dark-docker", "host": "dark", "role": "docker"},
            {"id": "mini-native", "host": "mini", "role": "native"},
        ],
        "gpu_roles": [
            {
                "id": "fast",
                "host": "dark",
                "runtime": "dark-docker",
                "uuid": "GPU-01234567-89ab-cdef-0123-456789abcdef",
            }
        ],
        "resources": [
            {
                "id": "router",
                "role": "router",
                "host": "dark",
                "runtime": "dark-docker",
                "endpoint": "http://100.64.0.10:8000/v1",
            },
            {
                "id": "model-serve",
                "role": "model-serve",
                "host": "dark",
                "runtime": "dark-docker",
                "gpu_role": "fast",
                "workload": "llm",
            },
            {
                "id": "realtime-proxy",
                "role": "realtime-proxy",
                "host": "mini",
                "runtime": "mini-native",
                "endpoint": "http://127.0.0.1:8765/v1",
                "endpoint_kind": "host-relative-loopback",
                "workload": "service",
            },
        ],
        "transports": [
            {
                "id": "dark-controller",
                "kind": "controller",
                "host": "dark",
                "runtime": "dark-docker",
                "endpoint": "http://100.64.0.10:8766",
                "auth_env": "ANVIL_CONTROLLER_TOKEN",
                "allowed_operations": ["router-status"],
            },
            {
                "id": "dark-ssh-recovery",
                "kind": "ssh",
                "host": "dark",
                "runtime": "dark-docker",
                "endpoint": "ssh://operator@100.64.0.10:22",
                "allowed_operations": ["router-status"],
                "host_key_fingerprint": "SHA256:synthetic",
                "known_hosts_path": "~/.ssh/known_hosts",
            },
            {
                "id": "mini-controller",
                "kind": "controller",
                "host": "mini",
                "runtime": "mini-native",
                "endpoint": "http://100.64.0.11:8766",
                "auth_env": "ANVIL_CONTROLLER_TOKEN",
                "allowed_operations": ["proxy-status"],
            },
        ],
    }


def _spec(name: str, role: str, *, transports: tuple[str, ...] = ("local", "controller")) -> CommandSpec:
    return CommandSpec(
        name=name,
        resource_role=role,
        supported_transports=transports,
        execution_runtime_roles=("docker", "native"),
        mutation_class="read",
        recovery_capable="ssh" in transports,
        gpu_role_required=False,
    )


def test_local_target_selects_the_command_host_without_transport_endpoint():
    plan = resolve_execution_plan(
        parse_topology(_topology_data()),
        _spec("router-status", "router"),
        target="host-role:router",
        command_host="host:dark",
        command_runtime="runtime:dark-docker",
    )

    assert plan.transport == "local"
    assert plan.command_host is plan.execution_host is plan.resource_host
    assert plan.transport_endpoint == "http://100.64.0.10:8000/v1"


def test_controller_target_selects_the_resource_owner_and_declared_controller():
    plan = resolve_execution_plan(
        parse_topology(_topology_data()),
        _spec("router-status", "router", transports=("controller", "ssh")),
        target="host:dark",
    )

    assert plan.transport == "controller"
    assert plan.command_host is not plan.execution_host
    assert plan.transport_endpoint == "http://100.64.0.10:8766"
    assert plan.recovery_transport_endpoint == "ssh://operator@100.64.0.10:22"
    assert plan.as_dict()["endpoint"] == "http://100.64.0.10:8766"
    assert plan.transport_id == "dark-controller"
    assert plan.recovery_transport_id == "dark-ssh-recovery"
    assert plan.recovery_host_key_fingerprint == "SHA256:synthetic"
    assert plan.recovery_known_hosts_path == "~/.ssh/known_hosts"


def test_ssh_target_requires_a_declared_recovery_operation():
    plan = resolve_execution_plan(
        parse_topology(_topology_data()),
        _spec("router-status", "router", transports=("controller", "ssh")),
        target="host:dark",
        transport="ssh",
    )

    assert plan.transport == "ssh"
    assert plan.transport_endpoint == "ssh://operator@100.64.0.10:22"


def test_local_transport_refuses_a_resource_owned_by_another_command_host():
    with pytest.raises(TargetResolutionError) as excinfo:
        resolve_execution_plan(
            parse_topology(_topology_data()),
            _spec("router-status", "router"),
            target="host:dark",
            transport="local",
        )

    assert excinfo.value.metadata == {"exit_class": "safety", "exit_code": 3}


def test_ambiguous_resource_owner_refuses_with_safety_exit_metadata():
    topology = parse_topology(_topology_data())
    duplicate = replace(topology.resource("router"), id="router-copy")
    ambiguous = Topology(
        id=topology.id,
        schema_version=topology.schema_version,
        hosts=topology.hosts,
        runtimes=topology.runtimes,
        resources=topology.resources + (duplicate,),
        gpu_roles=topology.gpu_roles,
        transports=topology.transports,
        capacity_policies=topology.capacity_policies,
        command_host=topology.command_host,
        command_runtime=topology.command_runtime,
    )

    with pytest.raises(TargetResolutionError) as excinfo:
        resolve_execution_plan(ambiguous, _spec("router-status", "router"))

    assert excinfo.value.metadata == {"exit_class": "safety", "exit_code": 3}


def test_explicit_target_disambiguates_same_role_across_hosts():
    topology = parse_topology(_topology_data())
    duplicate = replace(
        topology.resource("router"),
        id="mini-router-status",
        host="mini",
        runtime="mini-native",
        endpoint="http://127.0.0.1:8000/v1",
    )
    multi_host = replace(topology, resources=topology.resources + (duplicate,))
    plan = resolve_execution_plan(
        multi_host,
        _spec("router-status", "router"),
        target="host:mini",
        command_host="host:mini",
        command_runtime="runtime:mini-native",
    )
    assert plan.resource is duplicate
    assert plan.transport == "local"


def test_command_host_os_restriction_fails_before_transport_selection():
    topology = parse_topology(_topology_data())
    hosts = tuple(replace(item, os="linux") if item.id == "dark" else item for item in topology.hosts)
    topology = replace(topology, hosts=hosts)
    spec = replace(_spec("host-wsl-config", "router"), execution_host_os=("windows",))
    with pytest.raises(TargetResolutionError, match="does not support host OS") as excinfo:
        resolve_execution_plan(topology, spec, target="host:dark")
    assert excinfo.value.exit_code == 3


def test_missing_resource_owner_refuses_with_safety_exit_metadata():
    with pytest.raises(TargetResolutionError) as excinfo:
        resolve_execution_plan(parse_topology(_topology_data()), _spec("missing-status", "missing"))

    assert excinfo.value.metadata == {"exit_class": "safety", "exit_code": 3}


def test_host_relative_loopback_remains_bound_to_the_resolved_resource_host():
    plan = resolve_execution_plan(
        parse_topology(_topology_data()), _spec("proxy-status", "realtime-proxy"), target="host-role:proxy"
    )

    assert plan.execution_host.id == plan.resource_host.id == "mini"
    assert plan.resource_endpoint == "http://127.0.0.1:8765/v1"
    assert plan.transport_endpoint == "http://100.64.0.11:8766"


def test_controller_endpoint_must_match_declared_execution_host_before_dispatch():
    data = _topology_data()
    data["transports"][0]["endpoint"] = "http://100.64.0.11:8766"

    with pytest.raises(TargetResolutionError, match="does not match execution host"):
        resolve_execution_plan(
            parse_topology(data),
            _spec("router-status", "router", transports=("controller", "ssh")),
        )


def test_controller_loopback_is_only_valid_on_the_command_host():
    data = _topology_data()
    data["transports"][0]["endpoint"] = "http://127.0.0.1:8766"
    topology = parse_topology(data)
    command = _spec("router-status", "router", transports=("controller", "ssh"))

    with pytest.raises(TargetResolutionError, match="loopback transport"):
        resolve_execution_plan(topology, command, transport="controller")

    plan = resolve_execution_plan(
        topology,
        command,
        transport="controller",
        command_host="host:dark",
        command_runtime="runtime:dark-docker",
    )
    assert plan.transport_endpoint == "http://127.0.0.1:8766"


def test_generic_host_without_address_accepts_explicit_private_controller_endpoint():
    data = _topology_data()
    data["hosts"][1].pop("address")

    plan = resolve_execution_plan(
        parse_topology(data),
        _spec("router-status", "router", transports=("controller", "ssh")),
    )

    assert plan.transport_endpoint == "http://100.64.0.10:8766"


def test_staged_resolution_rejects_changed_topology_content_under_same_id():
    first = parse_topology(_topology_data())
    preflight = preflight_execution_plan(first, _spec("router-status", "router"))
    changed_data = _topology_data()
    changed_data["resources"][0]["endpoint"] = "http://100.64.0.10:9000/v1"
    changed = parse_topology(changed_data)

    with pytest.raises(TargetResolutionError, match="stale or different topology"):
        finalize_execution_plan(changed, preflight)


def test_mini_model_free_policy_refuses_a_model_resource_before_target_resolution():
    data = _topology_data()
    invalid_model = copy.deepcopy(data["resources"][1])
    invalid_model.update(id="mini-model", role="mini-model", host="mini", runtime="mini-native")
    invalid_model.pop("gpu_role")
    data["resources"].append(invalid_model)

    errors = validate_topology(data).errors

    assert any(error.code == "capacity_policy" and error.path == "resources[3].host" for error in errors)


def _mini_experimental_topology():
    data = _topology_data()
    data["capacity_policies"][0]["allow_experimental_model_workloads"] = True
    data["resources"].append(
        {
            "id": "mini-experimental-model",
            "role": "mini-experimental-model",
            "host": "mini",
            "runtime": "mini-native",
            "workload": "experimental-model",
        }
    )
    data["transports"][2]["allowed_operations"].append("mini-model-start")
    return parse_topology(data)


def test_model_free_capacity_rejects_before_transport_selection(monkeypatch):
    selected = []

    def transport_must_not_run(*args, **kwargs):
        selected.append((args, kwargs))
        raise AssertionError("transport selection is a side effect boundary")

    monkeypatch.setattr(targets, "_select_transport", transport_must_not_run)
    with pytest.raises(TargetResolutionError) as excinfo:
        resolve_execution_plan(
            _mini_experimental_topology(),
            _spec("mini-model-start", "mini-experimental-model"),
            target="host:mini",
        )

    assert selected == []
    assert excinfo.value.exit_code == 3
    assert excinfo.value.metadata["experimental_model_workload_requested"] is False
    assert excinfo.value.metadata["experimental_model_workload_permitted"] is True
    assert excinfo.value.metadata["experimental_model_workload_override"] is False


def test_model_free_capacity_override_requires_both_parts_and_reports_audit_state():
    plan = resolve_execution_plan(
        _mini_experimental_topology(),
        _spec("mini-model-start", "mini-experimental-model"),
        target="host:mini",
        experimental_model_workload=True,
    )

    assert plan.resource_host.id == "mini"
    assert plan.transport == "controller"
    assert plan.as_dict()["capacity_policy"] == "model-free"
    assert plan.as_dict()["experimental_model_workload_override"] is True
    assert plan.warnings and "experimental model workload override active" in plan.warnings[0]


def test_dark_model_owner_remains_normally_model_capable():
    plan = resolve_execution_plan(
        parse_topology(_topology_data()),
        _spec("model-status", "model-serve"),
        command_host="host:dark",
        command_runtime="runtime:dark-docker",
    )

    assert plan.resource_host.id == "dark"
    assert plan.as_dict()["capacity_policy"] == "model-capable"
    assert plan.as_dict()["experimental_model_workload_override"] is False
    assert plan.warnings == ()


def test_gpu_role_context_is_stable_uuid_identity_not_a_runtime_index():
    plan = resolve_execution_plan(
        parse_topology(_topology_data()),
        CommandSpec(
            name="model-status",
            resource_role="model-serve",
            supported_transports=("local",),
            execution_runtime_roles=("docker",),
            mutation_class="read",
            recovery_capable=False,
            gpu_role_required=True,
        ),
        command_host="host:dark",
        command_runtime="runtime:dark-docker",
    )

    assert plan.as_dict()["gpu_role"] == "fast"
    assert plan.as_dict()["gpu_uuid"] == "GPU-01234567-89ab-cdef-0123-456789abcdef"
    assert "index" not in plan.as_dict()
