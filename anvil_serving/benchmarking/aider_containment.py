"""Owned, offline containers for the pinned Aider test callback only."""

from __future__ import annotations

from contextlib import contextmanager
import ast
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import types
import stat
import subprocess
import uuid

from ..transports import _run_bounded_process
from .artifacts import atomic_write_json, evidence_file_reference
from .harness_runtime import resolve_container_binary
from .jobs import BenchmarkJobError, resolve_owned_run_path

AIDER_REVISION = "5dc9490bb35f9729ef2c95d00a19ccd30c26339c"

POLYGLOT_REVISION = "7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f"

SOURCE_HASHES = {
    "benchmark/benchmark.py": "ba350b7b3ebcc9da6f588c0724dade552791b3c5edd9f16641eadb5931de9c30",
    "benchmark/npm-test.sh": "4ad9c732f05da5cad4608545454b574c40a97aefb45ead784a31f398b5dad687",
    "benchmark/cpp-test.sh": "51184a5f17c90e5cfeec37bb7f7cb37119b9fe7c295621f36bdcef5094713eca",
    "aider/linter.py": "b798f0599d5dbc8cd1b628359134a3ce5eaed4b45fc347b5f6ac0d21e9f68c08",
    "aider/models.py": "37784de662eeafacf17cbc4eb656246aadee20477b8ad43768caf7d373ab6e0d",
    "aider/coders/base_coder.py": "04a7b38c4f1d2b1684ac958f40bcd81a28b00139eae88afed59d2c970bef1ee1",
    "aider/coders/wholefile_coder.py": "b8fa0d35e4a26b5c2b978a223e3c2762bb557a3ee15dad8e7a91ac242f6dd52f",
}

LANGUAGES = frozenset({"cpp", "go", "java", "javascript", "python", "rust"})

def validate_task_id(value):
    if (not isinstance(value, str) or len(value) > 256 or "\\" in value
            or not re.fullmatch(r"[a-z][a-z0-9-]*/exercises/practice/[a-z0-9][a-z0-9_-]*", value)
            or PurePosixPath(value).parts[0] not in LANGUAGES):
        raise ValueError("invalid source-relative Aider task ID")
    return value

def task_snapshot(root, task_id):
    root = Path(root).resolve(strict=True)
    task = root / validate_task_id(task_id)
    if not task.resolve(strict=True).is_relative_to(root):
        raise ValueError("Aider task escaped its source")
    for parent in [task, *task.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError("Aider task traverses a symlink")
    files = {}
    for path in sorted(task.rglob("*")):
        if path.is_symlink():
            raise ValueError("Aider task contains a symlink")
        if path.is_file():
            files[path.relative_to(task).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    config = json.loads((task / ".meta/config.json").read_text())
    for names in config.get("files", {}).values():
        if not isinstance(names, list):
            raise ValueError("invalid Aider task config")
        for name in names:
            if (not isinstance(name, str) or "\\" in name or PurePosixPath(name).is_absolute()
                    or ".." in PurePosixPath(name).parts or name not in files):
                raise ValueError("unsafe or missing Aider task config file")
    if not config.get("files", {}).get("test") or not config.get("files", {}).get("solution"):
        raise ValueError("Aider task lacks tests or solution")
    if ".docs/instructions.md" not in files:
        raise ValueError("Aider task lacks instructions")
    return files

def load_test_callback(root):
    """Load actual pinned oracle code without importing Aider or ambient dotenv."""
    path = Path(root) / "benchmark/benchmark.py"
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != SOURCE_HASHES["benchmark/benchmark.py"]:
        raise RuntimeError("pinned Aider test callback hash changed")
    tree = ast.parse(source, filename=str(path))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"run_unit_tests", "cleanup_test_output"}]
    if len(functions) != 2:
        raise RuntimeError("pinned Aider test callback missing")
    namespace = dict(Path=Path, os=os, shutil=shutil, re=re, subprocess=subprocess, dump=lambda *_: None)
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["run_unit_tests"]

