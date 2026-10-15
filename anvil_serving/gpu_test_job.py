"""One-GPU, digest-pinned numerical test job for private qualification evidence."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import threading
import time
import uuid

from .benchmarking.artifacts import atomic_write_json
from .guard import confirmation_authorized
from . import nccl_probe
from . import recipe_memory


SCHEMA = "anvil-serving.gpu-test-job/v1"
LABEL = "io.anvil-serving.gpu-test-job"
MAX_LOG_BYTES = 256 * 1024
COMMAND_LOG_BYTES = 12 * 1024
IMAGE = re.compile(r"(?:sha256:[0-9a-f]{64}|[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64})")
SHA256 = re.compile(r"[0-9a-f]{64}")
IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")
ENVIRONMENT = {
    "EXL3_KERNEL_ARCH": "120", "EXL3_RDMA_AR": "0",
    "VLLM_PLUGINS": "exl3_moe", "EXL3_INT8_GEMV": "0",
}


def _local(argv, *, timeout=30, _runner=subprocess.run):
    return nccl_probe._run(argv, timeout=timeout, _runner=_runner)


def validate_spec(value):
    """Return a strict, JSON-safe job contract without touching Docker or GPUs."""
    if not isinstance(value, dict) or set(value) != {"schema", "image", "gpu", "limits", "environment", "sources", "commands"} or value.get("schema") != SCHEMA:
        raise ValueError("spec must use schema " + SCHEMA)
    image = value.get("image")
    gpu = value.get("gpu")
    limits = value.get("limits")
    environment = value.get("environment")
    sources = value.get("sources")
    commands = value.get("commands")
    if not isinstance(image, str) or not IMAGE.fullmatch(image):
        raise ValueError("image must be a full sha256 image ID or repository@sha256 digest")
    if not isinstance(gpu, dict) or set(gpu) != {"uuid", "compute_capability", "idle_baseline_mib"} or not isinstance(gpu.get("uuid"), str) or not nccl_probe.GPU_UUID.fullmatch(gpu["uuid"]):
        raise ValueError("gpu.uuid must be one full GPU UUID")
    capability = gpu.get("compute_capability")
    if (not isinstance(capability, list) or len(capability) != 2 or
            any(type(item) is not int or item < 0 for item in capability)):
        raise ValueError("gpu.compute_capability must be [major, minor]")
    if type(gpu["idle_baseline_mib"]) is not int or not 0 <= gpu["idle_baseline_mib"] <= 512:
        raise ValueError("gpu.idle_baseline_mib must be 0..512")
    if not isinstance(limits, dict) or set(limits) != {"cpus", "memory_mib", "memory_swap_mib", "timeout_seconds"}:
        raise ValueError("limits must be an object")
    cpus, memory, swap, timeout = (limits.get(key) for key in ("cpus", "memory_mib", "memory_swap_mib", "timeout_seconds"))
    if type(cpus) is not int or not 1 <= cpus <= 8:
        raise ValueError("limits.cpus must be 1..8")
    if type(memory) is not int or not 1 <= memory <= 24576:
        raise ValueError("limits.memory_mib must be 1..24576")
    if swap != memory:
        raise ValueError("limits.memory_swap_mib must equal memory_mib (no effective swap)")
    if type(timeout) is not int or not 1 <= timeout <= 2700:
        raise ValueError("limits.timeout_seconds must be 1..2700")
    if environment != ENVIRONMENT:
        raise ValueError("environment must contain exactly the reviewed nonsecret environment")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 64:
        raise ValueError("sources must be a non-empty list")
    source_hashes = {}
    for row in sources:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise ValueError("each source requires only path and sha256")
        path, digest = row["path"], row["sha256"]
        if not isinstance(path, str) or not path.startswith("/") or not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise ValueError("source paths must be absolute and source hashes must be sha256")
        if path in source_hashes:
            raise ValueError("source paths must be unique")
        source_hashes[path] = digest
    if not isinstance(commands, list) or not 1 <= len(commands) <= 8:
        raise ValueError("commands must contain 1..8 sequential jobs")
    seen = set()
    normalized = []
    for command in commands:
        if not isinstance(command, dict) or set(command) != {"id", "kind", "argv", "cwd", "source_paths", "completion_markers"}:
            raise ValueError("command has unsupported or missing fields")
        ident, kind, argv, cwd, paths, markers = (command[key] for key in ("id", "kind", "argv", "cwd", "source_paths", "completion_markers"))
        if not isinstance(ident, str) or not IDENT.fullmatch(ident) or ident in seen:
            raise ValueError("command ids must be unique safe identifiers")
        seen.add(ident)
        if kind not in {"pytest", "standalone"} or not isinstance(argv, list) or not argv or any(not isinstance(item, str) or not item for item in argv):
            raise ValueError("command kind or argv is invalid")
        if cwd is not None and (not isinstance(cwd, str) or not cwd.startswith("/")):
            raise ValueError("command cwd must be absolute or null")
        if not isinstance(paths, list) or not paths or any(path not in source_hashes for path in paths):
            raise ValueError("command source_paths must name declared sources")
        if kind == "pytest":
            if argv[:4] != ["python3", "-m", "pytest", "-q"] or markers:
                raise ValueError("pytest commands must use python3 -m pytest -q with no completion markers")
            requested, deselected, index = [], [], 4
            while index < len(argv):
                item = argv[index]
                if item == "--deselect" and index + 1 < len(argv):
                    node = argv[index + 1]
                    source, marker, node_id = node.partition("::")
                    resolved = os.path.normpath(os.path.join(cwd, source)) if cwd else source
                    if not marker or resolved not in paths or not re.fullmatch(r"[A-Za-z0-9_./:[\]-]+", node_id):
                        raise ValueError("pytest --deselect must name a reviewed source node")
                    deselected.append(node); index += 2; continue
                resolved = os.path.normpath(os.path.join(cwd, item)) if cwd else item
                if resolved in paths:
                    requested.append(resolved); index += 1; continue
                raise ValueError("pytest accepts only reviewed source paths and --deselect nodes")
            if not requested or set(requested) != set(paths):
                raise ValueError("pytest source_paths must exactly match requested module files")
            if len({Path(path).name for path in paths}) != len(paths):
                raise ValueError("pytest source_paths must have unique basenames for collection proof")
        else:
            if len(argv) != 2 or argv[0] != "python3" or argv[1] not in paths or not markers or any(not isinstance(item, str) or not item for item in markers):
                raise ValueError("standalone commands need python3 script, source path, and completion markers")
        normalized.append(command)
    return {"schema": SCHEMA, "image": image, "gpu": gpu, "limits": limits,
            "environment": environment, "sources": sources, "commands": normalized}


RUNNER = r'''
import hashlib, json, os, signal, subprocess, sys, threading, time, xml.etree.ElementTree as ET
contract = json.loads(os.environ["ANVIL_GPU_TEST_CONTRACT"])
deadline = time.monotonic() + contract["limits"]["timeout_seconds"]
limit = 12288
def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for part in iter(lambda: f.read(1048576), b""): h.update(part)
    return h.hexdigest()
def capture(argv, cwd):
    remaining = deadline - time.monotonic()
    if remaining <= 0: return None, "deadline", "", ""
    p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False, start_new_session=True)
    chunks, lock, overflow = {"out": bytearray(), "err": bytearray()}, threading.Lock(), threading.Event()
    def drain(name, pipe):
        while not overflow.is_set():
            part = pipe.read(8192)
            if not part: return
            with lock:
                if len(chunks["out"]) + len(chunks["err"]) + len(part) > limit: overflow.set(); return
                chunks[name].extend(part)
    readers = [threading.Thread(target=drain, args=(name, pipe), daemon=True) for name, pipe in (("out", p.stdout), ("err", p.stderr))]
    [reader.start() for reader in readers]
    state = None
    while p.poll() is None:
        if overflow.is_set() or time.monotonic() >= deadline:
            state = "output_limit" if overflow.is_set() else "deadline"
            os.killpg(p.pid, signal.SIGTERM); time.sleep(.2)
            if p.poll() is None: os.killpg(p.pid, signal.SIGKILL)
            break
        time.sleep(.01)
    p.wait(); [reader.join(2) for reader in readers]
    return p.returncode, state, chunks["out"].decode("utf-8", "replace"), chunks["err"].decode("utf-8", "replace")
try:
    import torch
    torch_version = torch.__version__
except Exception:
    torch = None
    torch_version = None
def cgroups():
    names = ("memory.max", "memory.swap.max", "cpu.max")
    try:
        return {name: open("/sys/fs/cgroup/" + name).read().strip() for name in names}
    except OSError as exc:
        return {"error": str(exc)}
def contained(c):
    try:
        quota, period = (int(value) for value in c["cpu.max"].split())
        return c["memory.max"] == str(contract["limits"]["memory_mib"] * 1024 * 1024) and c["memory.swap.max"] == "0" and 0 < quota <= contract["limits"]["cpus"] * period
    except (KeyError, ValueError): return False
result = {"source_hashes": {}, "commands": [], "torch_version": torch_version, "cgroups": cgroups(), "ok": False}
try:
    for source in contract["sources"]:
        actual = digest(source["path"]); result["source_hashes"][source["path"]] = actual
        if actual != source["sha256"]: raise RuntimeError("source hash mismatch")
    if not contained(result["cgroups"]): raise RuntimeError("container cgroup limits differ from contract")
    if torch is None or torch.cuda.device_count() != 1 or list(torch.cuda.get_device_capability(0)) != contract["gpu"]["compute_capability"]: raise RuntimeError("exact one CUDA device with requested capability is required")
    actual_uuid = str(getattr(torch.cuda.get_device_properties(0), "uuid", "")).lower()
    result["cuda"] = {"device_count": 1, "capability": list(torch.cuda.get_device_capability(0)), "uuid": actual_uuid}
    if actual_uuid != contract["gpu"]["uuid"].lower(): raise RuntimeError("CUDA device UUID differs from contract")
    for command in contract["commands"]:
        argv = list(command["argv"]); junit = "/tmp/" + command["id"] + ".xml"
        collection = None
        if command["kind"] == "pytest":
            collect = argv + ["--collect-only"]
            rc, state, out, err = capture(collect, command["cwd"])
            if rc != 0 or state or not all(os.path.basename(path) in out for path in command["source_paths"]): raise RuntimeError("pytest collection failed or missed a requested module")
            collected = [line for line in out.splitlines() if "::" in line]
            if not collected: raise RuntimeError("pytest collected no executable nodes")
            collection = {"nodes": collected, "stdout": out, "stderr": err}
            argv += ["--junitxml=" + junit, "-o", "xfail_strict=true"]
        started = time.monotonic(); rc, state, out, err = capture(argv, command["cwd"])
        row = {"id": command["id"], "argv": argv, "cwd": command["cwd"], "exit_code": rc, "elapsed_seconds": time.monotonic()-started, "stdout": out, "stderr": err, "state": state}
        if command["kind"] == "pytest": row["collection"] = collection
        if command["kind"] == "pytest" and state is None and not os.path.exists(junit): state = row["state"] = "junit_missing"
        if command["kind"] == "pytest" and state is None:
            root = ET.parse(junit).getroot(); suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
            counts = {key: sum(int(s.get(key, "0")) for s in suites) for key in ("tests", "failures", "errors", "skipped")}
            counts["xfails"] = sum(1 for item in root.findall(".//testcase/skipped") if "xfail" in item.get("type", "").lower())
            counts["skipped"] -= counts["xfails"]
            counts["xpassed"] = int("xpass" in (out + err).lower()); row["test_counts"] = counts
            executed = {path: any(os.path.splitext(os.path.basename(path))[0] in item.get("classname", "") for item in root.findall(".//testcase")) for path in command["source_paths"]}
            row["executed_modules"] = executed
            if not counts["tests"] or not all(executed.values()) or any(counts[key] for key in ("failures", "errors", "skipped", "xfails", "xpassed")): state = row["state"] = "pytest_counts"
        if command["kind"] == "standalone" and (state is None) and not all(marker in out for marker in command["completion_markers"]): state = row["state"] = "completion_markers"
        result["commands"].append(row)
        if rc != 0 or state: raise RuntimeError("command failed")
    result["ok"] = True
except subprocess.TimeoutExpired:
    result["error"] = "whole job deadline expired"
except Exception as exc:
    result["error"] = str(exc)
print("ANVIL_GPU_TEST_RESULT " + json.dumps(result, sort_keys=True))
'''


def _bounded_attach(argv, *, timeout, _runner=subprocess.Popen):
    """Capture only a bounded attach stream; overflow fails closed and reaps it."""
    process = _runner(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=_docker_env())
    output = bytearray()
    done = threading.Event()
    def read():
        while len(output) <= MAX_LOG_BYTES:
            part = process.stdout.read(8192)
            if not part: break
            output.extend(part)
        done.set()
    reader = threading.Thread(target=read, daemon=True); reader.start()
    expired = False
    try:
        while process.poll() is None and not done.is_set():
            if time.monotonic() >= timeout or len(output) > MAX_LOG_BYTES:
                expired = True
                break
            time.sleep(.01)
    finally:
        if process.poll() is None:
            expired = True
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            expired = True
        reader.join(10)
        if reader.is_alive():
            expired = True
    return process.returncode, output[:MAX_LOG_BYTES].decode("utf-8", "replace"), expired, len(output) > MAX_LOG_BYTES


def _docker_env():
    return nccl_probe.local_docker_env()


def _capability(uuid_value, *, _runner=subprocess.run):
    result = _local(["nvidia-smi", "--query-gpu=uuid,compute_cap", "--format=csv,noheader,nounits"], _runner=_runner)
    if result.returncode: raise ValueError("GPU capability inventory failed")
    for line in result.stdout.splitlines():
        ident, _, value = line.partition(",")
        if ident.strip() == uuid_value and re.fullmatch(r"\d+\.\d+", value.strip()):
            return [int(piece) for piece in value.strip().split(".")]
    raise ValueError("requested GPU capability is unavailable")


def _containment(container_id, contract, *, _runner=subprocess.run):
    raw = nccl_probe._checked(nccl_probe.DOCKER + ["inspect", "--format", "{{json .}}", container_id], _runner=_runner)
    row = json.loads(raw)
    host, config = row.get("HostConfig"), row.get("Config")
    limits, gpu = contract["limits"], contract["gpu"]
    expected_memory, expected_memory_swap = recipe_memory.docker_limits({"memory_limit_mib": limits["memory_mib"], "memory_swap_limit_mib": limits["memory_swap_mib"], "host_memory_reserve_mib": 1024})
    expected_cpu = limits["cpus"] * 10**9
    environment = dict(item.split("=", 1) for item in config.get("Env", []) if "=" in item) if isinstance(config, dict) else {}
    expected_env = {**contract["environment"], "TORCH_EXTENSIONS_DIR": "/tmp/torch_extensions", "TMPDIR": "/tmp", "PYTHONOPTIMIZE": "0", "PYTEST_ADDOPTS": ""}
    mounts = row.get("Mounts")
    requests = host.get("DeviceRequests") if isinstance(host, dict) else None
    exact_gpu = (isinstance(requests, list) and len(requests) == 1 and isinstance(requests[0], dict)
                 and requests[0].get("Count") == 0 and requests[0].get("DeviceIDs") == [gpu["uuid"]]
                 and requests[0].get("Capabilities") == [["gpu"]])
    ok = (isinstance(config, dict) and isinstance(host, dict) and row.get("Id") == container_id
          and host.get("ReadonlyRootfs") is True and host.get("Privileged") is False
          and host.get("CapDrop") == ["ALL"] and not host.get("CapAdd")
          and host.get("SecurityOpt") in (["no-new-privileges"], ["no-new-privileges=true"])
          and host.get("NetworkMode") == "none" and host.get("Memory") == expected_memory
          and host.get("MemorySwap") == expected_memory_swap and host.get("NanoCpus") == expected_cpu
          and host.get("PidsLimit") == 512 and not host.get("Binds") and exact_gpu
          and host.get("Tmpfs") == {"/tmp": "rw,nosuid,nodev,size=4g"}
          and all(environment.get(key) == expected for key, expected in expected_env.items())
          and isinstance(mounts, list) and all(isinstance(mount, dict) and mount.get("Type") == "tmpfs" and mount.get("Destination") == "/tmp" for mount in mounts))
    return {"ok": ok, "host_config": host, "mounts": mounts, "environment": environment, "gpu_uuid": gpu["uuid"]}


def _runner_contained(runner, limits):
    if not isinstance(runner, dict) or not isinstance(runner.get("cgroups"), dict):
        return False
    cgroups = runner["cgroups"]
    if cgroups.get("memory.max") != str(limits["memory_mib"] * 1024 * 1024) or cgroups.get("memory.swap.max") != "0":
        return False
    try:
        quota, period = (int(value) for value in cgroups.get("cpu.max", "").split())
    except ValueError:
        return False
    return 0 < quota <= limits["cpus"] * period


def run_job(contract, *, dry_run=True, _runner=subprocess.run, _attach=_bounded_attach):
    contract = validate_spec(contract)
    if platform.system() != "Linux" or Path("/.dockerenv").exists() or "wsl" in platform.release().lower() or "microsoft" in platform.release().lower():
        raise ValueError("this job requires the native Linux resource owner")
    image_id = json.loads(nccl_probe._checked(nccl_probe.DOCKER + ["image", "inspect", "--format", "{{json .Id}}", contract["image"]], _runner=_runner))
    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id): raise ValueError("image inspection did not resolve an immutable local image ID")
    devices, blockers = nccl_probe.inventory([contract["gpu"]["uuid"]], _runner=_runner)
    if devices[0]["used_mib"] > contract["gpu"]["idle_baseline_mib"]:
        blockers.append("requested GPU exceeds its declared idle baseline")
    actual_capability = _capability(contract["gpu"]["uuid"], _runner=_runner)
    if actual_capability != contract["gpu"]["compute_capability"]: blockers.append("requested GPU compute capability differs from spec")
    run_id, name = uuid.uuid4().hex, "anvil-gpu-test-" + uuid.uuid4().hex
    artifact = {"schema": SCHEMA, "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(), "spec_sha256": hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), "runner_sha256": hashlib.sha256(RUNNER.encode()).hexdigest(), "image": contract["image"], "image_id": image_id, "gpu": {**contract["gpu"], "actual_compute_capability": actual_capability, "device": devices[0]}, "environment": contract["environment"], "commands": contract["commands"], "blockers": blockers, "executed": False, "dry_run": dry_run, "ok": not blockers}
    if dry_run: return artifact
    if blockers: return artifact
    rechecked, blockers = nccl_probe.inventory([contract["gpu"]["uuid"]], _runner=_runner)
    if rechecked[0]["used_mib"] > contract["gpu"]["idle_baseline_mib"]:
        blockers.append("requested GPU exceeds its declared idle baseline")
    if blockers: return {**artifact, "ok": False, "blockers": blockers + ["GPU became busy before container create"]}
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    limits = contract["limits"]
    memory_bytes, swap_bytes = recipe_memory.docker_limits({"memory_limit_mib": limits["memory_mib"], "memory_swap_limit_mib": limits["memory_swap_mib"], "host_memory_reserve_mib": 1024})
    command = nccl_probe.DOCKER + ["create", "--name", name, "--label", f"{LABEL}={run_id}", "--pull", "never", "--gpus", "device=" + contract["gpu"]["uuid"], "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", str(memory_bytes), "--memory-swap", str(swap_bytes), "--cpus", str(limits["cpus"]), "--pids-limit", "512", "--tmpfs", "/tmp:rw,nosuid,nodev,size=4g", "--env", "ANVIL_GPU_TEST_CONTRACT=" + encoded, "--env", "TORCH_EXTENSIONS_DIR=/tmp/torch_extensions", "--env", "TMPDIR=/tmp", "--env", "PYTHONOPTIMIZE=0", "--env", "PYTEST_ADDOPTS="]
    for key, value in contract["environment"].items(): command += ["--env", key + "=" + value]
    command += ["--entrypoint", "python3", image_id, "-c", RUNNER]
    artifact["container"] = name; artifact["resource_containment"] = {"requested": {"network": "none", "host_mounts": False, "extra_capabilities": False, "effective_swap": 0, "cpus": limits["cpus"], "memory_mib": limits["memory_mib"], "tmpfs": "/tmp:4g"}}
    cleanup_target = name
    try:
        created = _local(command, _runner=_runner)
        if created.returncode: raise ValueError("GPU test container creation failed")
        container_id = created.stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{64}", container_id):
            raise ValueError("Docker did not return an exact created container ID")
        artifact["container_id"] = container_id
        cleanup_target = container_id
        artifact["resource_containment"]["observed"] = _containment(container_id, contract, _runner=_runner)
        if not artifact["resource_containment"]["observed"]["ok"]:
            raise ValueError("container containment readback differs from contract")
        artifact["executed"] = True
        exit_code, logs, timed_out, truncated = _attach(nccl_probe.DOCKER + ["start", "--attach", container_id], timeout=time.monotonic() + limits["timeout_seconds"] + 10)
        artifact.update(exit_code=exit_code, logs=logs, logs_truncated=truncated, timed_out=timed_out)
        marker = [line[len("ANVIL_GPU_TEST_RESULT "):] for line in logs.splitlines() if line.startswith("ANVIL_GPU_TEST_RESULT ")]
        artifact["runner"] = json.loads(marker[0]) if len(marker) == 1 else None
        artifact["ok"] = bool(exit_code == 0 and not timed_out and not truncated and artifact["runner"] and artifact["runner"].get("ok") and _runner_contained(artifact["runner"], limits))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        artifact.update(ok=False, error=str(exc))
    finally:
        try:
            artifact["cleanup"] = nccl_probe._cleanup(cleanup_target, run_id, label=LABEL, _runner=_runner)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            artifact["cleanup"] = {"ok": False, "error": str(exc)}
    if not artifact["cleanup"].get("ok"): artifact["ok"] = False
    return artifact


def main(argv=None):
    parser = argparse.ArgumentParser(prog="anvil-serving host gpu-test")
    parser.add_argument("--spec", required=True); parser.add_argument("--output", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    dry_run = args.dry_run or not confirmation_authorized()
    try:
        contract = validate_spec(json.loads(Path(args.spec).read_text(encoding="utf-8")))
        if not dry_run and not Path(args.output).expanduser().parent.is_dir():
            parser.error("confirmed execution requires --output in an existing private directory")
        result = run_job(contract, dry_run=dry_run)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"schema": SCHEMA, "ok": False, "dry_run": dry_run, "error": str(exc)}
    if not dry_run: atomic_write_json(args.output, result)
    print(json.dumps({key: value for key, value in result.items() if key != "logs"}, indent=2))
    return 0 if result.get("ok") else 1
