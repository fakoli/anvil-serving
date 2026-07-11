from __future__ import annotations

import copy
import os
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import pytest

import anvil_serving.topology as topology_module
from anvil_serving.topology import (
    Resource,
    SCHEMA_VERSION,
    TopologyError,
    TopologyResolutionError,
    TopologyValidationError,
    load_topology_result,
    parse_topology,
    resolve_command_identity,
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


def _paths(result) -> set[str]:
    return {error.path for error in result.errors}


def test_fakoli_reference_topology_validates_offline_and_assigns_models_to_dark():
    source = Path(__file__).parent.parent / "examples" / "fakoli-dark" / "operator-topology.toml"

    result = load_topology_result(source)

    assert result.ok
    assert result.topology is not None
    topology = result.topology
    mini_resources = [resource for resource in topology.resources if resource.host == "fakoli-mini"]
    dark_resources = [resource for resource in topology.resources if resource.host == "fakoli-dark"]
    assert topology.host("fakoli-mini").capacity_policy == "mini-model-free"
    assert topology.host("fakoli-mini").os == "macos"
    assert topology.host("fakoli-dark").os == "windows"
    assert not any(resource.workload in {"model", "llm", "stt", "tts"} for resource in mini_resources)
    assert not [role for role in topology.gpu_roles if role.host == "fakoli-mini"]
    assert {resource.workload for resource in dark_resources} >= {"llm", "stt", "tts"}
    assert all(resource.host == "fakoli-dark" for resource in topology.resources if resource.workload in {"llm", "stt", "tts"})
    controller = topology.transport("dark-controller")
    recovery = topology.transport("dark-ssh-bootstrap-recovery")
    assert controller.kind == "controller"
    assert controller.auth_env == "ANVIL_CONTROLLER_TOKEN"
    assert recovery.kind == "ssh"
    assert recovery.host == "fakoli-dark"
    assert recovery.runtime == "dark-docker"
    assert recovery.auth_env is None
    assert recovery.allowed_operations == ("controller-bootstrap", "controller-recovery")
    assert recovery.host_key_fingerprint == "SHA256:REPLACE-WITH-VERIFIED-HOST-KEY"
    assert recovery.known_hosts_path == "~/.ssh/known_hosts"
    assert topology.transport("mini-controller").runtime == "mini-native"
    assert topology.transport("dark-host-controller").runtime == "dark-native"
    assert {
        resource.host for resource in topology.resources if resource.role == "host"
    } == {"fakoli-mini", "fakoli-dark"}


def test_valid_topology_parses_into_typed_models_and_preserves_stable_gpu_identity():
    topology = parse_topology(_topology())

    assert topology.id == "synthetic"
    assert topology.host("serve-host").capacity_policy == "model-capable"
    assert topology.resource_owner("realtime-proxy").host == "gateway-host"
    assert topology.gpu_role("fast").uuid == "GPU-01234567-89ab-cdef-0123-456789abcdef"
    assert topology.resource("model-serve").workload == "model"
    assert topology.transport("serve-controller").auth_env == "ANVIL_CONTROLLER_TOKEN"


def test_host_os_is_typed_and_invalid_values_are_rejected():
    data = _topology()
    data["hosts"][0]["os"] = "linux"
    assert parse_topology(data).host("operator").os == "linux"
    data["hosts"][0]["os"] = "wsl"
    result = validate_topology(data)
    assert any(error.path == "hosts[0].os" and error.code == "value" for error in result.errors)


def test_partial_overlay_merges_records_by_id(tmp_path):
    source = Path(__file__).parent.parent / "examples" / "fakoli-dark" / "operator-topology.toml"
    overlay = tmp_path / "deployment.toml"
    overlay.write_text(
        'command_host = "host:fakoli-dark"\n'
        'command_runtime = "runtime:dark-native"\n'
        '[[hosts]]\nid = "fakoli-dark"\naddress = "100.87.34.66"\n',
        encoding="utf-8",
    )
    topology = topology_module.load_topology(str(source), str(overlay))
    assert topology.command_host == "fakoli-dark"
    assert topology.command_runtime == "dark-native"
    assert topology.host("fakoli-dark").address == "100.87.34.66"
    assert topology.host("fakoli-dark").os == "windows"


def test_overlay_depth_is_bounded_before_recursive_merge(tmp_path):
    source = Path(__file__).parent.parent / "examples" / "fakoli-dark" / "operator-topology.toml"
    overlay = tmp_path / "deep-overlay.toml"
    overlay.write_text(
        "[" + ".".join(f"level{i}" for i in range(70)) + "]\nvalue = 1\n",
        encoding="utf-8",
    )
    result = load_topology_result(source, overlay)
    assert result.ok is False
    assert any(error.code == "depth" for error in result.errors)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data["gpu_roles"][0].pop("uuid"),
        lambda data: data["gpu_roles"][0].update(uuid=""),
    ],
)
def test_gpu_roles_require_nonempty_stable_uuids(mutate):
    data = _topology()
    mutate(data)

    errors = [error for error in validate_topology(data).errors if error.path == "gpu_roles[0].uuid"]
    assert any(error.code == "type" for error in errors)


def test_gpu_roles_reject_duplicate_stable_uuids():
    data = _topology()
    data["gpu_roles"].append(
        {
            "id": "heavy",
            "host": "serve-host",
            "runtime": "serve-docker",
            "uuid": "GPU-01234567-89AB-CDEF-0123-456789ABCDEF",
        }
    )

    errors = [error for error in validate_topology(data).errors if error.path == "gpu_roles[1].uuid"]
    assert any(error.code == "duplicate" for error in errors)


def test_gpu_uuid_is_stored_in_one_canonical_representation():
    data = _topology()
    data["gpu_roles"][0]["uuid"] = "GPU-01234567-89AB-CDEF-0123-456789ABCDEF"

    topology = parse_topology(data)

    assert topology.gpu_role("fast").uuid == "GPU-01234567-89ab-cdef-0123-456789abcdef"


@pytest.mark.parametrize(
    "uuid",
    [
        " ",
        " GPU-01234567-89ab-cdef-0123-456789abcdef",
        "GPU-01234567-89ab-cdef-0123-456789abcdef ",
        "GPU synthetic",
        "GPU-synthetic-fast",
        "GPU-01234567-89ab-cdef-0123-456789abcdeg",
        "GPU-\x00fast",
        "GPU-\x7ffast",
        "GPU-01234567-89ab-cdef-0123-456789abc\u200bdef",
    ],
)
def test_gpu_roles_reject_aliases_whitespace_control_and_format_character_uuids(uuid):
    data = _topology()
    data["gpu_roles"][0]["uuid"] = uuid

    errors = [error for error in validate_topology(data).errors if error.path == "gpu_roles[0].uuid"]
    assert any(error.code == "value" for error in errors)


