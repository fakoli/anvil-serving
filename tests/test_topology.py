from __future__ import annotations

import hashlib
import json
import urllib.parse
from dataclasses import asdict
from pathlib import Path

import pytest

from anvil_serving import cli, topology_cli
import anvil_serving.topology as topology_module
from anvil_serving.targets import resolve_resource_target
from anvil_serving.topology import (
    SCHEMA_VERSION,
    parse_topology,
    topology_snapshot_identity,
    validate_topology,
)


def _topology() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "id": "synthetic",
        "command_host": "host:operator",
        "command_runtime": "runtime:operator-native",
        "capacity_policies": [
            {"id": "model-free", "allow_model_workloads": False},
            {
                "id": "model-capable",
                "allow_model_workloads": True,
                "allow_experimental_model_workloads": True,
            },
        ],
        "hosts": [
            {"id": "operator", "roles": ["operator"], "address": "127.0.0.1"},
            {
                "id": "serve-host",
                "roles": ["router", "serve"],
                "address": "100.64.0.10",
                "capacity_policy": "model-capable",
            },
            {
                "id": "gateway-host",
                "roles": ["gateway", "proxy"],
                "address": "100.64.0.11",
                "capacity_policy": "model-free",
            },
        ],
        "runtimes": [
            {"id": "operator-native", "host": "operator", "role": "native"},
            {"id": "serve-docker", "host": "serve-host", "role": "docker"},
            {"id": "gateway-native", "host": "gateway-host", "role": "native"},
        ],
        "gpu_roles": [
            {
                "id": "fast",
                "host": "serve-host",
                "runtime": "serve-docker",
                "uuid": "GPU-01234567-89ab-cdef-0123-456789abcdef",
            },
        ],
        "resources": [
            {
                "id": "router",
                "role": "router",
                "host": "serve-host",
                "runtime": "serve-docker",
                "endpoint": "http://100.64.0.10:8000/v1",
                "endpoint_kind": "data-plane",
            },
            {
                "id": "model-serve",
                "role": "model-serve",
                "host": "serve-host",
                "runtime": "serve-docker",
                "gpu_role": "fast",
            },
            {
                "id": "realtime-proxy",
                "role": "realtime-proxy",
                "host": "gateway-host",
                "runtime": "gateway-native",
                "endpoint": "http://127.0.0.1:8765/v1",
                "endpoint_kind": "host-relative-loopback",
                "workload": "service",
            },
        ],
        "transports": [
            {
                "id": "serve-controller",
                "kind": "controller",
                "host": "serve-host",
                "runtime": "serve-docker",
                "endpoint": "http://100.64.0.10:8766",
                "auth_env": "ANVIL_CONTROLLER_TOKEN",
                "allowed_operations": ["router-status", "serves-status"],
            },
            {
                "id": "serve-recovery",
                "kind": "ssh",
                "host": "serve-host",
                "runtime": "serve-docker",
                "endpoint": "ssh://100.64.0.10:22",
                "allowed_operations": ["router-status"],
                "host_key_fingerprint": "SHA256:synthetic",
                "known_hosts_path": "~/.ssh/known_hosts",
            },
        ],
    }


def _host_bootstrap(platform: str = "linux") -> dict[str, object]:
    if platform == "windows":
        paths = {
            "staging_root": "C:\\Anvil\\Staging",
            "install_root": "D:\\Anvil\\Install",
            "python_executable": "C:\\Python311\\python.exe",
            "receiver_path": "D:\\Anvil\\Receiver\\bootstrap.py",
        }
        supervisor = "windows-scheduled-task"
    else:
        paths = {
            "staging_root": "/var/tmp/anvil-staging",
            "install_root": "/opt/anvil/install",
            "python_executable": "/usr/bin/python3",
            "receiver_path": "/opt/anvil-receiver/bootstrap.py",
        }
        supervisor = "linux-systemd-user"
    return {
        "enabled": True,
        "bootstrap_authorized": True,
        "execution_runtime": "operator-native",
        **paths,
        "receiver_sha256": "a" * 64,
        "install_adapter": "python-wheel-venv",
        "supervisor_adapter": supervisor,
        "supervisor_id": "anvil-controller",
    }


