"""Containment contracts without executing generated code or contacting Docker."""

import json
import os
import subprocess

import pytest

from anvil_serving.benchmarking import aider_containment as containment
from anvil_serving.benchmarking.jobs import BenchmarkJobError

IMAGE = "sha256:" + "a" * 64
IDENTITY = "b" * 64


class Docker:
    def __init__(self, run, *, drift=None, timeout=False, lost_create=False):
        self.run = run
        self.drift = drift
        self.timeout = timeout
        self.lost_create = lost_create
        self.calls = []
        self.container = None
        self.image = {"Id": IMAGE, "Config": {"Env": ["PATH=/usr/bin:/bin"]}}

    def __call__(self, argv, cwd, timeout):
        self.calls.append(argv)
        assert cwd is None
        def result(stdout="", stderr="", code=0):
            return subprocess.CompletedProcess(argv, code, stdout.encode(), stderr.encode())
        if argv[1:3] == ("image", "inspect"):
            return result(json.dumps([self.image]))
        if argv[1] == "create":
            record_path = self.run / "aider/containers" / (argv[argv.index("--name") + 1].removeprefix("anvil-aider-") + ".json")
            record = json.loads(record_path.read_text())
            assert record["state"] == "planned" and record["container_id"] is None
            assert "--network=none" in argv and "--read-only" in argv
            assert "--memory=8g" in argv and "--memory-swap=8g" in argv
            assert "--cpus=4" in argv and "--pids-limit=512" in argv
            assert "--cap-drop=ALL" in argv and "--security-opt=no-new-privileges:true" in argv
            assert argv.count("--mount") == 1
            assert [arg for arg in argv if arg.startswith("--env")] == ["--env=HOME=/tmp"]
            assert not any(arg.startswith(("--gpus", "--device", "--privileged")) for arg in argv)
            self.container = {
                "Id": IDENTITY, "Name": "/" + record["name"], "Image": IMAGE,
                "Config": {"User": record["user"], "WorkingDir": "/task", "Entrypoint": ["/bin/sh"],
                           "Cmd": record["command_args"], "Env": record["environment"],
                           "Labels": containment._labels(record)},
                "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True, "Privileged": False,
                               "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges:true"],
                               "Memory": 8 * 1024**3, "MemorySwap": 8 * 1024**3, "NanoCpus": 4 * 10**9,
                               "PidsLimit": 512, "Tmpfs": {"/tmp": containment._TMPFS}, "PidMode": "",
                               "IpcMode": "private", "UTSMode": "", "CgroupnsMode": "private",
                               "RestartPolicy": {"Name": "no"}, "LogConfig": {"Type": "none"}, "Runtime": "runc"},
                "Mounts": [{"Type": "bind", "Source": record["task_dir"], "Destination": "/task",
                            "RW": True, "Propagation": "rprivate"}],
                "State": {"Status": "created", "ExitCode": 0},
            }
            if self.drift:
                self.drift(self.container)
            if self.lost_create:
                raise subprocess.TimeoutExpired(argv, timeout)
            return result(IDENTITY)
        if argv[1:3] == ("container", "inspect"):
            if self.container is None:
                return result(stderr="Error: No such container: " + argv[-1], code=1)
            return result(json.dumps([self.container]))
        if argv[1] == "start":
            if self.timeout:
                raise subprocess.TimeoutExpired(argv, timeout)
            self.container["State"] = {"Status": "exited", "ExitCode": 1}
            return result("upstream test failure\n", code=1)
        if argv[1:4] == ("container", "rm", "--force"):
            assert argv[-1] == IDENTITY
            self.container = None
            return result(IDENTITY)
        raise AssertionError(argv)


def setup(tmp_path):
    run = tmp_path / "owner" / "run"
    task = run / "aider/results/task"
    task.mkdir(parents=True)
    args = dict(task_dir=task, run_path=run, ownership_id="owner", run_id="run", image_id=IMAGE,
                container_binary="/usr/bin/docker")
    return run, task, args


def test_exact_isolation_and_upstream_completed_process(tmp_path):
    run, task, args = setup(tmp_path)
    docker = Docker(run)
    result = containment.run_contained_test("python -m unittest", runner=docker, **args)
    assert result.args == "python -m unittest" and result.returncode == 1
    assert result.stdout == "upstream test failure\n" and result.stderr is None
    assert docker.container is None
    record, = (run / "aider/containers").glob("*.json")
    value = json.loads(record.read_text())
    assert value["state"] == "removed" and value["returncode"] == 1
    assert value["user"] == f"{os.getuid()}:{os.getgid()}"
    create = next(call for call in docker.calls if call[1] == "create")
    assert create[-2:] == ("-c", "{\npython -m unittest\n} 2>&1")
    assert str(task) in create[create.index("--mount") + 1]


