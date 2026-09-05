"""Docker supervisor observations stay exact, bounded, and non-mutating."""

from __future__ import annotations

import json
import subprocess

import pytest


CONTAINER_ID = "a" * 64
IMAGE_ID = "sha256:" + "b" * 64
OWNERSHIP_LABEL = "io.anvil-serving.managed-by"
OWNERSHIP_VALUE = "models-recipes"


def binding(**changes):
    result = {
        "id": "voice-tts",
        "resource": "voice-tts",
        "manager": "docker",
        "engine": "external",
        "support": "supported",
        "container": "anvil-voice-tts",
        "image_id": IMAGE_ID,
        "identity_labels": {
            OWNERSHIP_LABEL: OWNERSHIP_VALUE,
            "io.anvil-serving.service": "voice-tts",
        },
        "startup_policy": "unless-stopped",
        "dependencies": [],
    }
    result.update(changes)
    return result


def inspection(*, status="running", running=True, restart="unless-stopped", image=IMAGE_ID,
               labels=None, pid=4321):
    return {
        "Id": CONTAINER_ID,
        "Name": "/anvil-voice-tts",
        "Image": image,
        "Config": {"Image": "registry.example/anvil-voice@" + IMAGE_ID, "Labels": labels or binding()["identity_labels"]},
        "HostConfig": {"RestartPolicy": {"Name": restart}},
        "State": {"Status": status, "Running": running, "Pid": pid},
    }