def _topology_with_host_bootstrap(platform: str = "linux") -> dict:
    data = _topology()
    data["hosts"][0]["os"] = platform
    data["hosts"][0]["bootstrap"] = _host_bootstrap(platform)
    return data


def _paths(result) -> set[str]:
    return {error.path for error in result.errors}


def test_private_overlay_can_supply_media_gpu_and_concrete_transport(tmp_path):
    source = Path(__file__).parent.parent / "examples" / "primary-node" / "operator-topology.toml"
    overlay = tmp_path / "media-deployment.toml"
    overlay.write_text(
        '[[hosts]]\nid = "media-node"\naddress = "100.64.0.30"\n'
        '[[gpu_roles]]\nid = "media-compute"\nhost = "media-node"\n'
        'runtime = "media-docker"\nuuid = "GPU-11111111-2222-3333-4444-555555555555"\n'
        'vram_mib = 32768\nreserve_mib = 2048\n'
        '[[resources]]\nid = "anvil-media-worker"\ngpu_role = "media-compute"\n'
        '[[transports]]\nid = "media-controller"\nendpoint = "http://100.64.0.30:8766"\n',
        encoding="utf-8",
    )
    topology = topology_module.load_topology(str(source), str(overlay))
    target = resolve_resource_target(topology, "media-worker")
    assert target.resource.gpu_role == "media-compute"
    assert target.transport.endpoint == "http://100.64.0.30:8766"


@pytest.mark.parametrize(
    ("key", "value", "path"),
    [
        ("token", "not-allowed", "transports[0].token"),
        ("api_key", "sk-abcdefghijklmnopqrstuvwxyz", "resources[0].api_key"),
        ("auth_env", "sk-abcdefghijklmnopqrstuvwxyz", "transports[0].auth_env"),
    ],
)
def test_credentials_are_rejected_even_when_hidden_in_otherwise_valid_data(key, value, path):
    data = _topology()
    data["transports"][0 if key != "api_key" else 0][key] = value
    if key == "api_key":
        data["resources"][0][key] = value
        del data["transports"][0][key]

    assert path in _paths(validate_topology(data))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("aws_secret_access_key", "not-allowed"),
        ("vendor-api-key", "not-allowed"),
        ("unrelated_value", "ASIA1234567890ABCDEF"),
    ],
)
def test_credential_like_unknown_fields_and_values_are_rejected(key, value):
    data = _topology()
    data["resources"][0][key] = value

    errors = [error for error in validate_topology(data).errors if error.path == f"resources[0].{key}"]
    assert errors
    assert any(error.code == "credential" for error in errors)


def test_snapshot_rejects_unsupported_scalars_without_rendering_them():
    class HostileScalar:
        def __str__(self) -> str:
            return "untrusted scalar text"

    data = _topology()
    data["metadata"] = HostileScalar()

    result = validate_topology(data)

    errors = [error for error in result.errors if error.path == "metadata"]
    assert any(error.code == "type" for error in errors)
    assert all("untrusted scalar text" not in error.message for error in errors)


def test_embedded_url_credentials_and_noncanonical_loopback_do_not_bypass_validation():
    data = _topology()
    data["transports"][0]["endpoint"] = "http://user:pass@127.0.0.1:8766?token=hidden"
    result = validate_topology(data)

    errors = [error for error in result.errors if error.path == "transports[0].endpoint"]
    assert errors and errors[0].code == "credential"


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://100.64.0.10:8000/v1;token=hidden",
        "http://100.64.0.10:8000/v1;%74oken=hidden/models",
        "http://100.64.0.10:8000/v1;serviceToken=hidden",
    ],
)
def test_endpoint_semicolon_params_reject_credential_parameter_names(endpoint):
    data = _topology()
    data["resources"][0]["endpoint"] = endpoint

    errors = [error for error in validate_topology(data).errors if error.path == "resources[0].endpoint"]
    assert any(error.code == "credential" for error in errors)


