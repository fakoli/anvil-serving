import subprocess

import pytest

from anvil_serving.workbench_app.container_exec import ContainerExec, ContainerExecError


CONTAINER = "a" * 64


class Process:
    def __init__(self, stdout, stderr, code=0):
        self.stdout, self.stderr, self.code = stdout, stderr, code

    def wait(self, timeout):
        self.stdout.write(b"ready\n")
        self.stderr.write(b"")
        return self.code

    def kill(self):
        pass


def executor(calls):
    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return Process(kwargs["stdout"], kwargs["stderr"])
    return popen


def binding(**changes):
    return {
        "id": "chat-exec", "host_id": "host-a", "execution_runtime": "docker-local", "service": "chat", "container_id": CONTAINER,
        "commands": {"health": ["/usr/local/bin/health"]},
        **changes,
    }


def test_preview_and_execution_use_only_the_declared_identity_and_argv():
    calls = []
    value = ContainerExec([binding()], popen=executor(calls))
    preview = value.preview("chat-exec", "health")
    assert preview["container_id"] == CONTAINER
    assert value.read_services("chat-exec")[0]["exec"]["commands"] == ["health"]
    result = value.execute(preview, host_id="host-a", execution_runtime="docker-local")
    assert result == {"status": "completed", "exit_code": 0, "output": "ready\n", "truncated": False}
    assert calls[0][0] == ["docker", "exec", "--", CONTAINER, "/usr/local/bin/health"]
    assert calls[0][1]["stdin"] is subprocess.DEVNULL
    assert calls[0][1]["stdout"] is not None and calls[0][1]["stderr"] is not None


def test_rejects_arbitrary_targets_shells_flags_and_stale_reviews():
    with pytest.raises(ValueError):
        ContainerExec([binding(container_id="sample-container")])
    with pytest.raises(ValueError):
        ContainerExec([binding(commands={"shell": ["sh", "-c", "id"]})])
    value = ContainerExec([binding()], popen=executor([]))
    with pytest.raises(ContainerExecError, match="declared"):
        value.preview("chat-exec", "other")
    stale = value.preview("chat-exec", "health")
    stale["container_id"] = "b" * 64
    with pytest.raises(ContainerExecError, match="review"):
        value.execute(stale, host_id="host-a", execution_runtime="docker-local")


def test_timeout_and_output_limit_are_bounded():
    class Timeout(Process):
        def wait(self, timeout):
            raise subprocess.TimeoutExpired("docker", timeout)
    value = ContainerExec([binding(max_output_bytes=4)], popen=lambda argv, **kwargs: Timeout(kwargs["stdout"], kwargs["stderr"]))
    assert value.execute(value.preview("chat-exec", "health"), host_id="host-a", execution_runtime="docker-local")["status"] == "timeout"