def test_gpu_role_invalid_whitespace_decoration_is_rejected():
    data = _topology()
    data["gpu_roles"].append(
        {
            "id": "heavy",
            "host": "serve-host",
            "runtime": "serve-docker",
            "uuid": " GPU-01234567-89ab-cdef-0123-456789abcdef\t",
        }
    )

    errors = [error for error in validate_topology(data).errors if error.path == "gpu_roles[1].uuid"]
    assert any(error.code == "value" for error in errors)


def test_validation_is_offline_and_returns_path_addressable_errors():
    data = _topology()
    data["resources"][0]["runtime"] = "missing-runtime"
    result = validate_topology(data)

    assert result.topology is None
    assert result.ok is False
    assert result.errors[0].path == "resources[0].runtime"
    assert result.errors[0].code == "reference"


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (lambda data: data.update(schema_version=99), "schema_version"),
        (lambda data: data["hosts"].append(copy.deepcopy(data["hosts"][0])), "hosts[3].id"),
        (lambda data: data["runtimes"][0].update(host="missing"), "runtimes[0].host"),
        (lambda data: data["resources"][0].update(gpu_role="missing"), "resources[0].gpu_role"),
    ],
)
def test_invalid_versions_duplicate_ids_and_references_are_rejected_with_paths(mutate, path):
    data = _topology()
    mutate(data)

    assert path in _paths(validate_topology(data))


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


def test_unknown_schema_fields_keep_original_toml_record_indexes():
    data = _topology()
    data["hosts"].insert(0, "not-a-table")
    data["hosts"][1]["adress"] = "100.64.0.99"
    data["resoruces"] = []

    result = validate_topology(data)
    assert {"hosts[0]", "hosts[1].adress", "resoruces"} <= _paths(result)
    assert any(error.path == "hosts[1].adress" and error.code == "unknown" for error in result.errors)


def test_reference_errors_keep_original_toml_record_indexes():
    data = _topology()
    data["resources"].insert(0, "not-a-table")
    data["resources"][1]["runtime"] = "missing-runtime"

    result = validate_topology(data)
    assert {"resources[0]", "resources[1].runtime"} <= _paths(result)
    assert any(error.path == "resources[1].runtime" and error.code == "reference" for error in result.errors)


def test_public_validator_accepts_generic_mapping_inputs():
    topology = parse_topology(MappingProxyType(_topology()))

    assert topology.id == "synthetic"


def test_mutable_mapping_is_snapshotted_once_before_validation_reads_it():
    class MutatingMapping(Mapping[str, object]):
        def __init__(self, values: dict[str, object]) -> None:
            self.values = values
            self.items_calls = 0

        def __getitem__(self, key: str) -> object:
            return self.values[key]

        def __iter__(self):
            return iter(self.values)

        def __len__(self) -> int:
            return len(self.values)

        def items(self):
            self.items_calls += 1
            if self.items_calls > 1:
                raise AssertionError("topology mapping was read more than once")
            snapshot = tuple(self.values.items())
            self.values["token"] = "must-not-be-scanned-after-snapshot"
            return iter(snapshot)

    data = MutatingMapping(_topology())

    assert validate_topology(data).ok
    assert data.items_calls == 1


def test_mapping_read_failures_return_structured_errors_before_validation():
    class BrokenMapping(Mapping[str, object]):
        def __getitem__(self, key: str) -> object:
            raise AssertionError(f"unexpected lookup for {key}")

        def __iter__(self):
            raise AssertionError("unexpected iteration")

        def __len__(self) -> int:
            return 1

        def items(self):
            raise RuntimeError("mapping became unreadable")

    result = validate_topology(BrokenMapping())

    assert result.topology is None
    assert any(error.path == "$" and error.code == "read" for error in result.errors)


def test_snapshot_normalizes_scalar_subclasses_without_calling_override_hooks():
    class HostileString(str):
        def __str__(self) -> str:
            raise AssertionError("hostile scalar stringification")

    data = _topology()
    data["id"] = HostileString("synthetic")

    result = validate_topology(data)

    assert result.ok
    assert result.topology is not None
    assert type(result.topology.id) is str


def test_snapshot_does_not_look_up_a_hostile_nested_scalar_class():
    class HostileString(str):
        @property
        def __class__(self):
            raise AssertionError("topology snapshot looked up an untrusted scalar class")

    data = _topology()
    data["metadata"] = {"label": HostileString("ordinary")}

    result = validate_topology(data)

    assert any(error.path == "metadata" and error.code == "unknown" for error in result.errors)


def test_snapshot_mapping_failures_do_not_render_untrusted_exception_text():
    class HostileReadError(RuntimeError):
        def __str__(self) -> str:
            return "untrusted mapping exception text"

    class BrokenMapping(Mapping[str, object]):
        def __getitem__(self, key: str) -> object:
            raise KeyError(key)

        def __iter__(self):
            return iter(())

        def __len__(self) -> int:
            return 0

        def items(self):
            raise HostileReadError()

    result = validate_topology(BrokenMapping())

    assert result.topology is None
    assert result.errors == (TopologyError("$", "could not read topology mapping", "read"),)


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


@pytest.mark.parametrize(
    ("setter", "path"),
    [
        (lambda data: data["hosts"][0].update(address="LOCALHOST"), "hosts[0].address"),
        (lambda data: data["hosts"][0].update(address="localhost."), "hosts[0].address"),
        (lambda data: data["resources"][0].update(endpoint="http://localhost:8000/v1"), "resources[0].endpoint"),
        (lambda data: data["resources"][0].update(endpoint="http://LOCALHOST.:8000/v1"), "resources[0].endpoint"),
        (lambda data: data["transports"][0].update(endpoint="http://localhost:8766"), "transports[0].endpoint"),
    ],
)
def test_localhost_rejects_in_every_host_or_url_form(setter, path):
    data = _topology()
    setter(data)

    assert path in _paths(validate_topology(data))