def contained_test_callback(callback, run_test):
    """Replace only this function's subprocess reference, never global subprocess."""
    namespace = dict(callback.__globals__)
    namespace["subprocess"] = types.SimpleNamespace(
        run=run_test, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT,
        TimeoutExpired=subprocess.TimeoutExpired,
    )
    return types.FunctionType(callback.__code__, namespace, callback.__name__,
                              callback.__defaults__, callback.__closure__)


MAX_OUTPUT_BYTES = 1024 * 1024
_LABEL = "anvil.aider."
_TMPFS = "rw,nosuid,nodev,noexec,size=1g,nr_inodes=16384,mode=1777"
_TASK_TMPFS = "rw,exec,nosuid,nodev,size=4g,nr_inodes=65536,mode=1777"
_ENV_KEYS = {"PATH", "HOME", "LANG", "LC_ALL", "JAVA_HOME", "GOPATH", "CARGO_HOME",
             "RUSTUP_HOME", "GRADLE_USER_HOME", "PYTHONUNBUFFERED", "DEBIAN_FRONTEND", "NODE_PATH"}


def _require(condition, message):
    if not condition:
        raise BenchmarkJobError("aider_containment_invalid", message)


def _copy_input(task_name):
    # CMake and other upstream runners derive exercise identity from the basename.
    destination = shlex.quote("/task/" + task_name)
    return f"mkdir -- {destination} && cp -R -- /input/. {destination}/ && cd -- {destination} || exit\n"


def _default_runner(argv, cwd, timeout):
    # Docker receives no endpoint credentials or inherited DOCKER_HOST overrides.
    return _run_bounded_process(argv, cwd=cwd, timeout=timeout,
                                max_output_bytes=MAX_OUTPUT_BYTES,
                                env={"PATH": os.defpath})


def _text(value):
    raw = value if isinstance(value, bytes) else str(value or "").encode()
    _require(len(raw) <= MAX_OUTPUT_BYTES, "container output exceeded its bound")
    return raw.decode("utf-8", "replace")


def _call(runner, binary, *args, timeout=15):
    result = runner((binary, *args), None, timeout)
    _text(result.stdout)
    _text(result.stderr)
    return result


def _inspect(runner, binary, target, *, image=False, missing=False):
    args = ("image", "inspect", target) if image else ("container", "inspect", target)
    result = _call(runner, binary, *args)
    if missing and result.returncode and "No such container" in _text(result.stderr):
        return None
    _require(result.returncode == 0, "container inspection failed")
    try:
        rows = json.loads(_text(result.stdout))
    except (ValueError, TypeError) as exc:
        raise BenchmarkJobError("aider_containment_invalid", "invalid inspection JSON") from exc
    _require(isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict),
             "container inspection must identify exactly one object")
    return rows[0]


def _no_symlinks(path):
    _require(path.is_absolute() and ".." not in path.parts, "path must be absolute without traversal")
    _require(not any(item.is_symlink() for item in (path, *path.parents)), "symlink path rejected")


def _registry(run_path, ownership_id, run_id, *, create=False):
    root = Path(run_path)
    _no_symlinks(root)
    expected = resolve_owned_run_path(str(root.parent.parent), ownership_id=ownership_id, run_id=run_id)
    _require(str(root) == expected and root.is_dir(), "run path does not match its owner and run")
    registry = root / "aider" / "containers"
    _no_symlinks(registry)
    if create:
        registry.mkdir(parents=True, exist_ok=True)
    return registry


