"""Download-free, disposable container lane for the real Connect browser fixtures."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import uuid


_DEVICE_TESTS = ("container-gated CLI device login reaches only its declared API resource",)

def _load_runner(path: Path):
    spec = importlib.util.spec_from_file_location("connect_qualification_runner", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _entry() -> None:
    """Container-only execution; stdout contains closed metadata, never reports."""
    runner = _load_runner(Path("/source/qualification.py"))
    tests = _DEVICE_TESTS if sys.argv[3] == "device" else runner._TESTS
    cases = []
    try:
        shutil.copytree("/source/connect", "/work/connect")
        Path("/work/connect/node_modules").symlink_to("/opt/connect-browser/node_modules")
        shutil.copytree("/opt/go-modules", "/work/go-modules")
        chromium = list(Path("/ms-playwright").glob("chromium-*/chrome-linux*/chrome"))
        if len(chromium) != 1:
            raise ValueError("chromium pin unavailable")
        tools = {name: Path("/opt/connect-tools") / name for name in ("caddy", "authelia", "wstunnel")}
        tools.update(go=Path("/opt/go/bin/go"), node=Path("/usr/bin/node"), chromium=chromium[0], certutil=Path("/usr/bin/certutil"))
        node = shutil.which("node")
        if not node:
            raise ValueError("node unavailable")
        tools["node"] = Path(node)
        config = runner.QualificationConfig(Path("/work"), Path("/work"), Path("/opt/connect-browser"), Path("/work/go-modules"), tools)
        fixture = Path(tempfile.mkdtemp(prefix="acq-", dir="/tmp"))
        environment = runner._environment(config, Path("/work"), fixture_tmp=fixture)
        if sys.argv[3] == "device":
            environment["ANVIL_CONNECT_BROWSER_DEVICE_FIXTURE"] = "1"
        deadline = time.monotonic() + int(sys.argv[2])
        escalated = False
        for index, name in enumerate(tests):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                status, duration, forced = "runner-timeout", 0.0, False
            else:
                status, duration, forced = runner._run_test(
                    [str(tools["node"]), "/work/connect/node_modules/playwright/cli.js", "test", "test/browser_edge.spec.mjs", "--reporter=json", "--grep", re.escape(name) + "$"],
                    cwd=Path("/work/connect"), env=environment, timeout=remaining,
                    expected_name=name, supervisor=Path("/source/_qualification_supervisor.py"),
                )
            if status != "passed":
                for event_file, counter, label in (("pids.events", "max", "pids-limit"), ("memory.events", "oom_kill", "memory-limit")):
                    try:
                        events = dict(line.split() for line in Path("/sys/fs/cgroup", event_file).read_text().splitlines())
                        if int(events.get(counter, "0")) > 0:
                            status = "runner-failed-" + label
                    except (OSError, ValueError):
                        pass
            escalated |= forced
            item = {"name": name, "status": status, "duration_seconds": round(duration, 3)}
            cases.append(item)
            if status != "passed":
                cases.extend({"name": later, "status": "not-run", "duration_seconds": 0.0} for later in tests[index + 1:])
                break
        print(json.dumps({"tests": cases, "escalated": escalated}), flush=True)
    except Exception:
        # All details are volatile. The host records an unknown test count on
        # preparation failure instead of serializing exception/report content.
        print(json.dumps({"error_code": "container-execution-failed"}), flush=True)


def _attached(args: list[str], environment: dict[str, str], timeout: int) -> bytes:
    from .qualification import _error
    from .qualification_container import _stop_docker
    process = None
    selector = selectors.DefaultSelector()
    try:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=environment, start_new_session=True)
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        output = bytearray()
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            for key, _ in selector.select(min(0.05, remaining)):
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                elif len(output) + len(chunk) > 8192:
                    raise ValueError
                else:
                    output.extend(chunk)
        if process.wait(timeout=2) != 0:
            raise ValueError
        return bytes(output)
    except KeyboardInterrupt:
        raise _error("runner-interrupted", "container qualification interrupted", execution_started=True, stage="execution") from None
    except (OSError, ValueError, subprocess.SubprocessError):
        raise _error("runner-failed", "container qualification execution failed", execution_started=True, stage="execution") from None
    finally:
        selector.close()
        _stop_docker(process)
        if process is not None and process.stdout is not None:
            process.stdout.close()


def _cases(raw: bytes, names: tuple[str, ...]) -> tuple[list[dict], bool]:
    from .qualification import _error
    try:
        value = json.loads(raw)
        if set(value) != {"tests", "escalated"} or type(value["escalated"]) is not bool:
            raise ValueError
        cases = value["tests"]
        if not isinstance(cases, list) or len(cases) != len(names):
            raise ValueError
        for case, name in zip(cases, names):
            if set(case) != {"name", "status", "duration_seconds"} or case["name"] != name:
                raise ValueError
            status = case["status"]
            if not isinstance(status, str) or not re.fullmatch(r"passed|skipped|not-run|runner-timeout|runner-interrupted|runner-failed(?:-(?:pids-limit|memory-limit|build|fixture-startup|browser-launch-cert|browser-connection|browser-dns|browser-navigation|browser-assertion|report-parsing|timeout|interrupted|supervisor)(?:@browser_(?:edge|runtime)_fixture_test\.go:[1-9][0-9]{0,4})?)?", status):
                raise ValueError
            if type(case["duration_seconds"]) not in (int, float) or not 0 <= case["duration_seconds"] <= 1000:
                raise ValueError
        return cases, value["escalated"]
    except (ValueError, TypeError, KeyError):
        raise _error("runner-failed", "container qualification result is unavailable", execution_started=True, stage="execution") from None


def qualify(config_path=None, *, lane="container-baseline") -> dict:
    from . import qualification as runner
    from . import qualification_container as image
    if lane not in {"container-baseline", "device"}:
        raise runner._error("config-invalid", "container qualification lane is unsupported")
    names = _DEVICE_TESTS if lane == "device" else runner._TESTS
    if os.geteuid() == 0 or os.getegid() == 0:
        raise runner._error("config-invalid", "container qualification requires a non-root user and group")
    path = Path(config_path) if config_path else Path.home() / ".config/anvil-connect/qualification.toml"
    config = runner._read_config(path)
    runner._private_directory(config.artifact_root, create=True)
    cpus = sorted(os.sched_getaffinity(0))[-2:]
    if not cpus:
        raise runner._error("tool-invalid", "container qualification CPU affinity is unavailable")
    metadata = runner._source_metadata(config.source_root)
    digest = image._input_digest(image._context_inputs(config))
    image_id = image._cached_receipt(config.artifact_root, digest)
    if image_id is None:
        raise runner._error("tool-invalid", "prepare the pinned qualification container first")
    run_dir = config.artifact_root / ("run-" + uuid.uuid4().hex)
    run_dir.mkdir(mode=0o700)
    name = "anvil-connect-qualification-" + uuid.uuid4().hex
    result = None
    cleaned = False
    with tempfile.TemporaryDirectory(prefix="container-stage-", dir=run_dir) as temporary, tempfile.TemporaryDirectory(prefix="docker-config-", dir=run_dir) as docker_config:
        stage = Path(temporary)
        if ":" in str(stage):
            raise runner._error("config-invalid", "container staging path is unsupported")
        env = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "DOCKER_CONFIG": docker_config}
        tag = "anvil-connect-qualification:sha256-" + digest
        if image._image(tag, digest, env=env) != image_id:
            raise runner._error("tool-invalid", "prepared qualification image no longer matches its receipt")
        files = {}
        for relative in runner._tracked_connect_files(config.source_root):
            data = image._regular_under(config.source_root, relative.as_posix())
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            files[relative.as_posix()] = hashlib.sha256(data).hexdigest()
        for filename in ("qualification.py", "_qualification_supervisor.py", "qualification_container_run.py"):
            data = image._regular_under(config.source_root, "anvil_serving/connect/" + filename)
            (stage / filename).write_bytes(data)
            files[filename] = hashlib.sha256(data).hexdigest()
        metadata["staged_files_sha256"] = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        args = [image._DOCKER, "--host", image._DOCKER_HOST, "run", "--pull", "never", "--init", "--name", name,
                "--network", "none", "--read-only", "--log-driver", "none", "--user", f"{os.geteuid()}:{os.getegid()}",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--cpus", "2", "--cpuset-cpus", ",".join(map(str, cpus)), "--memory", "4g", "--memory-swap", "4g",
                "--pids-limit", "256", "--shm-size", "256m", "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=1g,mode=1777",
                "--tmpfs", "/work:rw,exec,nosuid,nodev,size=2g,mode=1777", "--sysctl", "net.ipv4.ip_unprivileged_port_start=0",
                "--volume", str(stage) + ":/source:ro"]
        for host in ("dash", "auth", "api", "control", "tunnel"):
            args.extend(["--add-host", host + ".example.test:127.0.0.1"])
        args.extend([image_id, "python3", "/source/qualification_container_run.py", "--entry", str(config.timeout_seconds), lane])
        try:
            result = _cases(_attached(args, env, config.timeout_seconds + 45), names)
        finally:
            image._docker(["rm", "--force", name], env=env, capture=True)
            code, remaining = image._docker(["container", "ls", "--all", "--filter", "name=^/" + name + "$", "--format", "{{.ID}}"], env=env, capture=True)
            cleaned = code == 0 and not remaining.strip()
            if not cleaned:
                raise runner._error("runner-failed", "qualification container cleanup failed", execution_started=True, stage="cleanup", case_counts=runner._counts(result[0]) if result else None)
    cases, escalated = result
    counts = runner._counts(cases)
    passed = cleaned and not escalated and all(case["status"] == "passed" for case in cases)
    error = None if passed else next((case["status"] for case in cases if case["status"] != "passed"), "runner-failed")
    evidence = {"schema": "anvil-connect.qualification/v1", "lane": lane, "source": metadata,
                "image_id": image_id, "input_digest": digest, "tests": cases, "counts": counts,
                "network": {"network_isolation": "docker-none", "published_ports": False},
                "cleanup": {"container_removed": cleaned, "private_stage_removed": not stage.exists(), "escalation_required": escalated},
                "ok": passed, "state": "passed" if passed else "failed", "error_code": error}
    runner._junit(run_dir / "junit.xml", cases)
    for filename, value in (("evidence.json", evidence), ("result.json", {key: evidence[key] for key in ("schema", "lane", "ok", "state", "error_code", "counts")})):
        (run_dir / filename).write_text(json.dumps(value, sort_keys=True) + "\n")
        (run_dir / filename).chmod(0o600)
    (run_dir / "SHA256SUMS").write_text("".join(f"{runner._sha256(run_dir / filename)}  {filename}\n" for filename in ("evidence.json", "junit.xml", "result.json")))
    (run_dir / "SHA256SUMS").chmod(0o600)
    return {"schema": evidence["schema"], "ok": passed, "state": evidence["state"], "error_code": error, "counts": counts, "artifact_dir": str(run_dir)}


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "--entry" and sys.argv[3] in {"container-baseline", "device"}:
    _entry()