def test_embedded_url_credentials_and_noncanonical_loopback_do_not_bypass_validation():
    data = _topology()
    data["transports"][0]["endpoint"] = "http://user:pass@127.0.0.1:8766?token=hidden"
    result = validate_topology(data)

    errors = [error for error in result.errors if error.path == "transports[0].endpoint"]
    assert errors and errors[0].code == "credential"


@pytest.mark.parametrize(
    ("transport_index", "endpoint"),
    [
        (0, "http://@100.64.0.10:8766"),
        (1, "ssh://@100.64.0.10:22"),
        (1, "ssh://operator:@100.64.0.10:22"),
        (1, "ssh://operator@@100.64.0.10:22"),
        (1, "ssh://operator%zz@100.64.0.10:22"),
    ],
)
def test_empty_url_userinfo_values_are_rejected(transport_index, endpoint):
    data = _topology()
    data["transports"][transport_index]["endpoint"] = endpoint

    errors = [
        error
        for error in validate_topology(data).errors
        if error.path == f"transports[{transport_index}].endpoint"
    ]
    assert any(error.code == "credential" for error in errors)


@pytest.mark.parametrize("suffix", ["?", "#"])
def test_empty_query_and_fragment_delimiters_are_rejected(suffix):
    data = _topology()
    data["resources"][0]["endpoint"] += suffix

    errors = [error for error in validate_topology(data).errors if error.path == "resources[0].endpoint"]
    assert any(error.code == "credential" for error in errors)


@pytest.mark.parametrize(
    "suffix",
    [
        "/%",
        "/%0",
        "/%zz",
        "/%00",
        "/%0a",
        "/%7f",
        "/%80",
        "/%c3%28",
        "/%e2%82",
        "/%c2%80",
        "/%c2%9f",
        "/v1\\models",
        "/v1%5Cmodels",
    ],
)
def test_endpoint_paths_reject_invalid_escapes_utf8_controls_and_backslashes(suffix):
    data = _topology()
    data["resources"][0]["endpoint"] = f"http://100.64.0.10:8000{suffix}"

    errors = [error for error in validate_topology(data).errors if error.path == "resources[0].endpoint"]
    assert any(error.code == "url" for error in errors)


@pytest.mark.parametrize("suffix", ["/v1%255Cmodels", "/v1%2500"])
def test_endpoint_paths_reject_double_encoded_controls_and_backslashes(suffix):
    data = _topology()
    data["resources"][0]["endpoint"] = f"http://100.64.0.10:8000{suffix}"

    errors = [error for error in validate_topology(data).errors if error.path == "resources[0].endpoint"]
    assert any(error.code == "url" for error in errors)


def test_endpoint_paths_allow_benign_percent_text_after_one_decode():
    data = _topology()
    data["resources"][0]["endpoint"] = "http://100.64.0.10:8000/v1/100%25complete"

    assert validate_topology(data).ok


def test_endpoint_paths_allow_benign_percent_text_after_repeated_decoding():
    data = _topology()
    data["resources"][0]["endpoint"] = "http://100.64.0.10:8000/v1/100%252525complete"

    assert validate_topology(data).ok


def test_percent_encoded_query_and_fragment_characters_remain_path_data_for_http_and_ssh():
    data = _topology()
    data["resources"][0]["endpoint"] = "http://100.64.0.10:8000/v1%253Fview=full%2523revision"
    data["transports"][1]["endpoint"] = "ssh://operator-build_1@100.64.0.10:22/opt%3Fview=full%23revision"

    assert validate_topology(data).ok


def test_endpoint_percent_decode_limit_fails_closed():
    data = _topology()
    data["resources"][0]["endpoint"] = "http://100.64.0.10:8000/v1%252525253Ftoken=hidden"

    errors = [error for error in validate_topology(data).errors if error.path == "resources[0].endpoint"]
    assert any(error.code == "url" for error in errors)


def test_ssh_endpoint_paths_apply_the_same_boundary_validation():
    data = _topology()
    data["transports"][1]["endpoint"] = "ssh://operator@100.64.0.10:22/%00"

    errors = [error for error in validate_topology(data).errors if error.path == "transports[1].endpoint"]
    assert any(error.code == "url" for error in errors)


@pytest.mark.parametrize(
    "endpoint",
    [
        "\x00http://100.64.0.10:8000/v1",
        "http\x1f://100.64.0.10:8000/v1",
        "http://100.64.0.10:8000/v1\x7f",
    ],
)
def test_raw_url_controls_are_rejected_before_url_parsing(endpoint):
    data = _topology()
    data["resources"][0]["endpoint"] = endpoint

    errors = [error for error in validate_topology(data).errors if error.path == "resources[0].endpoint"]
    assert any(error.code == "url" for error in errors)


@pytest.mark.parametrize("params", ["%", "%0", "%zz", "%00", "%0a", "%7f", r"\models", "%5Cmodels"])
def test_endpoint_semicolon_params_apply_path_boundary_validation(params):
    data = _topology()
    data["resources"][0]["endpoint"] = f"http://100.64.0.10:8000/v1;{params}"

    errors = [error for error in validate_topology(data).errors if error.path == "resources[0].endpoint"]
    assert any(error.code == "url" for error in errors)


def test_endpoint_semicolon_params_without_boundary_hazards_remain_valid():
    data = _topology()
    data["resources"][0]["endpoint"] = "http://100.64.0.10:8000/v1;version=1"

    assert validate_topology(data).ok


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


@pytest.mark.parametrize("parameter_name", ["vendor_apikey", "vendor_api_key"])
def test_endpoint_semicolon_params_reject_compound_apikey_parameter_names(parameter_name):
    data = _topology()
    data["resources"][0]["endpoint"] = f"http://100.64.0.10:8000/v1;{parameter_name}=hidden"

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


@pytest.mark.parametrize("parameter_name", ["vendor_notapikey", "vendor_api_keyring"])
def test_endpoint_semicolon_params_allow_non_sensitive_apikey_substrings(parameter_name):
    data = _topology()
    data["resources"][0]["endpoint"] = f"http://100.64.0.10:8000/v1;{parameter_name}=ordinary"

    assert validate_topology(data).ok


@pytest.mark.parametrize(
    "path_segment",
    [
        "\uff41\uff50\uff49\uff4b\uff45\uff59",
        "\U0001D5EE\U0001D5FD\U0001D5F6\U0001D5F8\U0001D5F2\U0001D606",
    ],
)
def test_compatibility_forms_in_endpoint_paths_remain_path_data(path_segment):
    data = _topology()
    data["resources"][0]["endpoint"] = f"http://100.64.0.10:8000/v1/{path_segment}"

    assert validate_topology(data).ok


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


