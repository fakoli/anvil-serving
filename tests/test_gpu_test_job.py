import hashlib
import io
import json
import os
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


def test_validation_fails_before_any_external_io():
    runtime = Runtime()
    bad = spec(environment={})
    with pytest.raises(ValueError, match="environment"):
        job.run_job(bad, _runner=runtime)
    assert not runtime.calls


def test_preview_reads_inventory_but_never_creates_container(monkeypatch):
    runtime = Runtime()
    monkeypatch.setenv("DOCKER_CONTEXT", "remote")
    result = job.run_job(spec(), _runner=runtime, _attach=good_attach)
    assert result["dry_run"] and not result["executed"]
    assert not any("create" in call for call in runtime.calls)


def test_busy_gpu_refuses_without_create():
    runtime = Runtime(busy=True)
    result = job.run_job(spec(), dry_run=False, _runner=runtime, _attach=good_attach)
    assert not result["ok"] and not result["executed"]
    assert not any("create" in call for call in runtime.calls)


def test_schema_rejects_unknown_pytest_option_and_unreviewed_deselect():
    bad = spec(commands=[{"id": "pytest", "kind": "pytest", "argv": ["python3", "-m", "pytest", "-q", "/image/test.py", "--collect-only"], "cwd": None, "source_paths": ["/image/test.py"], "completion_markers": []}])
    with pytest.raises(ValueError, match="reviewed"):
        job.validate_spec(bad)
    bad["commands"][0]["argv"][-1:] = ["--deselect", "/other.py::test_x"]
    with pytest.raises(ValueError, match="deselect"):
        job.validate_spec(bad)


def test_containment_rejects_wrong_triton_cache_before_start():
    runtime = Runtime(triton_cache_dir="/root/.triton")
    result = job.run_job(spec(), dry_run=False, _runner=runtime, _attach=good_attach)
    assert not result["ok"] and not any("start" in call for call in runtime.calls)
    assert result["cleanup"]["ok"]


def test_containment_readback_mismatch_stops_before_start(monkeypatch):
    runtime = Runtime()
    monkeypatch.setattr(job, "_containment", lambda *args, **kwargs: {"ok": False})
    result = job.run_job(spec(), dry_run=False, _runner=runtime, _attach=good_attach)
    assert not result["ok"] and not any("start" in call for call in runtime.calls)
    assert result["cleanup"]["ok"]


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


def test_post_run_pid_saturation_fails_closed_and_is_retained():
    runtime = Runtime()

    def saturated_attach(argv, *, timeout):
        _exit, output, _timed_out, _truncated = good_attach(argv, timeout=timeout)
        receipt = json.loads(output.removeprefix("ANVIL_GPU_TEST_RESULT "))
        receipt["cgroups_after"]["pids.events"] = "max 1"
        return 0, "ANVIL_GPU_TEST_RESULT " + json.dumps(receipt) + "\n", False, False

    result = job.run_job(spec(), dry_run=False, _runner=runtime, _attach=saturated_attach)
    assert not result["ok"] and result["runner"]["cgroups_after"]["pids.events"] == "max 1"
    assert result["cleanup"]["ok"]


def test_wrong_owner_never_removed():
    runtime = Runtime(wrong_owner=True)
    result = job.run_job(spec(), dry_run=False, _runner=runtime, _attach=good_attach)
    assert not result["ok"] and not runtime.removed


def _runner_contract(path, source, *, kind="standalone", markers=("done",)):
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    argv = ["python3", str(source)] if kind == "standalone" else ["python3", "-m", "pytest", "-q", str(source)]
    return {"gpu": {"uuid": GPU, "compute_capability": [12, 0]}, "limits": {"timeout_seconds": 20, "memory_mib": 1024, "cpus": 4}, "sources": [{"path": str(source), "sha256": digest}], "commands": [{"id": "test", "kind": kind, "argv": argv, "cwd": None, "source_paths": [str(source)], "completion_markers": list(markers if kind == "standalone" else ())}]}


def _runner_sitecustomize(mount_options="rw,nosuid,nodev,relatime", *, pids_max="1024", pids_current="1", pids_events="max 0"):
    return "import builtins, io\nreal = builtins.open\ndef open(path, *args, **kwargs):\n    values = {'/sys/fs/cgroup/memory.max': '1073741824', '/sys/fs/cgroup/memory.swap.max': '0', '/sys/fs/cgroup/cpu.max': '400000 100000', '/sys/fs/cgroup/pids.max': " + repr(pids_max) + ", '/sys/fs/cgroup/pids.current': " + repr(pids_current) + ", '/sys/fs/cgroup/pids.events': " + repr(pids_events) + ", '/proc/self/mountinfo': '34 25 0:30 / /tmp " + mount_options + " - tmpfs tmpfs rw,size=4194304k\\n'}\n    return io.StringIO(values[path]) if path in values else real(path, *args, **kwargs)\nbuiltins.open = open\n"


