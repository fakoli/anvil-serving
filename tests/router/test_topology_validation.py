"""Immutable router-config topology validation contract."""
from __future__ import annotations

import builtins
import hashlib
import os
import socket
import subprocess
from dataclasses import FrozenInstanceError, replace

import pytest

from anvil_serving.router.config import MAX_ROUTER_CONFIG_BYTES, load_bytes
from anvil_serving.router.topology_validation import (
    ERROR_CONFIG_TOO_LARGE,
    ERROR_REPLICA_ENDPOINT_MISMATCH,
    ERROR_REPLICA_ENDPOINT_MISSING,
    ERROR_REPLICA_HOST_MISMATCH,
    ERROR_REPLICA_HOST_SPLIT,
    ERROR_REPLICA_RESOURCE_MISSING,
    ERROR_REPLICA_RESOURCE_REUSED,
    ERROR_ROUTER_CONFIG_INVALID,
    ERROR_TOPOLOGY_INVALID,
    ReplicaTopologyValidationError,
    load_validated_router_snapshot,
    validate_replica_topology,
)
from anvil_serving.topology import parse_topology


_CONFIG_TEXT = """\
[server]
auth_env = "ANVIL_ROUTER_TOKEN"
media_principal = "synthetic-operator"
media_scopes = ["media:read"]
media_public_origin = "http://127.0.0.1:8000"

[router]
exhaustion_status = 429
relay_timeout = 9.5
availability_probe_interval = 2.5
availability_probe_timeout = 0.75
availability_probe_max_bytes = 8192

[[router.tiers]]
id = "primary"
model = "primary-model"
dialect = "openai"
context_limit = 8192
privacy = "local"
tool_support = true
auth_env = "ANVIL_PRIMARY_KEY"
extra_body = { temperature = 0 }
extra_body_defaults = { reasoning_effort = "low" }
engine = "synthetic-engine"
quantization = "synthetic-quant"
params = { tensor_parallel = 2 }
timeout = 8.5
max_concurrency = 4
max_output_tokens = 2048
health_path = "/health"
model_identity = true
replicas = [
  { id = "member-a", base_url = "http://REPLICA-A.EXAMPLE:80/v1/", host_id = "host-a", resource_id = "resource-a", qualification_ref = "qualification:primary-a" },
  { id = "member-b", base_url = "http://replica-b.example:30001/v1", host_id = "host-a", resource_id = "resource-b", qualification_ref = "qualification:primary-b" },
]
replica_identity = { model_revision = "revision-1", engine_version = "engine-1.0", image_digest = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", config_fingerprint = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" }

[router.model_routes]
llm.primary = "primary"
"""


def _topology_data() -> dict:
    return {
        "schema_version": 1,
        "id": "synthetic",
        "capacity_policies": [
            {"id": "model-capable", "allow_model_workloads": True}
        ],
        "hosts": [
            {
                "id": "host-a",
                "roles": ["serve"],
                "capacity_policy": "model-capable",
            }
        ],
        "runtimes": [{"id": "runtime-a", "host": "host-a", "role": "native"}],
        "resources": [
            {
                "id": "resource-a",
                "role": "model-serve-a",
                "host": "host-a",
                "runtime": "runtime-a",
                "endpoint": "http://replica-a.example/v1",
            },
            {
                "id": "resource-b",
                "role": "model-serve-b",
                "host": "host-a",
                "runtime": "runtime-a",
                "endpoint": "http://replica-b.example:30001/v1/",
            },
        ],
    }


def _topology_toml() -> str:
    return """\
schema_version = 1
id = "synthetic"

[[capacity_policies]]
id = "model-capable"
allow_model_workloads = true

[[hosts]]
id = "host-a"
roles = ["serve"]
capacity_policy = "model-capable"

[[runtimes]]
id = "runtime-a"
host = "host-a"
role = "native"

[[resources]]
id = "resource-a"
role = "model-serve-a"
host = "host-a"
runtime = "runtime-a"
endpoint = "http://replica-a.example/v1"

[[resources]]
id = "resource-b"
role = "model-serve-b"
host = "host-a"
runtime = "runtime-a"
endpoint = "http://replica-b.example:30001/v1/"
"""


def _config():
    return load_bytes(_CONFIG_TEXT.encode("utf-8"))


def _topology():
    return parse_topology(_topology_data())


_DIRECT_CONFIG_TEXT = """\
[router]
[[router.tiers]]
id = "direct"
base_url = "http://127.0.0.1:30000/v1"
model = "direct-model"
dialect = "openai"
context_limit = 4096
privacy = "local"
tool_support = true
auth_env = "ANVIL_DIRECT_KEY"
[router.model_routes]
llm.primary = "direct"
"""