@pytest.mark.parametrize("section,key,value", [
    ("HostConfig", "NetworkMode", "host"), ("HostConfig", "ReadonlyRootfs", False),
    ("HostConfig", "Privileged", True), ("HostConfig", "DeviceRequests", [{"Capabilities": [["gpu"]]}]),
    ("HostConfig", "Devices", [{"PathOnHost": "/dev/sda"}]), ("HostConfig", "CapAdd", ["SYS_ADMIN"]),
    ("HostConfig", "Memory", 0), ("HostConfig", "MemorySwap", -1), ("HostConfig", "NanoCpus", 0),
    ("HostConfig", "PidsLimit", 0), ("HostConfig", "SecurityOpt", []),
    ("HostConfig", "Tmpfs", {}), ("HostConfig", "PidMode", "host"),
    ("HostConfig", "Runtime", "nvidia"), ("HostConfig", "Binds", ["/var/run/docker.sock:/sock"]),
    ("Config", "Env", ["API_KEY=synthetic"]), ("Config", "User", "0:0"),
    ("Config", "Entrypoint", ["unexpected"]), ("Config", "Cmd", ["unexpected"]),
])
def test_inspect_drift_never_starts(tmp_path, section, key, value):
    run, _, args = setup(tmp_path)
    docker = Docker(run, drift=lambda row: row[section].update({key: value}))
    with pytest.raises(BenchmarkJobError):
        containment.run_contained_test("false", runner=docker, **args)
    assert not any(call[1] == "start" for call in docker.calls)
    assert docker.container is None


def test_mount_and_image_drift_never_starts(tmp_path):
    run, _, args = setup(tmp_path)
    for drift in (lambda row: row["Mounts"].append({"Type": "volume", "Destination": "/extra"}),
                  lambda row: row.update(Image="sha256:" + "c" * 64)):
        docker = Docker(run, drift=drift)
        with pytest.raises(BenchmarkJobError):
            containment.run_contained_test("false", runner=docker, **args)
        assert not any(call[1] == "start" for call in docker.calls)
        # Image drift cannot be removed, even though the unique name matches.
        if docker.container:
            assert not any(call[1:3] == ("container", "rm") for call in docker.calls)
            for path in (run / "aider/containers").glob("*.json"):
                path.unlink()


@pytest.mark.parametrize("lost_create", [False, True])
def test_timeout_preserved_and_created_container_recovered(tmp_path, lost_create):
    run, _, args = setup(tmp_path)
    docker = Docker(run, timeout=True, lost_create=lost_create)
    with pytest.raises(subprocess.TimeoutExpired):
        containment.run_contained_test("sleep 999", runner=docker, **args)
    assert docker.container is None
    record, = (run / "aider/containers").glob("*.json")
    assert json.loads(record.read_text())["failure"] == "TimeoutExpired"


def test_wrong_owner_cannot_remove_and_later_recovery(tmp_path):
    run, _, args = setup(tmp_path)
    def changed_owner(row):
        row["Config"]["Labels"]["anvil.aider.ownership_id"] = "other"
    docker = Docker(run, drift=changed_owner)
    with pytest.raises(BenchmarkJobError):
        containment.run_contained_test("false", runner=docker, **args)
    receipt = containment.cleanup_aider_containers(run_path=run, ownership_id="owner", run_id="run", runner=docker)
    assert receipt["status"] == "incomplete" and not receipt["removed"]
    assert not any(call[1:3] == ("container", "rm") for call in docker.calls)
    docker.container["Config"]["Labels"]["anvil.aider.ownership_id"] = "owner"
    receipt = containment.cleanup_aider_containers(run_path=run, ownership_id="owner", run_id="run", runner=docker)
    assert receipt["status"] == "completed" and receipt["removed"] == [IDENTITY]
    assert containment.cleanup_aider_containers(run_path=run, ownership_id="owner", run_id="run", runner=docker)["removed"] == []


def test_invalid_inputs_and_symlinks_never_contact_docker(tmp_path):
    run, task, args = setup(tmp_path)
    docker = Docker(run)
    for changes in ({"image_id": "mutable:tag"}, {"timeout": float("nan")}, {"timeout": 181},
                    {"ownership_id": "other"}, {"task_dir": run}, {"task_dir": run / "aider"},
                    {"task_dir": task / ".."}):
        with pytest.raises(BenchmarkJobError):
            containment.run_contained_test("false", runner=docker, **{**args, **changes})
    (task / "escape").symlink_to(tmp_path)
    with pytest.raises(BenchmarkJobError):
        containment.run_contained_test("false", runner=docker, **args)
    assert not docker.calls


def test_image_environment_rejects_credential_and_id_drift(tmp_path):
    run, _, args = setup(tmp_path)
    for image in ({"Id": IMAGE, "Config": {"Env": ["API_KEY=synthetic"]}}, {"Id": "mutable"}):
        docker = Docker(run)
        docker.image = image
        with pytest.raises(BenchmarkJobError):
            containment.run_contained_test("false", runner=docker, **args)
        assert all(call[1:3] == ("image", "inspect") for call in docker.calls)