@pytest.mark.parametrize("kwargs", [
    {"pids_max": "512"},
    {"pids_current": "1025"},
    {"pids_events": "max 1"},
])
def test_runner_refuses_pid_contract_before_cuda(tmp_path, kwargs):
    (tmp_path / "torch.py").write_text("__version__ = 'fixture'\nclass cuda:\n    device_count = staticmethod(lambda: (_ for _ in ()).throw(RuntimeError('cuda touched')))\n")
    (tmp_path / "sitecustomize.py").write_text(_runner_sitecustomize(**kwargs))
    source = tmp_path / "direct.py"
    source.write_text("print('done')\n")
    env = dict(os.environ, PYTHONPATH=str(tmp_path), ANVIL_GPU_TEST_CONTRACT=json.dumps(_runner_contract(tmp_path, source)))
    receipt = json.loads(subprocess.run(["python3", "-c", job.RUNNER], env=env, capture_output=True, text=True, check=False).stdout.split(" ", 1)[1])
    assert receipt["commands"] == [] and receipt["error"] == "container cgroup limits differ from contract"
    assert receipt["cgroups_after"]["pids.max"] == kwargs.get("pids_max", "1024")


def test_runner_rejects_missing_marker_and_pytest_skip(tmp_path):
    (tmp_path / "torch.py").write_text("__version__ = 'fixture'\nclass props: uuid = 'GPU-00000000-0000-0000-0000-000000000001'\nclass cuda:\n    device_count = staticmethod(lambda: 1)\n    get_device_capability = staticmethod(lambda index: (12, 0))\n    get_device_properties = staticmethod(lambda index: props())\n")
    (tmp_path / "sitecustomize.py").write_text(_runner_sitecustomize())
    script = tmp_path / "direct.py"; script.write_text("print('wrong')\n")
    env = dict(os.environ, PYTHONPATH=str(tmp_path), ANVIL_GPU_TEST_CONTRACT=json.dumps(_runner_contract(tmp_path, script)))
    direct = subprocess.run(["python3", "-c", job.RUNNER], env=env, capture_output=True, text=True, check=False)
    assert '"ok": false' in direct.stdout and "completion_markers" in direct.stdout
    skipped = tmp_path / "skipped.py"; skipped.write_text("import pytest\n@pytest.mark.skip\ndef test_skip(): pass\n")
    env["ANVIL_GPU_TEST_CONTRACT"] = json.dumps(_runner_contract(tmp_path, skipped, kind="pytest"))
    result = subprocess.run(["python3", "-c", job.RUNNER], env=env, capture_output=True, text=True, check=False)
    assert '"ok": false' in result.stdout and "pytest_counts" in result.stdout
    failed = tmp_path / "failed.py"; failed.write_text("def test_fail(): assert False\n")
    env["ANVIL_GPU_TEST_CONTRACT"] = json.dumps(_runner_contract(tmp_path, failed, kind="pytest"))
    result = subprocess.run(["python3", "-c", job.RUNNER], env=env, capture_output=True, text=True, check=False)
    assert '"ok": false' in result.stdout and "failures" in result.stdout
    bad_hash = _runner_contract(tmp_path, script); bad_hash["sources"][0]["sha256"] = "0" * 64
    env["ANVIL_GPU_TEST_CONTRACT"] = json.dumps(bad_hash)
    result = subprocess.run(["python3", "-c", job.RUNNER], env=env, capture_output=True, text=True, check=False)
    assert '"ok": false' in result.stdout and not json.loads(result.stdout.split(" ", 1)[1])["commands"]


def test_runner_fails_when_junit_is_missing(tmp_path):
    (tmp_path / "torch.py").write_text("__version__ = 'fixture'\nclass props: uuid = 'GPU-00000000-0000-0000-0000-000000000001'\nclass cuda:\n    device_count = staticmethod(lambda: 1)\n    get_device_capability = staticmethod(lambda index: (12, 0))\n    get_device_properties = staticmethod(lambda index: props())\n")
    (tmp_path / "sitecustomize.py").write_text(_runner_sitecustomize() + "import os\nreal_exists = os.path.exists\nos.path.exists = lambda path: False if str(path).endswith('.xml') else real_exists(path)\n")
    source = tmp_path / "passing.py"; source.write_text("def test_ok(): assert True\n")
    env = dict(os.environ, PYTHONPATH=str(tmp_path), ANVIL_GPU_TEST_CONTRACT=json.dumps(_runner_contract(tmp_path, source, kind="pytest")))
    result = subprocess.run(["python3", "-c", job.RUNNER], env=env, capture_output=True, text=True, check=False)
    assert '"ok": false' in result.stdout and "junit_missing" in result.stdout