@pytest.mark.parametrize(
    "key",
    [
        "access\u034fKey\ufe0fId",
    ],
)
def test_cgj_and_variation_selectors_cannot_hide_compound_credential_keys(key):
    data = _topology()
    data["resources"][0][key] = "ordinary-value"

    errors = validate_topology(data).errors

    assert any(error.code == "credential" for error in errors)


@pytest.mark.parametrize(
    "endpoint",
    ["http://100.64.0.10:", "http://100.64.0.10:0", "http://100.64.0.10:65536", "http://100.64.0.10:nope"],
)
def test_invalid_url_ports_are_rejected_with_offline_structured_errors(endpoint):
    data = _topology()
    data["resources"][0]["endpoint"] = endpoint

    errors = [error for error in validate_topology(data).errors if error.path == "resources[0].endpoint"]
    assert any(error.code == "port" for error in errors)


def test_resource_gpu_role_must_belong_to_the_same_host_and_runtime():
    data = _topology()
    data["resources"][1].update(host="gateway-host", runtime="gateway-native")

    errors = [error for error in validate_topology(data).errors if error.path == "resources[1].gpu_role"]
    assert errors and errors[0].code == "reference"


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


def test_unpolicied_hosts_allow_explicit_service_workloads():
    data = _topology()
    data["hosts"][2].pop("capacity_policy")

    assert validate_topology(data).ok


def test_attached_allow_policy_permits_model_workloads_and_gpu_roles():
    data = _topology()
    data["hosts"][1].pop("capacity_policy")

    assert not validate_topology(data).ok

    data["hosts"][1]["capacity_policy"] = "model-capable"
    assert validate_topology(data).ok


def test_id_only_capacity_policy_rejects_model_workloads_until_explicitly_allowed():
    data = _topology()
    data["capacity_policies"][1] = {"id": "model-capable"}
    data["resources"][1]["workload"] = "model"

    errors = validate_topology(data).errors
    assert any(error.path == "resources[1].host" and error.code == "capacity_policy" for error in errors)
    assert any(error.path == "gpu_roles[0].host" and error.code == "capacity_policy" for error in errors)

    data["capacity_policies"][1]["allow_model_workloads"] = True
    assert parse_topology(data).capacity_policy("model-capable").allow_model_workloads is True


@pytest.mark.parametrize("key", ["device_index", "container_id", "reachability", "health"])
def test_runtime_observations_are_not_stable_topology_identity(key):
    data = _topology()
    data["gpu_roles"][0][key] = 0

    result = validate_topology(data)
    assert f"gpu_roles[0].{key}" in _paths(result)
    assert any(error.code == "runtime_state" for error in result.errors)


def test_resource_role_must_have_one_declared_owner():
    data = _topology()
    duplicate = copy.deepcopy(data["resources"][0])
    duplicate["id"] = "second-router"
    data["resources"].append(duplicate)

    result = validate_topology(data)
    assert "resources[0].role" in _paths(result)
    assert "resources[3].role" in _paths(result)
    assert all(error.code == "ambiguous_owner" for error in result.errors)


def test_resource_role_owner_validation_has_linear_comparison_shape_beyond_container_limit():
    class CountedRole(str):
        comparisons = 0

        def __eq__(self, other: object) -> bool:
            type(self).comparisons += 1
            return super().__eq__(other)

        __hash__ = str.__hash__

    resource_count = 10_001
    resources = [
        (
            index,
            Resource(
                id=f"resource-{index}",
                role=CountedRole(f"role-{index}"),
                host="host",
                runtime="runtime",
            ),
        )
        for index in range(resource_count)
    ]

    import anvil_serving.topology as topology_module

    topology_module._validate_references([], [], resources, [], [], [], None, None, [])

    assert CountedRole.comparisons < resource_count * 8


def test_command_identity_obeys_explicit_environment_and_topology_precedence():
    topology = parse_topology(_topology())

    explicit = resolve_command_identity(
        topology,
        command_host="host:serve-host",
        command_runtime="runtime:serve-docker",
        environment={"ANVIL_COMMAND_HOST": "host:gateway-host", "ANVIL_COMMAND_RUNTIME": "runtime:gateway-native"},
    )
    environment = resolve_command_identity(
        topology,
        environment={"ANVIL_COMMAND_HOST": "host:gateway-host", "ANVIL_COMMAND_RUNTIME": "runtime:gateway-native"},
    )
    default = resolve_command_identity(topology, environment={})

    assert (explicit.host.id, explicit.runtime.id, explicit.host_source) == (
        "serve-host",
        "serve-docker",
        "explicit",
    )
    assert (environment.host.id, environment.runtime.id, environment.runtime_source) == (
        "gateway-host",
        "gateway-native",
        "environment",
    )
    assert (default.host.id, default.runtime.id) == ("operator", "operator-native")


def test_command_identity_requires_complete_matching_identity_but_offline_commands_do_not():
    data = _topology()
    data.pop("command_host")
    data.pop("command_runtime")
    topology = parse_topology(data)

    assert resolve_command_identity(topology, offline=True) is None
    with pytest.raises(TopologyResolutionError, match="command host is required"):
        resolve_command_identity(topology, environment={})
    with pytest.raises(TopologyResolutionError, match="belongs to host"):
        resolve_command_identity(
            parse_topology(_topology()),
            command_host="host:operator",
            command_runtime="runtime:serve-docker",
            environment={},
        )


def test_toml_loading_is_hermetic_and_reports_parse_errors_with_a_location(tmp_path):
    broken = tmp_path / "broken.toml"
    broken.write_text("schema_version = [", encoding="utf-8")

    result = load_topology_result(str(broken))
    assert result.topology is None
    assert result.errors[0].path == "$"
    assert result.errors[0].code == "toml"


def test_toml_loading_returns_a_structured_error_for_invalid_utf8(tmp_path):
    broken = tmp_path / "invalid-utf8.toml"
    broken.write_bytes(b"schema_version = 1\n# \xff\n")

    result = load_topology_result(str(broken))

    assert result.topology is None
    assert result.errors[0].path == "$"
    assert result.errors[0].code == "toml"


