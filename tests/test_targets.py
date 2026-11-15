from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

import pytest

from anvil_serving.targets import (
    CommandSpec,
    TargetResolutionError,
    finalize_execution_plan,
    preflight_execution_plan,
    resolve_execution_plan,
)
from anvil_serving.topology import (
    SCHEMA_VERSION, Topology, load_topology, parse_topology, validate_topology,
)
from anvil_serving.topology_cli import _command_node, _spec as command_tree_spec


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


def _bootstrap_spec(**changes) -> CommandSpec:
    values = {
        "name": "controller-bootstrap",
        "resource_role": None,
        "supported_transports": ("controller", "ssh"),
        "execution_runtime_roles": ("native",),
        "mutation_class": "write",
        "recovery_capable": True,
        "gpu_role_required": False,
        "execution_host_os": ("windows", "linux"),
        "execution_policy": "host-bootstrap",
    }
    values.update(changes)
    return CommandSpec(**values)


def _bootstrap_topology(*, recovery: bool = True):
    data = _topology_data()
    data["hosts"][1]["os"] = "linux"
    data["hosts"][1]["bootstrap"] = {
        "enabled": True,
        "bootstrap_authorized": True,
        "execution_runtime": "dark-native",
        "staging_root": "/var/tmp/private-stage",
        "install_root": "/opt/private-install",
        "python_executable": "/usr/bin/private-python",
        "receiver_path": "/opt/private-receiver/bootstrap.py",
        "receiver_sha256": "a" * 64,
        "install_adapter": "python-wheel-venv",
        "supervisor_adapter": "linux-systemd-user",
        "supervisor_id": "anvil-controller",
    }
    data["runtimes"].append({"id": "dark-native", "host": "dark", "role": "native"})
    data["transports"].append(
        {
            "id": "dark-bootstrap-controller",
            "kind": "controller",
            "host": "dark",
            "runtime": "dark-native",
            "endpoint": "http://100.64.0.10:8767",
            "auth_env": "SYNTHETIC_BOOTSTRAP_TOKEN",
            "allowed_operations": ["controller-bootstrap"],
            "expected_node": "dark",
        }
    )
    if recovery:
        data["transports"].append(
            {
                "id": "dark-bootstrap-recovery",
                "kind": "ssh",
                "host": "dark",
                "runtime": "dark-native",
                "endpoint": "ssh://operator@100.64.0.10:22",
                "allowed_operations": ["controller-bootstrap"],
                "host_key_fingerprint": "SHA256:synthetic-bootstrap",
                "known_hosts_path": "C:/synthetic/private-known-hosts",
            }
        )
    return parse_topology(data)


def test_ssh_target_requires_a_declared_recovery_operation():
    plan = resolve_execution_plan(
        parse_topology(_topology_data()),
        _spec("router-status", "router", transports=("controller", "ssh")),
        target="host:dark",
        transport="ssh",
    )

    assert plan.transport == "ssh"
    assert plan.transport_endpoint == "ssh://operator@100.64.0.10:22"


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


# ---- gpu_roles reservation capacity (ADR-0017) -------------------------------


# --- ADR-0033: same-host locality rule and per-host role scoping ------------


_REFERENCE_TOPOLOGY = (
    Path(__file__).resolve().parents[1] / "examples/primary-node/operator-topology.toml"
)


@pytest.mark.parametrize("verb", ("inspect", "logs"))
def test_generic_diagnostic_permission_refuses_without_fallback(verb):
    topology = load_topology(_REFERENCE_TOPOLOGY)
    operation = "controller-" + verb
    transports = tuple(
        replace(item, allowed_operations=tuple(
            value for value in item.allowed_operations if value != operation
        )) if item.id == "dark-controller" else item
        for item in topology.transports
    )
    topology = replace(topology, transports=transports)
    path, node = _command_node("controller " + verb)

    with pytest.raises(TargetResolutionError):
        resolve_execution_plan(
            topology, command_tree_spec(path, node), target="host:primary-node"
        )


def test_generic_diagnostic_permissions_are_scoped_and_scaffold_identical():
    topology = load_topology(_REFERENCE_TOPOLOGY)
    diagnostic_operations = {"controller-inspect", "controller-logs"}
    for transport in topology.transports:
        found = diagnostic_operations.intersection(transport.allowed_operations)
        assert found == (diagnostic_operations if transport.id == "dark-controller" else set())
    scaffold = (
        Path(__file__).resolve().parents[1]
        / "anvil_serving/_scaffold_templates/operator-topology.toml"
    )
    assert _REFERENCE_TOPOLOGY.read_bytes() == scaffold.read_bytes()


def test_same_host_bootstrap_still_selects_authenticated_controller():
    topology = _bootstrap_topology()

    plan = resolve_execution_plan(
        topology,
        _bootstrap_spec(),
        target="host:dark",
        command_host="host:dark",
        command_runtime="runtime:dark-native",
    )

    assert plan.transport == "controller"
    assert plan.transport_id == "dark-bootstrap-controller"


@pytest.mark.parametrize(
    "changes",
    (
        {"expected_node": None},
        {"auth_env": None},
        {"allow_unauthenticated_loopback": True},
    ),
)
def test_host_bootstrap_controller_requires_authenticated_expected_identity(changes):
    topology = _bootstrap_topology()
    controller = replace(topology.transport("dark-bootstrap-controller"), **changes)
    topology = replace(
        topology,
        transports=tuple(
            controller if item.id == controller.id else item for item in topology.transports
        ),
    )

    with pytest.raises(TargetResolutionError) as excinfo:
        resolve_execution_plan(topology, _bootstrap_spec(), target="host:dark")

    assert excinfo.value.metadata["reason_code"] == "bootstrap-controller-identity-invalid"


def test_host_bootstrap_recovery_is_optional_but_must_not_be_ambiguous():
    plan = resolve_execution_plan(
        _bootstrap_topology(recovery=False), _bootstrap_spec(), target="host:dark"
    )
    assert plan.recovery_transport_id is None

    topology = _bootstrap_topology()
    recovery = topology.transport("dark-bootstrap-recovery")
    topology = replace(
        topology,
        transports=topology.transports + (replace(recovery, id="private-recovery-copy"),),
    )
    with pytest.raises(TargetResolutionError) as excinfo:
        resolve_execution_plan(topology, _bootstrap_spec(), target="host:dark")
    assert excinfo.value.metadata["reason_code"] == "bootstrap-recovery-ambiguous"
    assert "private" not in str(excinfo.value)


@pytest.mark.parametrize("transport", ("local", "ssh", "bogus-private-transport"))
def test_host_bootstrap_rejects_local_or_caller_selected_recovery(transport):
    with pytest.raises(TargetResolutionError) as excinfo:
        resolve_execution_plan(
            _bootstrap_topology(),
            _bootstrap_spec(),
            target="host:dark",
            transport=transport,
        )

    assert excinfo.value.metadata["reason_code"] == "bootstrap-transport-invalid"
    assert "private" not in str(excinfo.value)


def test_host_bootstrap_recovery_requires_pinned_identity_metadata():
    topology = _bootstrap_topology()
    recovery = replace(
        topology.transport("dark-bootstrap-recovery"),
        host_key_fingerprint=None,
        known_hosts_path=None,
    )
    topology = replace(
        topology,
        transports=tuple(
            recovery if item.id == recovery.id else item for item in topology.transports
        ),
    )

    with pytest.raises(TargetResolutionError) as excinfo:
        resolve_execution_plan(topology, _bootstrap_spec(), target="host:dark")

    assert excinfo.value.metadata["reason_code"] == "bootstrap-transport-invalid"
