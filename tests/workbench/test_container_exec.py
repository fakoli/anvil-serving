import subprocess


from anvil_serving.workbench_app.container_exec import ContainerExec


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


def test_timeout_and_output_limit_are_bounded():
    class Timeout(Process):
        def wait(self, timeout):
            raise subprocess.TimeoutExpired("docker", timeout)
    value = ContainerExec([binding(max_output_bytes=4)], popen=lambda argv, **kwargs: Timeout(kwargs["stdout"], kwargs["stderr"]))
    assert value.execute(value.preview("chat-exec", "health"), host_id="host-a", execution_runtime="docker-local")["status"] == "timeout"