@pytest.mark.parametrize(
    "parameter_name",
    [
        "\uff41\uff50\uff49\uff4b\uff45\uff59",
        "\U0001D5EE\U0001D5FD\U0001D5F6\U0001D5F8\U0001D5F2\U0001D606",
    ],
    ids=["fullwidth", "mathematical-sans-serif-bold"],
)
@pytest.mark.parametrize("percent_encoded", [False, True], ids=["raw", "percent-encoded"])
def test_endpoint_semicolon_params_reject_compatibility_form_credential_names(parameter_name, percent_encoded):
    name = urllib.parse.quote(parameter_name, safe="") if percent_encoded else parameter_name
    data = _topology()
    data["resources"][0]["endpoint"] = f"http://100.64.0.10:8000/v1;{name}=hidden"

    errors = [error for error in validate_topology(data).errors if error.path == "resources[0].endpoint"]
    assert any(error.code == "credential" for error in errors)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://100.64.0.10:8000/v1;to\u200bken=hidden",
        "http://100.64.0.10:8000/v1;to%E2%80%8Bken=hidden",
        "http://100.64.0.10:8000/v1;to%25E2%2580%258Bken=hidden",
        "http://100.64.0.10:8000/v1;serviceTo\u200bken=hidden",
    ],
)
def test_endpoint_semicolon_params_reject_invisible_character_credential_names(endpoint):
    data = _topology()
    data["resources"][0]["endpoint"] = endpoint

    errors = [error for error in validate_topology(data).errors if error.path == "resources[0].endpoint"]
    assert any(error.code == "credential" for error in errors)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://100.64.0.10:8000/v1;%2574%256f%256b%2565%256e=ordinary",
        "http://100.64.0.10:8000/v1;%252574%25256f%25256b%252565%25256e=ordinary",
        "http://100.64.0.10:8000/v1%253Btoken=hidden",
    ],
)
def test_repeated_percent_encoding_cannot_hide_endpoint_semicolon_credential_parameters(endpoint):
    data = _topology()
    data["resources"][0]["endpoint"] = endpoint

    errors = [error for error in validate_topology(data).errors if error.path == "resources[0].endpoint"]
    assert any(error.code == "credential" for error in errors)


@pytest.mark.parametrize(
    "encoded_value",
    [
        "note=ordinary%3Btoken=hidden",
        "note=ordinary%253Btoken=hidden",
        "note=ordinary%25253Btoken=hidden",
    ],
)
def test_decoded_semicolons_inside_parameter_values_cannot_hide_credentials(encoded_value):
    data = _topology()
    data["resources"][0]["endpoint"] = f"http://100.64.0.10:8000/v1;{encoded_value}"

    errors = [error for error in validate_topology(data).errors if error.path == "resources[0].endpoint"]

    assert any(error.code == "credential" for error in errors)


def test_cgj_and_variation_selectors_cannot_hide_compound_credential_keys():
    key = "access\u034fKey\ufe0fId"
    data = _topology()
    data["resources"][0][key] = "ordinary-value"

    errors = validate_topology(data).errors

    assert any(error.code == "credential" for error in errors)


def test_model_serve_is_rejected_on_a_model_free_host_even_when_gpu_affinity_matches():
    data = _topology()
    data["gpu_roles"][0].update(host="gateway-host", runtime="gateway-native")
    data["resources"][1].update(host="gateway-host", runtime="gateway-native")

    errors = validate_topology(data).errors
    capacity_errors = [error for error in errors if error.path == "resources[1].host"]

    assert len(capacity_errors) == 1
    assert capacity_errors[0].code == "capacity_policy"
    assert "model-free" in capacity_errors[0].message
    assert "resources[1].gpu_role" not in _paths(validate_topology(data))


def test_unpolicied_hosts_reject_model_workloads_and_gpu_roles():
    data = _topology()
    data["hosts"][1].pop("capacity_policy")

    errors = validate_topology(data).errors
    assert any(error.path == "resources[1].host" and error.code == "capacity_policy" for error in errors)
    assert any(error.path == "gpu_roles[0].host" and error.code == "capacity_policy" for error in errors)


@pytest.mark.parametrize(
    "literal",
    [
        "AKIAIOSFODNN7EXAMPLE",
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "xox" + "b-123456789012-abcdefghijklmnop",
        "ghp_1234567890abcdefghijklmnop",
        "ghu_1234567890abcdefghijklmnop",
    ],
)
def test_common_literal_credential_formats_are_rejected(literal):
    data = _topology()
    data["resources"][0]["unrelated_value"] = literal

    errors = [error for error in validate_topology(data).errors if error.path == "resources[0].unrelated_value"]
    assert any(error.code == "credential" for error in errors)