def test_cleanup_refuses_active_record_and_symlink(tmp_path):
    run, _, args = setup(tmp_path)
    docker = Docker(run, drift=lambda row: row.update(Image="sha256:" + "c" * 64))
    with pytest.raises(BenchmarkJobError):
        containment.run_contained_test("false", runner=docker, **args)
    record, = (run / "aider/containers").glob("*.json")
    docker.calls.clear()
    with containment._locked(record):
        receipt = containment.cleanup_aider_containers(run_path=run, ownership_id="owner", run_id="run", runner=docker)
    assert receipt["status"] == "incomplete" and not docker.calls
    record.unlink()
    record.symlink_to(tmp_path / "foreign")
    receipt = containment.cleanup_aider_containers(run_path=run, ownership_id="owner", run_id="run", runner=docker)
    assert receipt["status"] == "incomplete" and not docker.calls


def test_default_runner_is_bounded_and_has_no_host_secrets(monkeypatch):
    calls = []
    monkeypatch.setenv("EXAMPLE_API_KEY", "synthetic")
    monkeypatch.setenv("DOCKER_HOST", "tcp://example.test:2375")
    monkeypatch.setattr(containment, "_run_bounded_process", lambda *a, **kw: calls.append((a, kw)))
    containment._default_runner(("docker", "inspect", IDENTITY), None, 3)
    assert calls[0][1]["env"] == {"PATH": os.defpath}
    assert calls[0][1]["max_output_bytes"] == containment.MAX_OUTPUT_BYTES


def test_sequence_command_and_image_home_override(tmp_path):
    run, _, args = setup(tmp_path)
    docker = Docker(run)
    docker.image["Config"]["Env"] = ["HOME=/root", "PATH=/usr/bin:/bin"]
    containment.run_contained_test(["python", "-m", "unittest"], runner=docker, **args)
    create = next(call for call in docker.calls if call[1] == "create")
    assert create[-6:] == ("-c", 'exec "$@" 2>&1', "sh", "python", "-m", "unittest")
    record, = (run / "aider/containers").glob("*.json")
    assert json.loads(record.read_text())["environment"] == ["HOME=/tmp", "PATH=/usr/bin:/bin"]


def test_cleanup_error_cannot_turn_into_success(tmp_path):
    run, _, args = setup(tmp_path)
    docker = Docker(run)
    def runner(argv, cwd, timeout):
        if argv[1:3] == ("container", "rm"):
            return subprocess.CompletedProcess(argv, 1, b"", b"failed")
        return docker(argv, cwd, timeout)
    with pytest.raises(BenchmarkJobError, match="removal failed"):
        containment.run_contained_test("false", runner=runner, **args)
    assert docker.container is not None
    receipt = containment.cleanup_aider_containers(run_path=run, ownership_id="owner", run_id="run", runner=docker)
    assert receipt["status"] == "completed" and receipt["removed"] == [IDENTITY]


def test_interrupt_preserves_timeout_and_recoverable_registry(tmp_path):
    run, _, args = setup(tmp_path)
    docker = Docker(run, lost_create=True)
    def interrupted(argv, cwd, timeout):
        if argv[1:3] == ("container", "inspect"):
            raise OSError("daemon unreachable")
        return docker(argv, cwd, timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        containment.run_contained_test("false", runner=interrupted, **args)
    record, = (run / "aider/containers").glob("*.json")
    saved = json.loads(record.read_text())
    assert saved["container_id"] is None and saved["state"] == "planned"
    receipt = containment.cleanup_aider_containers(run_path=run, ownership_id="owner", run_id="run", runner=docker)
    assert receipt["status"] == "completed" and receipt["removed"] == [IDENTITY]


def test_immutable_id_replacement_is_not_removed(tmp_path):
    run, _, args = setup(tmp_path)
    docker = Docker(run, drift=lambda row: row.update(Id="c" * 64))
    with pytest.raises(BenchmarkJobError, match="ID drift"):
        containment.run_contained_test("false", runner=docker, **args)
    assert not any(call[1:3] == ("container", "rm") for call in docker.calls)


def test_lost_create_absence_stays_recoverable_and_blocks_next_test(tmp_path):
    run, _, args = setup(tmp_path)
    docker = Docker(run)
    def uncertain(argv, cwd, timeout):
        if argv[1] == "create":
            raise subprocess.TimeoutExpired(argv, timeout)
        return docker(argv, cwd, timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        containment.run_contained_test("false", runner=uncertain, **args)
    receipt = containment.cleanup_aider_containers(run_path=run, ownership_id="owner", run_id="run", runner=docker)
    assert receipt["status"] == "incomplete" and receipt["removed"] == []
    with pytest.raises(BenchmarkJobError, match="cleanup is unresolved"):
        containment.run_contained_test("false", runner=docker, **args)
