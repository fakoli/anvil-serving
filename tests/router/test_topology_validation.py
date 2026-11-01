"""Immutable router-config topology validation contract."""
from __future__ import annotations

from dataclasses import replace

import pytest

from anvil_serving.router.config import MAX_ROUTER_CONFIG_BYTES, load_bytes
from anvil_serving.router.topology_validation import (
    ERROR_CONFIG_TOO_LARGE,
    ERROR_ROUTER_CONFIG_INVALID,
    ERROR_TOPOLOGY_INVALID,
    ReplicaTopologyValidationError,
    load_validated_router_snapshot,
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