@pytest.mark.parametrize("key", ["awsAccessKeyId", "connectionSecretValue", "serviceToken"])
def test_credential_like_camel_case_unknown_fields_are_rejected(key):
    data = _topology()
    data["resources"][0][key] = "ordinary-value"

    errors = [error for error in validate_topology(data).errors if error.path == f"resources[0].{key}"]
    assert any(error.code == "credential" for error in errors)


@pytest.mark.parametrize(
    ("transport_index", "endpoint"),
    [
        (0, "http://operator@100.64.0.10:8766"),
        (1, "ssh://operator:password@100.64.0.10:22"),
    ],
)
def test_credential_bearing_url_authority_is_rejected(transport_index, endpoint):
    data = _topology()
    data["transports"][transport_index]["endpoint"] = endpoint

    errors = [
        error
        for error in validate_topology(data).errors
        if error.path == f"transports[{transport_index}].endpoint"
    ]
    assert any(error.code == "credential" for error in errors)


@pytest.mark.parametrize(
    "userinfo",
    ["operator%3Apassword", "operator%00", "operator%5Cname", "operator%40name", "operator%C2%80"],
)
def test_percent_encoded_ssh_userinfo_cannot_bypass_credential_validation(userinfo):
    data = _topology()
    data["transports"][1]["endpoint"] = f"ssh://{userinfo}@100.64.0.10:22/opt/anvil"

    errors = [error for error in validate_topology(data).errors if error.path == "transports[1].endpoint"]
    assert any(error.code == "credential" for error in errors)


@pytest.mark.parametrize(
    "userinfo",
    [
        "operator;token=hidden",
        "operator%3Btoken=hidden",
        "operator%253Btoken=hidden",
        "operator%2500build",
        "operator%25E2%2580%258Bbuild",
    ],
)
def test_ssh_userinfo_rejects_semicolon_credential_carriers_and_repeated_invisible_characters(userinfo):
    data = _topology()
    data["transports"][1]["endpoint"] = f"ssh://{userinfo}@100.64.0.10:22/opt/anvil"

    errors = [error for error in validate_topology(data).errors if error.path == "transports[1].endpoint"]
    assert any(error.code == "credential" for error in errors)


@pytest.mark.parametrize(
    "identity",
    [
        "llmv2apiworkerendpoint",
    ],
)
def test_exact_compact_model_suffix_chains_cannot_fall_back_to_service(identity):
    data = _topology()
    resource = data["resources"][1]
    resource.update(
        id="candidate",
        role=identity,
        workload="service",
        host="gateway-host",
        runtime="gateway-native",
    )
    resource.pop("gpu_role")

    errors = validate_topology(data).errors
    assert any(error.path == "resources[1].workload" and error.code == "conflict" for error in errors)
    assert any(error.path == "resources[1].host" and error.code == "capacity_policy" for error in errors)


def test_controller_without_auth_env_is_rejected():
    data = _topology()
    data["transports"][0].pop("auth_env")

    errors = [error for error in validate_topology(data).errors if error.path == "transports[0].auth_env"]
    assert any(error.code == "required" for error in errors)


def test_remote_transport_without_auth_env_is_rejected():
    data = _topology()
    data["transports"][0]["kind"] = "remote"
    data["transports"][0].pop("auth_env")

    errors = [error for error in validate_topology(data).errors if error.path == "transports[0].auth_env"]
    assert any(error.code == "required" for error in errors)


def test_remote_transport_with_auth_env_is_valid():
    data = _topology()
    data["transports"][0]["kind"] = "remote"

    assert validate_topology(data).ok


def test_explicit_unauthenticated_loopback_controller_mode_is_valid_for_development():
    data = _topology()
    controller = data["transports"][0]
    controller.update(
        host="operator",
        runtime="operator-native",
        endpoint="http://127.0.0.1:8766",
        allow_unauthenticated_loopback=True,
    )
    controller.pop("auth_env")

    assert validate_topology(data).ok