def test_toml_loading_caps_file_bytes_before_parse(tmp_path, monkeypatch):
    monkeypatch.setattr(topology_module, "_MAX_TOPOLOGY_FILE_BYTES", 32)
    source = tmp_path / "oversized.toml"
    source.write_bytes(b"x" * 33)

    result = load_topology_result(source)

    assert result.topology is None
    assert result.errors == (
        TopologyError("$", "topology file exceeds the maximum byte size", "resource"),
    )


def test_topology_snapshot_caps_scalar_values_and_mapping_keys(monkeypatch):
    monkeypatch.setattr(topology_module, "_MAX_TOPOLOGY_SCALAR_CHARS", 16)
    value_result = validate_topology({"schema_version": 1, "id": "x" * 17})
    key_result = validate_topology({"x" * 17: "value"})

    assert any(error.code == "resource" and error.path == "id" for error in value_result.errors)
    assert any(error.code == "resource" and error.path == "$" for error in key_result.errors)


def test_topology_snapshot_identity_is_deterministic_and_content_bound():
    first = parse_topology(_topology())
    second = parse_topology(copy.deepcopy(_topology()))
    changed_data = _topology()
    changed_data["transports"][0]["endpoint"] = "http://100.64.0.10:9999"
    changed = parse_topology(changed_data)

    assert topology_snapshot_identity(first) == topology_snapshot_identity(second)
    assert topology_snapshot_identity(first) != topology_snapshot_identity(changed)


def test_toml_loading_accepts_valid_pathlike_values(tmp_path):
    source = tmp_path / "topology.toml"
    source.write_text('schema_version = 1\nid = "pathlike"\n', encoding="utf-8")

    result = load_topology_result(source)

    assert result.ok
    assert result.topology is not None
    assert result.topology.id == "pathlike"


def test_toml_loading_returns_a_structured_read_error_for_malformed_source_paths():
    result = load_topology_result("invalid\x00-topology.toml")

    assert result.topology is None
    assert result.errors[0].path == "$"
    assert result.errors[0].code == "read"


@pytest.mark.parametrize("path", [None, []])
def test_toml_loading_rejects_unsupported_path_types_before_open(path):
    result = load_topology_result(path)

    assert result.topology is None
    assert result.errors[0].path == "$"
    assert result.errors[0].code == "read"


def test_toml_loading_returns_a_structured_read_error_for_failing_pathlike_conversion():
    class ExplodingPath(os.PathLike[str]):
        def __fspath__(self) -> str:
            raise RuntimeError("path conversion failed")

    result = load_topology_result(ExplodingPath())

    assert result.topology is None
    assert result.errors[0].path == "$"
    assert result.errors[0].code == "read"


def test_toml_loading_does_not_consume_file_descriptors(tmp_path):
    source = tmp_path / "topology.toml"
    source.write_text("schema_version = 1\n", encoding="utf-8")
    descriptor = os.open(source, os.O_RDONLY)

    try:
        result = load_topology_result(descriptor)

        assert result.topology is None
        assert result.errors[0].path == "$"
        assert result.errors[0].code == "read"
        assert os.fstat(descriptor).st_size == source.stat().st_size
    finally:
        os.close(descriptor)


def test_strict_parser_keeps_structured_errors_available_to_callers():
    data = _topology()
    data["hosts"][0]["address"] = "localhost"

    with pytest.raises(TopologyValidationError) as raised:
        parse_topology(data)
    assert raised.value.errors[0].path == "hosts[0].address"


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


def test_valid_endpoint_paths_and_ssh_operator_url_remain_valid():
    data = _topology()
    data["resources"][0]["endpoint"] = "http://100.64.0.10:8000/v1/models%2Fcatalog"
    data["transports"][1]["endpoint"] = "ssh://operator@100.64.0.10:22/opt/anvil"

    assert validate_topology(data).ok


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
        "operator%253Apassword",
        "operator%2540name",
        "operator%252Fhome",
        "operator%255Cname",
        "operator%25E2%2580%258Bname",
        "operator%25E2%2580%25AEname",
    ],
)
def test_repeated_percent_encoding_cannot_hide_ssh_userinfo_delimiters(userinfo):
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
    "userinfo",
    [
        "operator%2Fhome",
        "operator%5Cname",
        "operator%E2%80%8Bname",
        "operator%E2%80%AEname",
    ],
)
def test_encoded_ssh_userinfo_path_separators_and_format_characters_are_rejected(userinfo):
    data = _topology()
    data["transports"][1]["endpoint"] = f"ssh://{userinfo}@100.64.0.10:22/opt/anvil"

    errors = [error for error in validate_topology(data).errors if error.path == "transports[1].endpoint"]
    assert any(error.code == "credential" for error in errors)


def test_passwordless_ssh_username_remains_valid():
    data = _topology()
    data["transports"][1]["endpoint"] = "ssh://operator@100.64.0.10:22/opt/anvil"

    assert validate_topology(data).ok


@pytest.mark.parametrize("userinfo", ["operator", "operator-build_1", "operator%2Dbuild"])
def test_safe_passwordless_ssh_usernames_remain_valid(userinfo):
    data = _topology()
    data["transports"][1]["endpoint"] = f"ssh://{userinfo}@100.64.0.10:22/opt/anvil"

    assert validate_topology(data).ok


@pytest.mark.parametrize(
    ("setter", "path"),
    [
        (lambda data: data["hosts"][0].update(address="bad host"), "hosts[0].address"),
        (lambda data: data["hosts"][0].update(address="bad..host"), "hosts[0].address"),
        (lambda data: data["hosts"][0].update(address="https://anvil-gpu.tailnet.example"), "hosts[0].address"),
        (lambda data: data["hosts"][0].update(address="ssh://fakoli-dark"), "hosts[0].address"),
        (lambda data: data["hosts"][0].update(address="127.1"), "hosts[0].address"),
        (lambda data: data["hosts"][0].update(address="127.0.0.2"), "hosts[0].address"),
        (lambda data: data["hosts"][0].update(address="0x7f000001"), "hosts[0].address"),
        (lambda data: data["hosts"][0].update(address="[::1]"), "hosts[0].address"),
        (
            lambda data: data["resources"][0].update(endpoint="http://127.1:8000/v1"),
            "resources[0].endpoint",
        ),
        (
            lambda data: data["resources"][0].update(endpoint="http://[::1]:8000/v1"),
            "resources[0].endpoint",
        ),
    ],
)
def test_malformed_and_noncanonical_host_addresses_are_rejected(setter, path):
    data = _topology()
    setter(data)

    assert path in _paths(validate_topology(data))