def _replace_members(config, members):
    tier = replace(config.tiers[0], replicas=tuple(members))
    return replace(config, tiers=(tier,))


def test_snapshot_preserves_exact_full_config_bytes_and_allowlisted_summary(tmp_path):
    raw = ("# synthetic-private-marker\n" + _CONFIG_TEXT).replace(
        "\n", "\r\n"
    ).encode("utf-8")
    config_path = tmp_path / "router.toml"
    topology_path = tmp_path / "topology.toml"
    config_path.write_bytes(raw)
    topology_path.write_text(_topology_toml(), encoding="utf-8")

    snapshot = load_validated_router_snapshot(config_path, topology_path)

    assert snapshot.config_bytes == raw
    assert snapshot.config_sha256 == hashlib.sha256(raw).hexdigest()
    assert snapshot.tier_count == 1
    assert snapshot.replica_tier_count == 1
    assert snapshot.replica_member_count == 2
    tier = snapshot.config.tier("primary")
    assert tier.auth_env == "ANVIL_PRIMARY_KEY"
    assert tier.timeout == 8.5
    assert tier.extra_body == {"temperature": 0}
    assert snapshot.to_dict() == {
        "schema_version": "replica-topology-validation/v1",
        "valid": True,
        "error_code": None,
        "config_sha256": hashlib.sha256(raw).hexdigest(),
        "tier_count": 1,
        "replica_tier_count": 1,
        "replica_member_count": 2,
        "deployment_identity_source": "declared",
        "runtime_deployment_identity_verified": False,
    }
    assert raw.decode("utf-8") not in repr(snapshot)
    assert "ANVIL_PRIMARY_KEY" not in repr(snapshot)
    assert "replica-a.example" not in repr(snapshot)
    with pytest.raises(FrozenInstanceError):
        snapshot.config_sha256 = "0" * 64


def test_direct_snapshot_is_valid_without_replica_resource_joins(tmp_path):
    config_path = tmp_path / "router.toml"
    topology_path = tmp_path / "topology.toml"
    config_path.write_text(_DIRECT_CONFIG_TEXT, encoding="utf-8")
    topology_path.write_text(_topology_toml(), encoding="utf-8")

    snapshot = load_validated_router_snapshot(config_path, topology_path)

    assert snapshot.tier_count == 1
    assert snapshot.replica_tier_count == 0
    assert snapshot.replica_member_count == 0


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda data: data["resources"].pop(), ERROR_REPLICA_RESOURCE_MISSING),
        (
            lambda data: data["resources"][0].update(
                endpoint="http://other.example/v1"
            ),
            ERROR_REPLICA_ENDPOINT_MISMATCH,
        ),
    ],
)
def test_resource_join_failures_are_fixed_and_input_free(mutate, code):
    data = _topology_data()
    mutate(data)

    with pytest.raises(ReplicaTopologyValidationError) as excinfo:
        validate_replica_topology(_config(), parse_topology(data))

    assert excinfo.value.code == code
    assert str(excinfo.value) == code
    assert excinfo.value.__cause__ is None
    assert "other.example" not in str(excinfo.value)


def test_missing_resource_endpoint_is_refused_defensively():
    topology = _topology()
    resources = (replace(topology.resources[0], endpoint=None), *topology.resources[1:])

    with pytest.raises(ReplicaTopologyValidationError) as excinfo:
        validate_replica_topology(_config(), replace(topology, resources=resources))

    assert excinfo.value.code == ERROR_REPLICA_ENDPOINT_MISSING


def test_host_mismatch_is_rejected():
    data = _topology_data()
    data["hosts"].append(
        {
            "id": "host-b",
            "roles": ["serve"],
            "capacity_policy": "model-capable",
        }
    )
    data["runtimes"].append(
        {"id": "runtime-b", "host": "host-b", "role": "native"}
    )
    data["resources"][0].update(host="host-b", runtime="runtime-b")

    with pytest.raises(ReplicaTopologyValidationError) as excinfo:
        validate_replica_topology(_config(), parse_topology(data))

    assert excinfo.value.code == ERROR_REPLICA_HOST_MISMATCH


@pytest.mark.parametrize(
    ("member_transform", "code"),
    [
        (
            lambda members: (members[0], replace(members[1], host_id="host-b")),
            ERROR_REPLICA_HOST_SPLIT,
        ),
        (
            lambda members: (
                members[0],
                replace(members[1], resource_id=members[0].resource_id),
            ),
            ERROR_REPLICA_RESOURCE_REUSED,
        ),
    ],
)
def test_defensive_join_guards_do_not_rely_on_config_parser(member_transform, code):
    config = _config()
    hostile = _replace_members(config, member_transform(config.tiers[0].replicas))

    with pytest.raises(ReplicaTopologyValidationError) as excinfo:
        validate_replica_topology(hostile, _topology())

    assert excinfo.value.code == code