def test_unauthenticated_loopback_mode_cannot_be_used_for_remote_controllers():
    data = _topology()
    data["transports"][0].pop("auth_env")
    data["transports"][0]["allow_unauthenticated_loopback"] = True

    paths = _paths(validate_topology(data))
    assert "transports[0].allow_unauthenticated_loopback" in paths
    assert "transports[0].auth_env" in paths


# --- ADR-0033: expected_node and per-host resource-role scoping -------------


def test_topology_drift_reports_metadata_only_differences(tmp_path):
    import json as _json

    from anvil_serving import topology_cli

    def _write(path, data):
        lines = [
            'schema_version = 1',
            f'id = "{data["id"]}"',
            f'command_host = "{data["command_host"]}"',
            f'command_runtime = "{data["command_runtime"]}"',
        ]
        for host in data["hosts"]:
            lines += [
                "[[hosts]]",
                f'id = "{host}"',
                'roles = ["operator"]',
                'address = "127.0.0.1"',
            ]
        for runtime, host in data["runtimes"]:
            lines += [
                "[[runtimes]]",
                f'id = "{runtime}"',
                f'host = "{host}"',
                'role = "native"',
            ]
        for resource, host, runtime in data["resources"]:
            lines += [
                "[[resources]]",
                f'id = "{resource}"',
                'role = "host"',
                f'host = "{host}"',
                f'runtime = "{runtime}"',
                'workload = "service"',
            ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    canonical = tmp_path / "fleet.toml"
    installed = tmp_path / "installed.toml"
    _write(
        canonical,
        {
            "id": "fleet",
            "command_host": "host:seat",
            "command_runtime": "runtime:seat-native",
            "hosts": ["seat", "extra"],
            "runtimes": [("seat-native", "seat"), ("extra-native", "extra")],
            "resources": [("seat-host", "seat", "seat-native"), ("extra-host", "extra", "extra-native")],
        },
    )
    _write(
        installed,
        {
            "id": "seat-view",
            "command_host": "host:seat",
            "command_runtime": "runtime:seat-native",
            "hosts": ["seat"],
            "runtimes": [("seat-native", "seat")],
            "resources": [("seat-host", "seat", "seat-native")],
        },
    )

    report = topology_cli.run(
        ["drift", "--topology", str(installed), "--canonical", str(canonical)]
    )
    assert report["in_sync"] is False
    kinds = {(d["section"], d["id"], d["kind"]) for d in report["differences"]}
    assert ("hosts", "extra", "missing_in_installed") in kinds
    assert ("resources", "extra-host", "missing_in_installed") in kinds
    # Values never appear in the report — only section/id/field names.
    assert "127.0.0.1" not in _json.dumps(report["differences"])

    # An identical view (id/command identity differences only) is in sync.
    _write(
        installed,
        {
            "id": "seat-view",
            "command_host": "host:seat",
            "command_runtime": "runtime:seat-native",
            "hosts": ["seat", "extra"],
            "runtimes": [("seat-native", "seat"), ("extra-native", "extra")],
            "resources": [("seat-host", "seat", "seat-native"), ("extra-host", "extra", "extra-native")],
        },
    )
    report = topology_cli.run(
        ["drift", "--topology", str(installed), "--canonical", str(canonical)]
    )
    assert report["in_sync"] is True
    assert report["differences"] == []


_VALIDATION_CONFIG = """\
[router]

[[router.tiers]]
id = "primary"
model = "primary-model"
dialect = "openai"
context_limit = 8192
privacy = "local"
tool_support = true
auth_env = "ANVIL_PRIMARY_KEY"
health_path = "/health"
model_identity = true
replicas = [
  { id = "member-a", base_url = "http://replica-a.example/v1", host_id = "host-a", resource_id = "resource-a", qualification_ref = "qualification:primary-a" },
  { id = "member-b", base_url = "http://replica-b.example/v1", host_id = "host-a", resource_id = "resource-b", qualification_ref = "qualification:primary-b" },
]
replica_identity = { model_revision = "revision-1", engine_version = "engine-1", image_digest = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", config_fingerprint = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" }

[router.model_routes]
llm.primary = "primary"
"""


_VALIDATION_DIRECT_CONFIG = """\
[router]

[[router.tiers]]
id = "direct"
base_url = "http://replica-a.example/v1"
model = "direct-model"
dialect = "openai"
context_limit = 8192
privacy = "local"
tool_support = true
auth_env = "ANVIL_DIRECT_KEY"

[router.model_routes]
llm.direct = "direct"
"""


_VALIDATION_TOPOLOGY = """\
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
endpoint = "http://replica-b.example/v1"
"""


def _validation_paths(tmp_path):
    config = tmp_path / "router.toml"
    topology = tmp_path / "topology.toml"
    config.write_text(_VALIDATION_CONFIG, encoding="utf-8")
    topology.write_text(_VALIDATION_TOPOLOGY, encoding="utf-8")
    return config, topology


@pytest.mark.parametrize(
    "arguments",
    (
        ["--config="],
        ["--config", "safe-config", "--topology="],
        ["--config", "safe-config", "--topology-overlay="],
    ),
)
def test_root_router_config_validation_refuses_empty_operands_before_validator(
    monkeypatch, capsys, arguments
):
    calls = []
    monkeypatch.setattr(
        topology_cli,
        "load_validated_router_snapshot",
        lambda *_args: calls.append(True) or pytest.fail("validator called"),
    )

    assert cli.main(["--json", "topology", "validate-router-config", *arguments]) == 2
    rendered = capsys.readouterr()
    payload = json.loads(rendered.out)
    assert rendered.err == ""
    assert calls == []
    assert payload["command"] == "topology validate-router-config"
    assert payload["context"] is None and payload["warnings"] == []
    assert payload["data"] == {
        "schema_version": "replica-topology-validation/v1",
        "valid": False,
        "error_code": "router_config_invalid",
        "config_sha256": None,
        "tier_count": None,
        "replica_tier_count": None,
        "replica_member_count": None,
        "deployment_identity_source": None,
        "runtime_deployment_identity_verified": False,
    }


def test_router_config_validation_human_and_module_paths_are_bounded(tmp_path, capsys):
    config, topology = _validation_paths(tmp_path)
    assert topology_cli.main(["validate-router-config", "--config", str(config), "--topology", str(topology)]) == 0
    assert capsys.readouterr().out == "router config topology valid: tiers=1 replica_tiers=1 replica_members=2\n"

    broken = topology.read_text(encoding="utf-8").replace('id = "resource-b"', 'id = "missing"')
    topology.write_text(broken, encoding="utf-8")
    assert cli.main(["topology", "validate-router-config", "--config", str(config), "--topology", str(topology)]) == 2
    assert capsys.readouterr().err == "router config topology refused: replica_resource_missing\n"


@pytest.mark.parametrize(
    ("platform", "field", "value"),
    (
        ("linux", "staging_root", "/"),
        ("linux", "staging_root", "relative/path"),
        ("linux", "staging_root", "/var//stage"),
        ("linux", "staging_root", "/var/../stage"),
        ("linux", "staging_root", "/var\\stage"),
        ("linux", "receiver_path", "/opt/anvil\x00receiver.py"),
        ("windows", "staging_root", "C:\\"),
        ("windows", "staging_root", "\\root-relative"),
        ("windows", "staging_root", "\\\\server\\share\\stage"),
        ("windows", "staging_root", "C:/Anvil/Stage"),
        ("windows", "staging_root", "C:\\Anvil\\..\\Stage"),
        ("windows", "staging_root", "C:\\Anvil\\NUL.txt"),
        ("windows", "staging_root", "C:\\Anvil\\stage. "),
        ("windows", "receiver_path", "C:\\Anvil\\receiver.py:stream"),
    ),
)
def test_host_bootstrap_rejects_unsafe_paths_without_host_filesystem_access(
    platform, field, value
):
    data = _topology_with_host_bootstrap(platform)
    data["hosts"][0]["bootstrap"][field] = value

    errors = validate_topology(data).errors

    assert any(error.path == f"hosts[0].bootstrap.{field}" for error in errors)


def test_absent_bootstrap_preserves_exact_legacy_snapshot_digest():
    topology = parse_topology(_topology())
    legacy = asdict(topology)
    for host in legacy["hosts"]:
        host.pop("bootstrap")
    expected = hashlib.sha256(
        json.dumps(legacy, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()

    assert topology_snapshot_identity(topology) == expected