@pytest.mark.parametrize("host", ["08.08.08.08", "256.256.256.256", "1.2.3.999"])
def test_noncanonical_all_numeric_dotted_host_candidates_are_rejected_in_hosts_and_urls(host):
    host_data = _topology()
    host_data["hosts"][0]["address"] = host
    host_errors = [error for error in validate_topology(host_data).errors if error.path == "hosts[0].address"]

    url_data = _topology()
    url_data["resources"][0]["endpoint"] = f"http://{host}:8000/v1"
    url_errors = [error for error in validate_topology(url_data).errors if error.path == "resources[0].endpoint"]

    assert any(error.code == "host" for error in host_errors)
    assert any(error.code == "host" for error in url_errors)


def test_canonical_loopback_address_and_url_remain_valid():
    data = _topology()
    data["hosts"][0]["address"] = "127.0.0.1"
    data["resources"][2]["endpoint"] = "http://127.0.0.1:8765/v1"

    assert validate_topology(data).ok


@pytest.mark.parametrize(
    ("setter", "path"),
    [
        (lambda data: data["hosts"][0].update(address="0.0.0.0"), "hosts[0].address"),
        (lambda data: data["hosts"][0].update(address="::"), "hosts[0].address"),
        (lambda data: data["hosts"][0].update(address="::ffff:0.0.0.0"), "hosts[0].address"),
        (
            lambda data: data["resources"][0].update(endpoint="http://0.0.0.0:8000/v1"),
            "resources[0].endpoint",
        ),
        (
            lambda data: data["resources"][0].update(endpoint="http://[::]:8000/v1"),
            "resources[0].endpoint",
        ),
        (
            lambda data: data["resources"][0].update(endpoint="http://[::ffff:0.0.0.0]:8000/v1"),
            "resources[0].endpoint",
        ),
    ],
)
def test_wildcard_host_addresses_are_rejected_as_unstable_target_identities(setter, path):
    data = _topology()
    setter(data)

    errors = [error for error in validate_topology(data).errors if error.path == path]
    assert any(error.code == "host" for error in errors)


@pytest.mark.parametrize(
    ("setter", "path"),
    [
        (lambda data: data["hosts"][0].update(address="fakoli-dark.example."), "hosts[0].address"),
        (lambda data: data["hosts"][0].update(address="127.0.0.1."), "hosts[0].address"),
        (
            lambda data: data["resources"][0].update(endpoint="http://fakoli-dark.example.:8000/v1"),
            "resources[0].endpoint",
        ),
        (
            lambda data: data["resources"][0].update(endpoint="http://127.0.0.1.:8000/v1"),
            "resources[0].endpoint",
        ),
    ],
)
def test_trailing_dot_loopback_aliases_are_rejected(setter, path):
    data = _topology()
    setter(data)

    assert path in _paths(validate_topology(data))


def test_canonical_hostname_address_and_endpoint_remain_valid():
    data = _topology()
    data["hosts"][0]["address"] = "fakoli-dark.example"
    data["resources"][0]["endpoint"] = "http://fakoli-dark.example:8000/v1"

    assert validate_topology(data).ok


@pytest.mark.parametrize(
    ("setter", "path", "code"),
    [
        (lambda data: data["hosts"][0].update(address="0.0.0.0."), "hosts[0].address", "host"),
        (lambda data: data["hosts"][0].update(address="100.64.0.10."), "hosts[0].address", "host"),
        (
            lambda data: data["resources"][0].update(endpoint="http://0.0.0.0.:8000/v1"),
            "resources[0].endpoint",
            "host",
        ),
        (
            lambda data: data["resources"][0].update(endpoint="http://100.64.0.10.:8000/v1"),
            "resources[0].endpoint",
            "host",
        ),
    ],
)
def test_trailing_dot_ip_aliases_are_rejected_with_canonical_address_errors(setter, path, code):
    data = _topology()
    setter(data)

    errors = [error for error in validate_topology(data).errors if error.path == path]
    assert any(error.code == code for error in errors)


def test_malformed_uri_like_host_address_returns_a_structured_validation_error():
    data = _topology()
    data["hosts"][0]["address"] = "ssh://[::1"

    errors = [error for error in validate_topology(data).errors if error.path == "hosts[0].address"]
    assert any(error.code == "host" for error in errors)


def test_malformed_control_host_returns_a_structured_error_when_ipv4_parser_rejects_it(monkeypatch):
    def reject_control_host(_: str) -> bytes:
        raise ValueError("malformed control host")

    monkeypatch.setattr("anvil_serving.topology.socket.inet_aton", reject_control_host)
    data = _topology()
    data["hosts"][0]["address"] = "bad..host"

    errors = [error for error in validate_topology(data).errors if error.path == "hosts[0].address"]
    assert any(error.code == "host" for error in errors)


@pytest.mark.parametrize(
    ("role", "workload"),
    [
        ("llm-serve", "llm"),
        ("stt-serve", "stt"),
        ("tts-serve", "tts"),
        ("renamed-worker", "model"),
    ],
)
def test_typed_model_workloads_cannot_bypass_model_free_capacity(role, workload):
    data = _topology()
    resource = data["resources"][1]
    resource.update(
        id="candidate",
        role=role,
        workload=workload,
        host="gateway-host",
        runtime="gateway-native",
    )
    resource.pop("gpu_role")

    errors = [error for error in validate_topology(data).errors if error.path == "resources[1].host"]
    assert any(error.code == "capacity_policy" for error in errors)


@pytest.mark.parametrize("role", ["llm", "model", "stt", "tts", "inference"])
def test_recognized_model_roles_cannot_bypass_model_free_capacity_without_a_workload_field(role):
    data = _topology()
    resource = data["resources"][1]
    resource.update(id="candidate", role=role, host="gateway-host", runtime="gateway-native")
    resource.pop("gpu_role")

    errors = [error for error in validate_topology(data).errors if error.path == "resources[1].host"]
    assert any(error.code == "capacity_policy" for error in errors)


@pytest.mark.parametrize("role", ["LLMServe", "STTService", "ASRWorker"])
def test_pascal_case_acronym_model_roles_cannot_bypass_model_free_capacity_without_a_workload_field(role):
    data = _topology()
    resource = data["resources"][1]
    resource.update(id="candidate", role=role, host="gateway-host", runtime="gateway-native")
    resource.pop("gpu_role")

    errors = [error for error in validate_topology(data).errors if error.path == "resources[1].host"]
    assert any(error.code == "capacity_policy" for error in errors)