@contextmanager
def _locked(record_path):
    # One lock per container avoids lost updates without serializing parallel tasks.
    import fcntl
    lock_path = record_path.with_suffix(".lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def _labels(record):
    return {_LABEL + key: record[key] for key in ("ownership_id", "run_id", "token")}


def _environment(values):
    _require(isinstance(values, list), "invalid container environment")
    result = {}
    for value in values:
        _require(isinstance(value, str) and "=" in value, "invalid container environment")
        key, item = value.split("=", 1)
        _require(key in _ENV_KEYS and key not in result,
                 "container environment contains unsupported or duplicate keys")
        result[key] = item
    return result


def _owned(observed, record):
    identity = observed.get("Id", "")
    _require(isinstance(identity, str) and re.fullmatch(r"[0-9a-f]{64}", identity), "invalid container ID")
    _require(record.get("container_id") in (None, identity), "container ID drift")
    _require(observed.get("Name") == "/" + record["name"] and observed.get("Image") == record["image_id"],
             "container image or name drift")
    labels = observed.get("Config", {}).get("Labels") or {}
    _require(all(labels.get(key) == value for key, value in _labels(record).items()), "container ownership mismatch")
    return identity


def _isolated(observed, record):
    _owned(observed, record)
    config = observed.get("Config", {})
    host = observed.get("HostConfig", {})
    _require(config.get("User") == record["user"] and config.get("WorkingDir") == "/task"
             and config.get("Entrypoint") == ["/bin/sh"] and config.get("Cmd") == record["command_args"]
             and _environment(config.get("Env")) == _environment(record["environment"]),
             "container execution configuration drift")
    required = {"NetworkMode": "none", "ReadonlyRootfs": True, "Privileged": False,
                "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges:true"],
                "Memory": 8 * 1024**3, "MemorySwap": 8 * 1024**3,
                "NanoCpus": 4 * 10**9, "PidsLimit": 512, "Tmpfs": {"/tmp": _TMPFS, "/task": _TASK_TMPFS},
                "PidMode": "", "IpcMode": "private", "UTSMode": "", "CgroupnsMode": "private"}
    _require(all(host.get(key) == value for key, value in required.items()), "container isolation/resource drift")
    _require(host.get("RestartPolicy", {}).get("Name") == "no"
             and host.get("LogConfig", {}).get("Type") == "none"
             and host.get("Runtime") in ("", "runc"), "container runtime policy drift")
    for key in ("Devices", "DeviceRequests", "DeviceCgroupRules", "CapAdd", "Binds", "VolumesFrom",
                "PortBindings", "Links", "ExtraHosts", "GroupAdd", "Sysctls"):
        _require(host.get(key) in (None, [], {}), "unexpected container host option: " + key)
    mounts = observed.get("Mounts")
    _require(isinstance(mounts, list), "missing container mount inspection")
    binds = [m for m in mounts if m.get("Type") != "tmpfs"]
    _require(len(binds) == 1 and binds[0].get("Type") == "bind"
             and binds[0].get("Source") == record["task_dir"] and binds[0].get("Destination") == "/input"
             and binds[0].get("RW") is False and binds[0].get("Propagation") == "rprivate",
             "container must have only its exact read-only input bind")
    _require(all(m.get("Destination") in {"/tmp", "/task"} for m in mounts if m.get("Type") == "tmpfs"),
             "unexpected tmpfs mount")
    _require(observed.get("State", {}).get("Status") == "created", "container already started")


def _remove(record_path, record, runner):
    binary = record["container_binary"]
    observed = _inspect(runner, binary, record.get("container_id") or record["name"], missing=True)
    _require(observed is not None or record.get("container_id") is not None
             or record["state"] == "creation_failed",
             "uncertain creation remains recoverable until its container is observed")
    if observed is not None:
        identity = _owned(observed, record)
        record["container_id"] = identity
        atomic_write_json(record_path, record)
        # Immutable ID plus a fresh ownership check precedes the destructive command.
        _owned(_inspect(runner, binary, identity), record)
        result = _call(runner, binary, "container", "rm", "--force", identity)
        _require(result.returncode == 0, "owned container removal failed")
        _require(_inspect(runner, binary, identity, missing=True) is None, "container removal unverified")
    record["state"] = "removed"
    atomic_write_json(record_path, record)