@pytest.mark.parametrize(("torch_uuid", "expected_ok"), [
    (GPU, True),
    (GPU.removeprefix("GPU-"), True),
    ("GPU-00000000-0000-0000-0000-000000000002", False),
])
def test_runner_canonicalizes_only_full_cuda_uuids_before_commands(tmp_path, torch_uuid, expected_ok):
    (tmp_path / "torch.py").write_text(
        "__version__ = 'fixture'\n"
        f"class props: uuid = {torch_uuid!r}\n"
        "class cuda:\n"
        "    device_count = staticmethod(lambda: 1)\n"
        "    get_device_capability = staticmethod(lambda index: (12, 0))\n"
        "    get_device_properties = staticmethod(lambda index: props())\n"
    )
    (tmp_path / "sitecustomize.py").write_text(_runner_sitecustomize())
    source = tmp_path / "direct.py"
    source.write_text("print('done')\n")
    env = dict(os.environ, PYTHONPATH=str(tmp_path), ANVIL_GPU_TEST_CONTRACT=json.dumps(_runner_contract(tmp_path, source)))
    result = subprocess.run(["python3", "-c", job.RUNNER], env=env, capture_output=True, text=True, check=False)
    receipt = json.loads(result.stdout.split(" ", 1)[1])
    assert receipt["ok"] is expected_ok
    assert bool(receipt["commands"]) is expected_ok


def test_executable_tmpfs_is_requested_and_noexec_runner_refuses_before_cuda(tmp_path):
    runtime = Runtime()
    result = job.run_job(spec(), dry_run=False, _runner=runtime, _attach=good_attach)
    command = next(call for call in runtime.calls if "create" in call)
    assert command[command.index("--tmpfs") + 1] == "/tmp:rw,nosuid,nodev,exec,size=4g"
    assert result["resource_containment"]["observed"]["ok"]
    (tmp_path / "torch.py").write_text("__version__ = 'fixture'\nclass cuda:\n    device_count = staticmethod(lambda: (_ for _ in ()).throw(RuntimeError('cuda touched')))\n")
    source = tmp_path / "direct.py"; source.write_text("print('done')\n")
    env = dict(os.environ, PYTHONPATH=str(tmp_path), ANVIL_GPU_TEST_CONTRACT=json.dumps(_runner_contract(tmp_path, source)))
    (tmp_path / "sitecustomize.py").write_text(_runner_sitecustomize("rw,nosuid,nodev,noexec"))
    receipt = json.loads(subprocess.run(["python3", "-c", job.RUNNER], env=env, capture_output=True, text=True, check=False).stdout.split(" ", 1)[1])
    assert receipt["tmp_mount"]["filesystem"] == "tmpfs" and "noexec" in receipt["tmp_mount"]["mount_options"]
    assert receipt["tmp_mount"]["executable"] is False
    assert receipt["commands"] == [] and receipt["error"] == "/tmp must be an executable tmpfs"


def test_runner_retains_bounded_pytest_collection_failure(tmp_path):
    (tmp_path / "torch.py").write_text("__version__ = 'fixture'\nclass props: uuid = 'GPU-00000000-0000-0000-0000-000000000001'\nclass cuda:\n    device_count = staticmethod(lambda: 1)\n    get_device_capability = staticmethod(lambda index: (12, 0))\n    get_device_properties = staticmethod(lambda index: props())\n")
    (tmp_path / "sitecustomize.py").write_text(_runner_sitecustomize())
    source = tmp_path / "broken.py"; source.write_text("def test_broken(:\n")
    env = dict(os.environ, PYTHONPATH=str(tmp_path), ANVIL_GPU_TEST_CONTRACT=json.dumps(_runner_contract(tmp_path, source, kind="pytest")))
    receipt = json.loads(subprocess.run(["python3", "-c", job.RUNNER], env=env, capture_output=True, text=True, check=False).stdout.split(" ", 1)[1])
    assert receipt["commands"] == []
    assert receipt["collection_failure"]["exit_code"] != 0
    assert "broken.py" in receipt["collection_failure"]["stdout"] + receipt["collection_failure"]["stderr"]