def test_valid_normalized_endpoints_require_no_dns_or_process_calls(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("external call")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)

    summary = validate_replica_topology(_config(), _topology())

    assert summary.to_dict() == {
        "tier_count": 1,
        "replica_tier_count": 1,
        "replica_member_count": 2,
        "deployment_identity_source": "declared",
        "runtime_deployment_identity_verified": False,
    }


def test_resource_cannot_be_owned_by_two_replica_tiers():
    config = _config()
    duplicate = replace(config.tiers[0], id="secondary")
    config = replace(config, tiers=(*config.tiers, duplicate))

    with pytest.raises(ReplicaTopologyValidationError) as excinfo:
        validate_replica_topology(config, _topology())

    assert excinfo.value.code == ERROR_REPLICA_RESOURCE_REUSED


@pytest.mark.parametrize(
    "raw",
    [b"value = " + b"[" * 2000 + b"]" * 2000, b"value = " + b"9" * 5000],
)
def test_parser_resource_failures_have_only_fixed_error(tmp_path, monkeypatch, raw):
    from anvil_serving.router import topology_validation as module

    config_path = tmp_path / "private-marker.toml"
    config_path.write_bytes(raw)
    monkeypatch.setattr(
        module, "load_topology",
        lambda *_args: pytest.fail("invalid router input reached topology load"),
    )

    with pytest.raises(ReplicaTopologyValidationError) as excinfo:
        load_validated_router_snapshot(config_path, tmp_path / "unused.toml")

    assert excinfo.value.code == ERROR_ROUTER_CONFIG_INVALID
    assert str(excinfo.value) == ERROR_ROUTER_CONFIG_INVALID
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


def test_snapshot_reads_config_once_and_path_change_cannot_change_bytes(
    tmp_path, monkeypatch
):
    from anvil_serving.router import topology_validation as module

    original = _CONFIG_TEXT.encode("utf-8")
    changed = original.replace(b"primary-model", b"changed-model")
    config_path = tmp_path / "router.toml"
    topology_path = tmp_path / "topology.toml"
    config_path.write_bytes(original)
    topology_path.write_text(_topology_toml(), encoding="utf-8")
    real_open = builtins.open
    config_reads = 0

    def tracking_open(path, *args, **kwargs):
        nonlocal config_reads
        if os.path.abspath(os.fspath(path)) == os.path.abspath(config_path):
            config_reads += 1
        return real_open(path, *args, **kwargs)

    real_load_topology = module.load_topology

    def change_after_capture(path, overlay=None):
        config_path.write_bytes(changed)
        return real_load_topology(path, overlay)

    monkeypatch.setattr(builtins, "open", tracking_open)
    monkeypatch.setattr(module, "load_topology", change_after_capture)

    snapshot = load_validated_router_snapshot(config_path, topology_path)

    assert config_reads == 1
    assert snapshot.config_bytes == original
    assert snapshot.config.tier("primary").model == "primary-model"
    assert config_path.read_bytes() == changed


def test_snapshot_maps_size_config_topology_and_private_path_failures(tmp_path):
    topology_path = tmp_path / "topology.toml"
    topology_path.write_text(_topology_toml(), encoding="utf-8")
    cases = []

    oversized = tmp_path / "oversized.toml"
    oversized.write_bytes(b"x" * (MAX_ROUTER_CONFIG_BYTES + 1))
    cases.append((oversized, topology_path, ERROR_CONFIG_TOO_LARGE))

    invalid = tmp_path / "synthetic-private-marker.toml"
    invalid.write_bytes(b"\xffsynthetic-private-marker")
    cases.append((invalid, topology_path, ERROR_ROUTER_CONFIG_INVALID))

    missing = tmp_path / "private-missing-marker.toml"
    cases.append((missing, topology_path, ERROR_ROUTER_CONFIG_INVALID))

    valid = tmp_path / "router.toml"
    valid.write_text(_CONFIG_TEXT, encoding="utf-8")
    bad_topology = tmp_path / "private-topology-marker.toml"
    bad_topology.write_text("not = [valid", encoding="utf-8")
    cases.append((valid, bad_topology, ERROR_TOPOLOGY_INVALID))

    for config_path, selected_topology, code in cases:
        with pytest.raises(ReplicaTopologyValidationError) as excinfo:
            load_validated_router_snapshot(config_path, selected_topology)
        assert excinfo.value.code == code
        assert str(excinfo.value) == code
        assert excinfo.value.__cause__ is None
        assert excinfo.value.__context__ is None
        assert "private" not in str(excinfo.value)
