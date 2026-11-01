import io
import json
import subprocess
from types import SimpleNamespace

import pytest

from anvil_serving import gpu_test_job as job


GPU = "GPU-00000000-0000-0000-0000-000000000001"
IMAGE = "sha256:" + "1" * 64


def spec(**changes):
    value = {
        "schema": job.SCHEMA, "image": IMAGE,
        "gpu": {"uuid": GPU, "compute_capability": [12, 0], "idle_baseline_mib": 0},
        "limits": {"cpus": 4, "memory_mib": 1024, "memory_swap_mib": 1024, "timeout_seconds": 30},
        "environment": dict(job.ENVIRONMENT),
        "sources": [{"path": "/image/test.py", "sha256": "2" * 64}],
        "commands": [{"id": "check", "kind": "standalone", "argv": ["python3", "/image/test.py"], "cwd": None, "source_paths": ["/image/test.py"], "completion_markers": ["ok"]}],
    }
    value.update(changes)
    return value


@pytest.fixture(autouse=True)
def native(monkeypatch):
    monkeypatch.setattr(job.platform, "system", lambda: "Linux")
    monkeypatch.setattr(job.platform, "release", lambda: "6.8")
    real_path = job.Path
    monkeypatch.setattr(job, "Path", lambda value: SimpleNamespace(exists=lambda: False) if value == "/.dockerenv" else real_path(value))


class Runtime:
    def __init__(self, busy=False, wrong_owner=False, triton_cache_dir="/tmp/triton_cache"):
        self.calls, self.busy, self.wrong_owner, self.removed = [], busy, wrong_owner, False
        self.triton_cache_dir = triton_cache_dir
        self.label = None

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        assert kwargs["env"]["DOCKER_HOST"] == "unix:///var/run/docker.sock"
        assert "DOCKER_CONTEXT" not in kwargs["env"]
        if argv[0] == "nvidia-smi":
            if "compute_cap" in argv[1]: out = GPU + ", 12.0\n"
            elif "compute-apps" in argv[1]: out = (GPU + ", 9\n") if self.busy else ""
            else: out = GPU + ", Test GPU, 00000000:01:00.0, 24000, 0, Disabled, 1\n"
            return subprocess.CompletedProcess(argv, 0, out, "")
        if "image" in argv: return subprocess.CompletedProcess(argv, 0, json.dumps(IMAGE), "")
        if "create" in argv:
            self.label = argv[argv.index("--label") + 1].split("=", 1)[1]
            return subprocess.CompletedProcess(argv, 0, "a" * 64, "")
        if "inspect" in argv:
            if self.removed: return subprocess.CompletedProcess(argv, 1, "", "no such object")
            if "{{json .}}" in argv:
                host = {"ReadonlyRootfs": True, "Privileged": False, "CapDrop": ["ALL"], "CapAdd": None,
                        "SecurityOpt": ["no-new-privileges"], "NetworkMode": "none", "Memory": 1024 * 1024**2,
                        "MemorySwap": 1024 * 1024**2 + 1, "NanoCpus": 4 * 10**9, "PidsLimit": job.PIDS_LIMIT, "Binds": None,
                        "Tmpfs": {"/tmp": "rw,nosuid,nodev,exec,size=4g"},
                        "DeviceRequests": [{"Count": 0, "DeviceIDs": [GPU], "Capabilities": [["gpu"]]}]}
                env = [key + "=" + value for key, value in {**job.ENVIRONMENT, "TORCH_EXTENSIONS_DIR": "/tmp/torch_extensions", "TRITON_CACHE_DIR": self.triton_cache_dir, "TMPDIR": "/tmp", "PYTHONOPTIMIZE": "0", "PYTEST_ADDOPTS": ""}.items()]
                return subprocess.CompletedProcess(argv, 0, json.dumps({"Id": "a" * 64, "HostConfig": host, "Config": {"Env": env}, "Mounts": []}), "")
            label = "other" if self.wrong_owner else self.label
            return subprocess.CompletedProcess(argv, 0, json.dumps({job.LABEL: label}), "")
        if "rm" in argv:
            self.removed = True
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)


def good_attach(argv, *, timeout):
    assert "start" in argv and timeout > 0
    return 0, 'ANVIL_GPU_TEST_RESULT {"ok": true, "commands": [], "cgroups": {"memory.max": "1073741824", "memory.swap.max": "0", "cpu.max": "400000 100000", "pids.max": "1024", "pids.current": "1", "pids.events": "max 0"}, "cgroups_after": {"memory.max": "1073741824", "memory.swap.max": "0", "cpu.max": "400000 100000", "pids.max": "1024", "pids.current": "1", "pids.events": "max 0"}}\n', False, False


def test_schema_rejects_unknown_pytest_option_and_unreviewed_deselect():
    bad = spec(commands=[{"id": "pytest", "kind": "pytest", "argv": ["python3", "-m", "pytest", "-q", "/image/test.py", "--collect-only"], "cwd": None, "source_paths": ["/image/test.py"], "completion_markers": []}])
    with pytest.raises(ValueError, match="reviewed"):
        job.validate_spec(bad)
    bad["commands"][0]["argv"][-1:] = ["--deselect", "/other.py::test_x"]
    with pytest.raises(ValueError, match="deselect"):
        job.validate_spec(bad)


def test_attach_eof_with_live_process_is_killed_and_reaped():
    class Process:
        def __init__(self): self.stdout, self.returncode, self.killed = io.BytesIO(), None, False
        def poll(self): return self.returncode
        def kill(self): self.killed = True; self.returncode = -9
        def wait(self, timeout): return self.returncode
    process = Process()
    _exit, _logs, expired, _truncated = job._bounded_attach(["docker"], timeout=10**12, _runner=lambda *args, **kwargs: process)
    assert process.killed and expired


def test_owned_cleanup_and_timeout_are_required():
    runtime = Runtime()
    result = job.run_job(spec(), dry_run=False, _runner=runtime,
                         _attach=lambda *args, **kwargs: (137, "", True, False))
    assert not result["ok"] and result["cleanup"]["ok"] and runtime.removed
    assert result["container_id"] == "a" * 64
    command = next(call for call in runtime.calls if "create" in call)
    assert command[command.index("--network") + 1] == "none"
    assert "--volume" not in command and "--mount" not in command
    assert int(command[command.index("--memory-swap") + 1]) == int(command[command.index("--memory") + 1]) + 1
    assert command[command.index("--pids-limit") + 1] == str(job.PIDS_LIMIT)


def test_wrong_owner_never_removed():
    runtime = Runtime(wrong_owner=True)
    result = job.run_job(spec(), dry_run=False, _runner=runtime, _attach=good_attach)
    assert not result["ok"] and not runtime.removed
