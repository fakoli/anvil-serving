"""Hermetic policy tests: no Docker daemon, container, GPU, or network."""
import json
from types import SimpleNamespace

import pytest

from anvil_serving import network_policy, serves
from tests.conftest import proc


def _managed_network(*, internal=True, driver="bridge", managed=True):
    return [{
        "Name": network_policy.MODEL_EGRESS_DENY_NETWORK,
        "Driver": driver,
        "Internal": internal,
        "Labels": (
            {network_policy.MANAGED_BY_LABEL: network_policy.MANAGED_BY_VALUE}
            if managed else {}
        ),
    }]


def test_ensure_model_egress_network_accepts_exact_existing_network():
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(_managed_network()))

    result = network_policy.ensure_model_egress_network(_run=run)

    assert result == {
        "name": network_policy.MODEL_EGRESS_DENY_NETWORK,
        "driver": "bridge",
        "internal": True,
        "managed_by": network_policy.MANAGED_BY_VALUE,
    }
    assert [call[0][:3] for call in calls] == [["docker", "network", "inspect"]]


def test_ensure_model_egress_network_creates_then_proves_postcondition():
    calls = []
    responses = iter([
        SimpleNamespace(returncode=1, stdout="", stderr="not found"),
        SimpleNamespace(returncode=0, stdout=network_policy.MODEL_EGRESS_DENY_NETWORK),
        SimpleNamespace(returncode=0, stdout=json.dumps(_managed_network())),
    ])

    def run(argv, **kwargs):
        calls.append(argv)
        return next(responses)

    network_policy.ensure_model_egress_network(_run=run)

    assert calls[1] == [
        "docker", "network", "create", "--driver", "bridge", "--internal",
        "--label", "io.anvil-serving.managed-by=model-egress-policy",
        "anvil-serving-model-egress-denied",
    ]
    assert calls[2] == calls[0]


@pytest.mark.parametrize(
    "document,match",
    [
        (_managed_network(internal=False), "not an internal bridge"),
        (_managed_network(driver="host"), "not an internal bridge"),
        (_managed_network(managed=False), "not owned by Anvil Serving"),
    ],
)
def test_ensure_model_egress_network_rejects_fail_open_or_spoofed_network(
    document, match,
):
    def run(_argv, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps(document))

    with pytest.raises(network_policy.NetworkPolicyError, match=match):
        network_policy.ensure_model_egress_network(_run=run)


def test_validate_compose_document_requires_every_attached_network_internal():
    document = {
        "services": {"model": {"networks": {"isolated": None}}},
        "networks": {"isolated": {"internal": True}},
    }

    assert network_policy.validate_compose_document(document, ["model"]) == [{
        "service": "model",
        "network_egress": "deny",
        "networks": ["isolated"],
    }]

    document["services"]["model"]["networks"]["public"] = None
    document["networks"]["public"] = {"internal": False}
    with pytest.raises(network_policy.NetworkPolicyError, match="without internal=true"):
        network_policy.validate_compose_document(document, ["model"])


def test_validate_compose_document_accepts_network_mode_none_and_rejects_host():
    document = {
        "services": {"model": {"network_mode": "none"}},
        "networks": {},
    }
    assert network_policy.validate_compose_document(document, ["model"])[0][
        "networks"
    ] == []

    document["services"]["model"]["network_mode"] = "host"
    with pytest.raises(network_policy.NetworkPolicyError, match="not externally isolated"):
        network_policy.validate_compose_document(document, ["model"])


def test_ad_hoc_compose_allow_exception_requires_durable_reason_labels():
    document = {
        "services": {
            "proxy": {
                "labels": {
                    network_policy.EGRESS_LABEL: "allow",
                    network_policy.EGRESS_ROLE_LABEL: "voice-gateway",
                    network_policy.EGRESS_REASON_LABEL: "connects to declared remote audio endpoints",
                },
            },
        },
        "networks": {"default": {}},
    }

    result = network_policy.validate_compose_document(
        document, ["proxy"], policy=None
    )
    assert result[0]["network_egress"] == "allow"
    assert result[0]["role"] == "voice-gateway"

    del document["services"]["proxy"]["labels"][network_policy.EGRESS_REASON_LABEL]
    with pytest.raises(network_policy.NetworkPolicyError, match="requires a non-empty"):
        network_policy.validate_compose_document(document, ["proxy"], policy=None)

    document["services"]["proxy"]["labels"][network_policy.EGRESS_REASON_LABEL] = "reason"
    document["services"]["proxy"]["labels"][network_policy.EGRESS_ROLE_LABEL] = "model"
    with pytest.raises(network_policy.NetworkPolicyError, match="one of"):
        network_policy.validate_compose_document(document, ["proxy"], policy=None)