@pytest.mark.parametrize("identity", ["transcriptionist-service", "modeling-service", "speech-to-textbook"])
def test_model_phrase_inference_requires_complete_tokens(identity):
    data = _topology()
    resource = data["resources"][1]
    resource.update(id=identity, role=identity)
    resource.pop("gpu_role")

    topology = parse_topology(data)
    assert topology.resource(identity).workload == "service"


@pytest.mark.parametrize("identity", ["MetricsService", "GatewayHTTPProxy"])
def test_pascal_case_service_names_remain_service_workloads(identity):
    data = _topology()
    resource = data["resources"][1]
    resource.update(id="candidate", role=identity)
    resource.pop("gpu_role")

    topology = parse_topology(data)
    assert topology.resource("candidate").workload == "service"


@pytest.mark.parametrize("identity", ["notllmv2apiserver", "llmv2apiserverish", "llmv2apiworkerendpointish"])
def test_compact_model_marker_inference_requires_a_bounded_identity(identity):
    data = _topology()
    resource = data["resources"][1]
    resource.update(id="candidate", role=identity)
    resource.pop("gpu_role")

    assert parse_topology(data).resource("candidate").workload == "service"


@pytest.mark.parametrize(
    "identity",
    [
        "llmv2apiworkerendpoint",
        "llmv2apiendpointserverserviceworker",
        "llmv2apiapiapiapiapiapi",
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


def test_compact_model_suffix_classifier_handles_long_exact_chains_without_slicing_work():
    data = _topology()
    identity = "llm" + "api" * 8_192
    resource = data["resources"][1]
    resource.update(id=identity, role=identity)
    resource.pop("gpu_role")

    assert parse_topology(data).resource(identity).workload == "model"


@pytest.mark.parametrize("token", ["llm", "stt", "tts", "model"])
def test_model_workload_tokens_still_infer(token):
    data = _topology()
    resource = data["resources"][1]
    identity = f"{token}-service"
    resource.update(id=identity, role=identity)
    resource.pop("gpu_role")

    topology = parse_topology(data)
    assert topology.resource(identity).workload == "model"


@pytest.mark.parametrize(
    "identity",
    [
        "LLMAPI",
        "STTAPI",
        "LLMv2Service",
        "ASRv3Worker",
        "LLMAPISERVER",
        "llmapiserver",
        "llmv2apiserver",
        "LLMv2APISERVER",
    ],
)
def test_adjacent_model_markers_and_versions_infer_model_workloads(identity):
    data = _topology()
    resource = data["resources"][1]
    resource.update(id="candidate", role=identity)
    resource.pop("gpu_role")

    topology = parse_topology(data)
    assert topology.resource("candidate").workload == "model"


@pytest.mark.parametrize(
    "identity",
    [
        "LLMAPI",
        "STTAPI",
        "LLMv2Service",
        "ASRv3Worker",
        "LLMAPISERVER",
        "llmapiserver",
        "llmv2apiserver",
        "LLMv2APISERVER",
    ],
)
def test_adjacent_model_markers_cannot_be_downgraded_to_service_on_model_free_hosts(identity):
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


@pytest.mark.parametrize(
    ("resource_id", "role"),
    [
        ("candidate", "llm"),
        ("model-serve", "worker"),
        ("candidate", "stt"),
        ("candidate", "tts"),
        ("candidate", "LLMServe"),
        ("candidate", "STTService"),
        ("candidate", "ASRWorker"),
    ],
)
def test_explicit_service_workload_cannot_downgrade_an_inferred_model_workload(resource_id, role):
    data = _topology()
    resource = data["resources"][1]
    resource.update(
        id=resource_id,
        role=role,
        workload="service",
        host="gateway-host",
        runtime="gateway-native",
    )
    resource.pop("gpu_role")

    errors = validate_topology(data).errors
    assert any(error.path == "resources[1].workload" and error.code == "conflict" for error in errors)
    assert any(error.path == "resources[1].host" and error.code == "capacity_policy" for error in errors)


@pytest.mark.parametrize("role", ["audio-worker", "worker"])
def test_restricted_hosts_require_an_explicit_workload_for_ambiguous_resources(role):
    data = _topology()
    resource = data["resources"][2]
    resource.update(id="candidate", role=role)
    resource.pop("workload")

    errors = [error for error in validate_topology(data).errors if error.path == "resources[2].workload"]
    assert any(error.code == "required" for error in errors)


def test_restricted_hosts_allow_an_explicit_service_workload_for_ambiguous_resources():
    data = _topology()
    resource = data["resources"][2]
    resource.update(id="audio-worker", role="audio-worker", workload="service")

    assert validate_topology(data).ok


def test_gpu_only_typed_model_assignment_cannot_bypass_model_free_capacity():
    data = _topology()
    data["gpu_roles"].append(
        {
            "id": "gateway-gpu",
            "host": "gateway-host",
            "runtime": "gateway-native",
            "uuid": "GPU-89abcdef-0123-4567-89ab-cdef01234567",
        }
    )
    resource = data["resources"][1]
    resource.update(
        id="renamed-gpu-worker",
        role="worker",
        workload="llm",
        host="gateway-host",
        runtime="gateway-native",
        gpu_role="gateway-gpu",
    )

    errors = [error for error in validate_topology(data).errors if error.path == "resources[1].host"]
    assert any(error.code == "capacity_policy" for error in errors)


def test_unassigned_gpu_role_cannot_bypass_model_free_capacity():
    data = _topology()
    data["gpu_roles"].append(
        {
            "id": "gateway-gpu",
            "host": "gateway-host",
            "runtime": "gateway-native",
            "uuid": "GPU-89abcdef-0123-4567-89ab-cdef01234567",
        }
    )

    errors = [error for error in validate_topology(data).errors if error.path == "gpu_roles[1].host"]
    assert any(error.code == "capacity_policy" for error in errors)


def test_experimental_model_workloads_require_the_explicit_capacity_override():
    data = _topology()
    resource = data["resources"][1]
    resource.update(
        id="experimental-audio",
        role="audio-worker",
        workload="experimental-model",
        host="gateway-host",
        runtime="gateway-native",
    )
    resource.pop("gpu_role")

    errors = [error for error in validate_topology(data).errors if error.path == "resources[1].host"]
    assert any(error.code == "capacity_policy" for error in errors)

    data["capacity_policies"][0]["allow_experimental_model_workloads"] = True
    assert validate_topology(data).ok


def test_experimental_model_workloads_require_an_attached_capacity_policy():
    data = _topology()
    data["hosts"][2].pop("capacity_policy")
    resource = data["resources"][2]
    resource.update(id="experimental-audio", role="audio-worker", workload="experimental-model")

    errors = [error for error in validate_topology(data).errors if error.path == "resources[2].host"]
    assert any(error.code == "capacity_policy" for error in errors)


def test_experimental_model_workloads_accept_only_an_explicit_attached_override():
    data = _topology()
    resource = data["resources"][2]
    resource.update(id="experimental-audio", role="audio-worker", workload="experimental-model")
    data["capacity_policies"][0]["allow_experimental_model_workloads"] = True

    assert validate_topology(data).ok


def test_explicit_experimental_workload_can_own_a_gpu_on_a_model_free_host():
    data = _topology()
    data["capacity_policies"][0]["allow_experimental_model_workloads"] = True
    data["gpu_roles"][0].update(host="gateway-host", runtime="gateway-native")
    resource = data["resources"][1]
    resource.update(
        id="experimental-audio",
        role="audio-worker",
        workload="experimental-model",
        host="gateway-host",
        runtime="gateway-native",
    )

    assert validate_topology(data).ok


def test_experimental_override_does_not_allow_ordinary_model_workloads():
    data = _topology()
    data["capacity_policies"][0]["allow_experimental_model_workloads"] = True
    resource = data["resources"][1]
    resource.update(
        id="renamed-llm-worker",
        role="worker",
        workload="llm",
        host="gateway-host",
        runtime="gateway-native",
    )
    resource.pop("gpu_role")

    errors = [error for error in validate_topology(data).errors if error.path == "resources[1].host"]
    assert any(error.code == "capacity_policy" for error in errors)


def test_topology_rejects_mismatched_command_host_and_runtime():
    data = _topology()
    data["command_runtime"] = "runtime:serve-docker"

    errors = [error for error in validate_topology(data).errors if error.path == "command_runtime"]
    assert any(error.code == "reference" for error in errors)


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


def test_controller_allowed_operations_default_and_explicit_values_are_validated():
    data = _topology()
    controller = data["transports"][0]
    controller.pop("allowed_operations")

    topology = parse_topology(data)
    assert topology.transport("serve-controller").allowed_operations == ()

    controller["allowed_operations"] = ["router-status"]
    topology = parse_topology(data)
    assert topology.transport("serve-controller").allowed_operations == ("router-status",)

    controller["allowed_operations"] = None
    errors = [error for error in validate_topology(data).errors if error.path == "transports[0].allowed_operations"]
    assert any(error.code == "type" for error in errors)


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


def test_special_field_keys_use_quoted_paths_without_losing_array_indexes():
    data = _topology()
    data["hosts"].insert(0, "not-a-table")
    data["hosts"][1]["adress.with.dot"] = "100.64.0.99"
    data["resources"][0]['secret"value'] = "ordinary-value"

    paths = _paths(validate_topology(data))
    assert 'hosts[1]["adress.with.dot"]' in paths
    assert 'resources[0]["secret\\"value"]' in paths


@pytest.mark.parametrize("field", ["command_host", "command_runtime"])
def test_invalid_top_level_reference_types_use_consistent_field_paths(field):
    data = _topology()
    data[field] = []

    errors = [error for error in validate_topology(data).errors if error.path == field]
    assert any(error.code == "type" for error in errors)


def test_cyclic_mappings_return_structured_errors_instead_of_recursing():
    data = _topology()
    data["cycle"] = data

    result = validate_topology(data)
    assert any(error.path == "cycle" and error.code == "cycle" for error in result.errors)


def test_deep_nested_generic_data_returns_a_structured_depth_error_without_recursion():
    data = _topology()
    nested: object = "ordinary"
    for _ in range(80):
        nested = [nested]
    data["metadata"] = nested

    result = validate_topology(data)
    assert any(error.path.startswith("metadata") and error.code == "depth" for error in result.errors)


def test_scalar_heavy_generic_data_hits_the_total_element_resource_limit_early():
    data = _topology()
    data["metadata"] = [{"value": index} for index in range(5_000)]

    result = validate_topology(data)

    assert result.topology is None
    assert any(error.path.startswith("metadata") and error.code == "resource" for error in result.errors)


def test_nested_wide_snapshot_reads_only_one_item_past_the_remaining_budget(monkeypatch):
    class WideArray(list[object]):
        def __init__(self) -> None:
            self.items_read = 0

        def __iter__(self):
            for value in ("first", "second", "third"):
                self.items_read += 1
                yield value
            raise AssertionError("snapshot read beyond the item budget")

    monkeypatch.setattr(topology_module, "_MAX_NESTED_STRUCTURE_NODES", 5)
    wide = WideArray()
    errors: list[TopologyError] = []

    snapshot = topology_module._snapshot_topology_data({"metadata": [wide]}, errors)

    assert snapshot is None
    assert wide.items_read == 3
    assert errors == [TopologyError("metadata[0]", "nested topology data exceeds the resource limit", "resource")]


def test_nested_wide_snapshot_accepts_items_that_exactly_fit_the_remaining_budget(monkeypatch):
    monkeypatch.setattr(topology_module, "_MAX_NESTED_STRUCTURE_NODES", 5)
    errors: list[TopologyError] = []

    snapshot = topology_module._snapshot_topology_data({"metadata": [["first", "second"]]}, errors)

    assert snapshot == {"metadata": [["first", "second"]]}
    assert errors == []


def test_nested_empty_snapshot_container_is_valid_at_the_resource_boundary(monkeypatch):
    monkeypatch.setattr(topology_module, "_MAX_NESTED_STRUCTURE_NODES", 3)
    errors: list[TopologyError] = []

    snapshot = topology_module._snapshot_topology_data({"metadata": [[]]}, errors)

    assert snapshot == {"metadata": [[]]}
    assert errors == []


def test_shared_generic_mapping_dag_is_visited_once_per_container():
    shared: dict[str, object] = {"leaf": "ordinary"}
    for _ in range(20):
        shared = {"left": shared, "right": shared}
    data = _topology()
    data["metadata"] = shared

    result = validate_topology(data)
    assert any(error.path == "metadata" and error.code == "unknown" for error in result.errors)
    assert not any(error.code in {"depth", "resource"} for error in result.errors)