class FakeRun:
    """The Docker CLI boundary: every response is independently supplied."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        for prefix, response in self.responses:
            if list(argv[:len(prefix)]) == prefix:
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError("unexpected Docker command: %r" % (argv,))


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def context_inspection(endpoint, *, name="default"):
    return [{
        "Name": name,
        "Metadata": {},
        "Endpoints": {"docker": {"Host": endpoint, "SkipTLSVerify": False}},
        "TLSMaterial": {},
        "Storage": {"MetadataPath": "<IN MEMORY>", "TLSPath": "<IN MEMORY>"},
    }]


@pytest.mark.parametrize(
    "endpoint",
    ["unix:///var/run/docker.sock", "npipe:////./pipe/docker_engine"],
)
def test_verify_context_accepts_only_local_socket_transports(endpoint, monkeypatch):
    from anvil_serving.service_runtime.docker import Adapter

    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    runner = FakeRun([
        (["docker", "context", "inspect"], completed(json.dumps(context_inspection(endpoint))))
    ])

    assert Adapter(run=runner).verify_context() is None
    assert runner.calls[0][0] == ["docker", "context", "inspect"]


def test_docker_context_overrides_a_remote_docker_host(monkeypatch):
    from anvil_serving.service_runtime.docker import Adapter

    monkeypatch.setenv("DOCKER_HOST", "ssh://operator:host-secret@remote.example")
    monkeypatch.setenv("DOCKER_CONTEXT", "desktop-local")
    runner = FakeRun([
        (["docker", "context", "inspect", "desktop-local"], completed(json.dumps(
            context_inspection("unix:///Users/example/.docker/run/docker.sock", name="desktop-local")
        )))
    ])

    assert Adapter(run=runner).verify_context() is None
    assert runner.calls[0][0] == ["docker", "context", "inspect", "desktop-local"]


@pytest.mark.parametrize(
    "endpoint",
    [
        "ssh://operator:context-secret@remote.example",
        "tcp://127.0.0.1:2375",
        "npipe:////remote.example/pipe/docker_engine",
    ],
)
def test_verify_context_rejects_remote_context_endpoints_without_exposing_them(
    endpoint, monkeypatch
):
    from anvil_serving.service_runtime.contracts import ServiceError
    from anvil_serving.service_runtime.docker import Adapter

    monkeypatch.setenv("DOCKER_CONTEXT", "selected")
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    runner = FakeRun([
        (["docker", "context", "inspect", "selected"], completed(json.dumps(
            context_inspection(endpoint, name="selected")
        )))
    ])

    with pytest.raises(ServiceError) as raised:
        Adapter(run=runner).verify_context()

    assert raised.value.code == "remote_context"
    assert endpoint not in str(raised.value)
    assert "context-secret" not in str(raised.value)


def test_verify_context_rejects_remote_docker_host_without_running_docker(monkeypatch):
    from anvil_serving.service_runtime.contracts import ServiceError
    from anvil_serving.service_runtime.docker import Adapter

    endpoint = "ssh://operator:host-secret@remote.example"
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("DOCKER_HOST", endpoint)
    runner = FakeRun([])

    with pytest.raises(ServiceError) as raised:
        Adapter(run=runner).verify_context()

    assert raised.value.code == "remote_context"
    assert endpoint not in str(raised.value)
    assert "host-secret" not in str(raised.value)
    assert runner.calls == []


def test_verify_context_accepts_local_docker_host_without_running_docker(monkeypatch):
    from anvil_serving.service_runtime.docker import Adapter

    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    runner = FakeRun([])

    assert Adapter(run=runner).verify_context() is None
    assert runner.calls == []


def test_inspect_returns_exact_running_container_observation():
    from anvil_serving.service_runtime.docker import Adapter

    runner = FakeRun([(["docker", "inspect", "anvil-voice-tts"], completed(json.dumps([inspection()])) )])
    observed = Adapter(run=runner).inspect(binding())

    assert observed == {
        "registered": True,
        "running": True,
        "enabled": True,
        "pid": 4321,
        "state": "running",
        "identity": CONTAINER_ID,
        "manager": "docker",
    }
    assert runner.calls[0][1]["timeout"] <= 15


def test_inspect_refuses_container_with_wrong_pinned_image():
    from anvil_serving.service_runtime.contracts import ServiceError
    from anvil_serving.service_runtime.docker import Adapter

    runner = FakeRun([(["docker", "inspect", "anvil-voice-tts"], completed(json.dumps([inspection(image="sha256:" + "c" * 64)])))])

    with pytest.raises(ServiceError, match="image") as raised:
        Adapter(run=runner).inspect(binding())
    assert raised.value.code == "identity_mismatch"


def test_inspect_refuses_container_with_missing_declared_identity_label():
    from anvil_serving.service_runtime.contracts import ServiceError
    from anvil_serving.service_runtime.docker import Adapter

    runner = FakeRun([(["docker", "inspect", "anvil-voice-tts"], completed(json.dumps([inspection(labels={OWNERSHIP_LABEL: "anvil-services"})])) )])

    with pytest.raises(ServiceError, match="identity label"):
        Adapter(run=runner).inspect(binding())


def test_inspect_refuses_oversized_docker_responses_before_parsing_them():
    from anvil_serving.service_runtime.contracts import ServiceError
    from anvil_serving.service_runtime.docker import Adapter

    runner = FakeRun([(["docker", "inspect", "anvil-voice-tts"], completed("x" * (1024 * 1024 + 1)))])

    with pytest.raises(ServiceError, match="output") as raised:
        Adapter(run=runner).inspect(binding())
    assert raised.value.code == "output_too_large"


@pytest.mark.parametrize(
    ("stderr", "state"),
    [
        ("Error: No such container: anvil-voice-tts", "absent"),
        ("permission denied while trying to connect to the Docker daemon socket", "inaccessible"),
        ("Cannot connect to the Docker daemon", "unreachable"),
    ],
)
def test_inspect_distinguishes_absent_permission_and_daemon_states(stderr, state):
    from anvil_serving.service_runtime.docker import Adapter

    runner = FakeRun([(["docker", "inspect", "anvil-voice-tts"], completed(stderr=stderr, returncode=1))])

    assert Adapter(run=runner).inspect(binding()) == {
        "registered": False if state == "absent" else None,
        "running": False if state == "absent" else None,
        "enabled": None,
        "pid": None,
        "state": state,
        "identity": None,
        "manager": "docker",
    }


def test_describe_exposes_pinned_identity_without_environment_or_argv():
    from anvil_serving.service_runtime.docker import Adapter

    described = Adapter(run=FakeRun([])).describe(binding())

    assert described == {
        "manager": "docker",
        "container": "anvil-voice-tts",
        "image_id": IMAGE_ID,
        "identity_labels": {
            OWNERSHIP_LABEL: OWNERSHIP_VALUE,
            "io.anvil-serving.service": "voice-tts",
        },
        "startup_policy": "unless-stopped",
        "stop_policy_scope": "until an explicit docker start or restart-policy change",
    }
    assert "Env" not in described
    assert "Args" not in described


def test_plan_uses_immutable_inspected_id_and_preserves_current_startup_policy():
    from anvil_serving.service_runtime.docker import Adapter

    adapter = Adapter(run=FakeRun([]))
    stopped = {"registered": True, "running": False, "enabled": False, "identity": CONTAINER_ID}
    running = {"registered": True, "running": True, "enabled": True, "identity": CONTAINER_ID}

    assert adapter.plan(binding(), "up", stopped) == [["docker", "start", CONTAINER_ID]]
    assert adapter.plan(binding(), "up", running) == []
    assert adapter.plan(binding(), "down", stopped) == []
    assert adapter.plan(binding(), "down", running) == [["docker", "stop", CONTAINER_ID]]
    assert adapter.plan(binding(), "restart", running) == [["docker", "restart", CONTAINER_ID]]
    assert adapter.plan(binding(), "enable", stopped) == [["docker", "update", "--restart", "unless-stopped", CONTAINER_ID]]
    assert adapter.plan(binding(), "disable", running) == [["docker", "update", "--restart", "no", CONTAINER_ID]]


def test_startup_policy_defaults_to_unless_stopped_and_enable_never_uses_no():
    from anvil_serving.service_runtime.docker import Adapter

    adapter = Adapter(run=FakeRun([]))
    disabled = {"registered": True, "running": False, "enabled": False, "identity": CONTAINER_ID}
    default_binding = binding()
    del default_binding["startup_policy"]

    assert adapter.describe(default_binding)["startup_policy"] == "unless-stopped"
    assert adapter.plan(default_binding, "enable", disabled) == [
        ["docker", "update", "--restart", "unless-stopped", CONTAINER_ID]
    ]
    assert adapter.plan(binding(startup_policy="no"), "enable", disabled) == [
        ["docker", "update", "--restart", "unless-stopped", CONTAINER_ID]
    ]
    assert adapter.describe(binding(startup_policy="always"))["stop_policy_scope"] == (
        "until an explicit docker start or a Docker daemon restart"
    )


def test_plan_requires_immutable_identity_and_rejects_unknown_actions():
    from anvil_serving.service_runtime.contracts import ServiceError
    from anvil_serving.service_runtime.docker import Adapter

    adapter = Adapter(run=FakeRun([]))
    with pytest.raises(ServiceError, match="immutable") as raised:
        adapter.plan(binding(), "up", {"registered": True, "running": False, "identity": None})
    assert raised.value.code == "identity_required"
    with pytest.raises(ServiceError, match="action"):
        adapter.plan(binding(), "rotate", {"registered": True, "running": True, "identity": CONTAINER_ID})


def test_plan_treats_an_absent_container_as_an_idempotent_down_and_refuses_unknown_state():
    from anvil_serving.service_runtime.contracts import ServiceError
    from anvil_serving.service_runtime.docker import Adapter

    adapter = Adapter(run=FakeRun([]))
    assert adapter.plan(binding(), "down", {"registered": False, "running": False}) == []
    with pytest.raises(ServiceError, match="known") as raised:
        adapter.plan(
            binding(),
            "down",
            {"registered": True, "running": None, "enabled": True, "identity": CONTAINER_ID},
        )
    assert raised.value.code == "unknown_state"


def test_inspect_requires_state_running_to_be_a_boolean():
    from anvil_serving.service_runtime.contracts import ServiceError
    from anvil_serving.service_runtime.docker import Adapter

    runner = FakeRun([
        (["docker", "inspect", "anvil-voice-tts"], completed(json.dumps([inspection(running="false")])))
    ])
    with pytest.raises(ServiceError, match="Running") as raised:
        Adapter(run=runner).inspect(binding())
    assert raised.value.code == "malformed_response"


def test_logs_uses_bounded_tail_and_immutable_identity():
    from anvil_serving.service_runtime.docker import Adapter

    runner = FakeRun([
        (["docker", "inspect", "anvil-voice-tts"], completed(json.dumps([inspection()]))),
        (["docker", "logs"], completed("first\nsecond\n")),
    ])
    assert Adapter(run=runner).logs(binding(), 2) == ["first", "second"]
    assert runner.calls[1][0] == ["docker", "logs", "--tail", "2", CONTAINER_ID]
    assert runner.calls[1][1]["timeout"] <= 15


def test_logs_include_stderr_and_redact_shared_secret_patterns():
    from anvil_serving.service_runtime.docker import Adapter

    runner = FakeRun([
        (["docker", "inspect", "anvil-voice-tts"], completed(json.dumps([inspection()]))),
        (["docker", "logs"], completed("stdout\n", "API_KEY=must-not-leak\n")),
    ])

    assert Adapter(run=runner).logs(binding(), 2) == ["stdout", "API_KEY=<redacted>"]


def test_command_bounds_combined_stdout_and_stderr_and_classifies_timeout():
    from anvil_serving.service_runtime.contracts import ServiceError
    from anvil_serving.service_runtime.docker import Adapter

    oversized = FakeRun([
        (["docker", "inspect", "anvil-voice-tts"], completed("x" * 600_000, "y" * 600_000))
    ])
    with pytest.raises(ServiceError, match="output") as raised:
        Adapter(run=oversized).inspect(binding())
    assert raised.value.code == "output_too_large"

    timed_out = FakeRun([
        (["docker", "inspect", "anvil-voice-tts"], subprocess.TimeoutExpired(["docker"], 15))
    ])
    with pytest.raises(ServiceError, match="timed out") as raised:
        Adapter(run=timed_out).inspect(binding())
    assert raised.value.code == "timeout"


def test_discover_filters_to_ownership_label_and_returns_safe_metadata():
    from anvil_serving.service_runtime.docker import Adapter

    row = inspection()
    runner = FakeRun([
        (["docker", "ps", "-a", "--no-trunc", "--filter"], completed(CONTAINER_ID + "\n")),
        (["docker", "inspect", CONTAINER_ID], completed(json.dumps([row]))),
    ])

    assert Adapter(run=runner).discover() == [{
        "manager": "docker",
        "container": "anvil-voice-tts",
        "identity": CONTAINER_ID,
        "image_id": IMAGE_ID,
        "identity_labels": binding()["identity_labels"],
        "state": "running",
        "running": True,
    }]
    assert "--no-trunc" in runner.calls[0][0]
    assert "label=%s=%s" % (OWNERSHIP_LABEL, OWNERSHIP_VALUE) in runner.calls[0][0]