def test_manifest_allow_exception_requires_reason(tmp_path):
    manifest = tmp_path / "serves.toml"
    manifest.write_text(
        """
[[serve]]
name = "model"
container = "model"
runtime = "docker"
port = 30000
model = "org/model"
engine = "vllm"
network_egress = "allow"
network_egress_role = "capability-gateway"
up = "docker compose -f compose.yml up -d model"
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires a non-empty"):
        serves.load_manifest(manifest)


def test_manifest_model_cannot_request_gateway_egress(tmp_path):
    manifest = tmp_path / "serves.toml"
    manifest.write_text(
        """
[[serve]]
name = "model"
container = "model"
runtime = "docker"
port = 30000
model = "org/model"
engine = "vllm"
network_egress = "allow"
network_egress_role = "capability-gateway"
network_egress_reason = "misclassified model"
up = "docker compose -f compose.yml up -d model"
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="gpu_inference=false"):
        serves.load_manifest(manifest)


def test_cmd_up_refuses_external_compose_network_before_start():
    serve = [{
        "name": "model",
        "container": "model",
        "port": 30000,
        "health": "/health",
        "model": "org/model",
        "up": ["docker", "compose", "-f", "compose.yml", "up", "-d", "model"],
    }]
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        if "config" in argv:
            return proc(0, json.dumps({
                "services": {"model": {"networks": {"default": None}}},
                "networks": {"default": {"internal": False}},
            }))
        if argv[:3] == ["docker", "ps", "-a"]:
            return proc(0)
        return proc(0)

    assert serves.cmd_up(serve, ["model"], _run=run) == 1
    assert not any("up" in argv for argv in calls if argv[:2] == ["docker", "compose"])


def test_cmd_up_accepts_reasoned_allow_exception_on_external_network():
    serve = [{
        "name": "proxy",
        "container": "proxy",
        "port": 8765,
        "health": "/health",
        "model": "proxy",
        "gpu_inference": False,
        "network_egress": "allow",
        "network_egress_role": "capability-gateway",
        "network_egress_reason": "connects to declared upstream services",
        "up": ["docker", "compose", "-f", "compose.yml", "up", "-d", "proxy"],
    }]
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        if "config" in argv:
            return proc(0, json.dumps({
                "services": {"proxy": {"networks": {"default": None}}},
                "networks": {"default": {"internal": False}},
            }))
        if argv[:3] == ["docker", "ps", "-a"]:
            return proc(0)
        if argv[:2] == ["docker", "inspect"]:
            return proc(1, "", "Error: No such object")
        return proc(0)

    assert serves.cmd_up(serve, ["proxy"], _run=run) == 0
    assert any("up" in argv for argv in calls if argv[:2] == ["docker", "compose"])


def test_cmd_up_refuses_model_masquerading_as_gateway_before_any_docker_call(capsys):
    serve = [{
        "name": "model",
        "container": "model",
        "port": 30000,
        "model": "org/model",
        "network_egress": "allow",
        "network_egress_role": "capability-gateway",
        "network_egress_reason": "misclassified model",
        "up": ["docker", "compose", "-f", "compose.yml", "up", "-d", "model"],
    }]
    calls = []

    assert serves.cmd_up(
        serve,
        ["model"],
        _run=lambda argv, **_kwargs: calls.append(argv),
    ) == 1
    assert calls == []
    assert "gpu_inference=false" in capsys.readouterr().out


def test_cmd_up_refuses_opaque_default_deny_launch_before_mutation(capsys):
    serve = [{
        "name": "model",
        "container": "model",
        "port": 30000,
        "health": "/health",
        "model": "org/model",
        "up": ["bash", "launch-model.sh"],
    }]
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        if argv[:3] == ["docker", "ps", "-a"]:
            return proc(0)
        return proc(0)

    assert serves.cmd_up(serve, ["model"], _run=run) == 1
    assert ["bash", "launch-model.sh"] not in calls
    assert "models recipes load" in capsys.readouterr().out


def test_cmd_up_refuses_existing_recipe_container_without_recreate(capsys):
    serve = [{
        "name": "model",
        "container": "model",
        "port": 30000,
        "health": "/health",
        "model": "org/model",
        "up": [
            "python", "-m", "anvil_serving.cli",
            "models", "recipes", "load", "org/model", "--confirm",
        ],
    }]
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        if argv[:3] == ["docker", "ps", "-a"]:
            return proc(0, json.dumps({"Names": "model", "State": "exited"}) + "\n")
        if argv[:2] == ["docker", "inspect"]:
            return proc(0, "exited\n")
        return proc(0)

    assert serves.cmd_up(serve, ["model"], _run=run) == 1
    assert not any(argv[:2] == ["docker", "start"] for argv in calls)
    assert "--recreate" in capsys.readouterr().out