def run_contained_test(command, *, task_dir, run_path, ownership_id, run_id, image_id,
                       timeout=180, container_binary=None, runner=_default_runner):
    """Copy read-only input into bounded scratch; never export candidate-written files."""
    _require(os.name == "posix", "Aider containers require a POSIX worker")
    _require(isinstance(timeout, (int, float)) and not isinstance(timeout, bool)
             and math.isfinite(timeout) and 0 < timeout <= 180, "invalid Aider test timeout")
    _require(isinstance(image_id, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", image_id),
             "test image must be an immutable image ID")
    registry = _registry(run_path, ownership_id, run_id, create=True)
    for previous in registry.glob("*.json"):
        _no_symlinks(previous)
        _require(previous.stat().st_size <= MAX_OUTPUT_BYTES, "container record exceeds its bound")
        prior = json.loads(previous.read_text())
        _require(prior.get("state") == "removed" or not prior.get("cleanup_error"),
                 "previous container cleanup is unresolved")
    task = Path(task_dir)
    _no_symlinks(task)
    _require(task.is_dir() and task != Path(run_path) and task.is_relative_to(Path(run_path)),
             "task must be inside its owned run")
    _require(not registry.is_relative_to(task) and not task.is_relative_to(registry),
             "task bind cannot expose container ownership records")
    for child in task.rglob("*"):
        info = child.lstat()
        _require(stat.S_ISDIR(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1),
                 "task input must contain only directories and single-linked regular files")
    _require("," not in str(task), "task path cannot contain mount delimiters")
    if isinstance(command, str):
        _require(bool(command) and "\x00" not in command, "invalid test command")
        command_args = ["-c", "{\n" + _copy_input(task.name) + command + "\n} 2>&1"]
    else:
        _require(isinstance(command, (list, tuple)) and bool(command)
                 and all(isinstance(v, str) and v and "\x00" not in v for v in command), "invalid test argv")
        command_args = ["-c", "{\n" + _copy_input(task.name) + 'exec "$@"\n} 2>&1', "sh", *command]
    _require(sum(map(len, command_args)) <= 32768, "test command exceeded its bound")
    binary = container_binary or resolve_container_binary()
    _require(isinstance(binary, str) and bool(binary), "Docker is unavailable")
    image = _inspect(runner, binary, image_id, image=True)
    _require(image.get("Id") == image_id, "test image ID drift")
    environment = image.get("Config", {}).get("Env") or []
    _environment(environment)
    environment = ["HOME=/tmp" if v.startswith("HOME=") else v for v in environment]
    if "HOME=/tmp" not in environment:
        environment.append("HOME=/tmp")
    token = uuid.uuid4().hex
    record = {"schema": "anvil-serving.aider-container/v1", "ownership_id": ownership_id,
              "run_id": run_id, "token": token, "name": "anvil-aider-" + token,
              "image_id": image_id, "container_binary": binary, "container_id": None,
              "task_dir": str(task), "user": f"{os.getuid()}:{os.getgid()}",
              "command": command, "command_args": command_args, "environment": environment, "state": "planned"}
    record_path = registry / (token + ".json")
    argv = ["create", "--name", record["name"], "--pull=never", "--network=none", "--read-only",
            "--tmpfs", "/tmp:" + _TMPFS, "--tmpfs", "/task:" + _TASK_TMPFS, "--memory=8g", "--memory-swap=8g", "--cpus=4", "--pids-limit=512",
            "--security-opt=no-new-privileges:true", "--cap-drop=ALL", "--user", record["user"],
            "--workdir=/task", "--restart=no", "--log-driver=none", "--no-healthcheck", "--ipc=private",
            "--cgroupns=private", "--entrypoint=/bin/sh", "--env=HOME=/tmp",
            "--mount", f"type=bind,src={task},dst=/input,readonly,bind-propagation=rprivate"]
    for key, value in _labels(record).items():
        argv.extend(("--label", key + "=" + value))
    argv.extend((image_id, *command_args))
    with _locked(record_path):
        atomic_write_json(record_path, record)  # durable intent before Docker can create anything
        try:
            created = _call(runner, binary, *argv)
            if created.returncode != 0:
                record["state"] = "creation_failed"
            _require(created.returncode == 0 and re.fullmatch(r"[0-9a-f]{64}", _text(created.stdout).strip()),
                     "container creation failed")
            record["container_id"] = _text(created.stdout).strip()
            record["state"] = "created"
            atomic_write_json(record_path, record)
            _isolated(_inspect(runner, binary, record["container_id"]), record)
            record["state"] = "verified"
            atomic_write_json(record_path, record)
            result = _call(runner, binary, "start", "--attach", record["container_id"], timeout=timeout)
            observed = _inspect(runner, binary, record["container_id"])
            _owned(observed, record)
            state = observed.get("State", {})
            _require(state.get("Status") == "exited" and isinstance(state.get("ExitCode"), int),
                     "container test completion unverified")
            record["returncode"] = state["ExitCode"]
            _require(result.returncode == state["ExitCode"], "container attach and exit status disagree")
            return subprocess.CompletedProcess(command, state["ExitCode"], _text(result.stdout) + _text(result.stderr), None)
        except BaseException as exc:
            record["failure"] = type(exc).__name__
            raise
        finally:
            try:
                _remove(record_path, record, runner)
            except Exception as cleanup_error:
                record["cleanup_error"] = type(cleanup_error).__name__
                atomic_write_json(record_path, record)
                # A cleanup timeout is not a test timeout. In particular, callers
                # may continue after a test timeout, so unresolved cleanup must
                # propagate distinctly even when the test also timed out.
                if isinstance(cleanup_error, subprocess.TimeoutExpired) or record.get("failure") == "TimeoutExpired":
                    raise BenchmarkJobError("aider_cleanup_unresolved", "owned container cleanup is unresolved") from cleanup_error
                # A test must never look successful while its generated code remains live.
                if "failure" not in record:
                    raise


def cleanup_aider_containers(*, run_path, ownership_id, run_id, runner=_default_runner):
    """Recover recorded containers after the owning worker has stopped."""
    registry = _registry(run_path, ownership_id, run_id)
    receipt = {"schema": "anvil-serving.aider-container-cleanup/v1", "ownership_id": ownership_id,
               "run_id": run_id, "status": "completed", "removed": [], "errors": []}
    if not registry.exists():
        return receipt
    for path in sorted(registry.glob("*.json")):
        try:
            _no_symlinks(path)
            with _locked(path):
                _require(path.stat().st_size <= MAX_OUTPUT_BYTES, "container record exceeds its bound")
                record = json.loads(path.read_text())
                _require(record["ownership_id"] == ownership_id and record["run_id"] == run_id
                         and record["token"] == path.stem and re.fullmatch(r"[0-9a-f]{32}", path.stem)
                         and record["name"] == "anvil-aider-" + path.stem,
                         "container record ownership mismatch")
                if record["state"] != "removed":
                    _remove(path, record, runner)
                    receipt["removed"].append(record["container_id"] or record["name"])
        except Exception as exc:
            receipt["errors"].append({"record": path.name, "error": type(exc).__name__})
    if receipt["errors"]:
        receipt["status"] = "incomplete"
    return receipt


def aider_custody_snapshot(*, run_path, ownership_id, run_id):
    """Read and bind all removed container records without invoking the runtime."""
    registry = _registry(run_path, ownership_id, run_id)
    if not registry.exists():
        _require(not os.path.lexists(os.path.join(run_path, "aider")), "container registry is missing")
        return []
    entries = []
    with os.scandir(registry) as iterator:
        for item in iterator:
            if item.name.endswith(".json"):
                entries.append(registry / item.name)
                _require(len(entries) <= 512, "container record count exceeds bound")
    entries.sort()
    records = []
    for path in entries:
        _no_symlinks(path)
        _require(path.is_file() and path.stat().st_size <= MAX_OUTPUT_BYTES, "invalid container record")
        with open(path, "rb", opener=lambda p, f: os.open(p, f | os.O_NOFOLLOW | os.O_NONBLOCK)) as stream:
            _require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "nonregular container record")
            raw = stream.read(MAX_OUTPUT_BYTES + 1)
        _require(len(raw) <= MAX_OUTPUT_BYTES, "container record exceeds bound")
        record = json.loads(raw)
        _require(record.get("schema") == "anvil-serving.aider-container/v1"
                 and record.get("ownership_id") == ownership_id and record.get("run_id") == run_id
                 and record.get("token") == path.stem and re.fullmatch(r"[0-9a-f]{32}", path.stem)
                 and record.get("name") == "anvil-aider-" + path.stem and record.get("state") == "removed",
                 "container record custody mismatch")
        reference = evidence_file_reference(str(path), root=run_path)
        _require(reference["bytes"] == len(raw) and reference["sha256"] == hashlib.sha256(raw).hexdigest(),
                 "container record changed during verification")
        records.append(reference)
    return records
