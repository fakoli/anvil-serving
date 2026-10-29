"""Preview or build a declared local image with bounded Linux build steps."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import selectors
import signal
import sys
import subprocess
import tempfile
import threading
import time
import tomllib
import uuid
from pathlib import Path

from .paths import config_path
from .control_plane.mcp.runtime import _process_group_options, _terminate_process_tree

MAX_LOG_BYTES = 16 * 1024 * 1024

class ImageBuildError(ValueError):
    """A build cannot honor its declared contract."""


def build_plan(name: str, config: Path) -> dict:
    """Resolve a closed operator declaration without changing Docker state."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
        raise ImageBuildError("invalid build name")
    config = config.resolve()
    if config.stat().st_size > 1024 * 1024:
        raise ImageBuildError("image build configuration exceeds one MiB")
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    if set(data) != {"schema", "builds"} or data["schema"] != "anvil-serving.image-builds/v1":
        raise ImageBuildError("unsupported image build configuration")
    rows = data["builds"]
    if not isinstance(rows, dict) or name not in rows:
        raise ImageBuildError("named image build is not declared")
    row = rows[name]
    required = {"context", "dockerfile", "image", "cpus", "memory_mib"}
    if not isinstance(row, dict) or not required <= row.keys() or row.keys() - required - {
        "platform", "timeout_seconds", "min_host_available_mib", "min_context_free_mib"
    }:
        raise ImageBuildError("invalid image build fields")
    for field in ("context", "dockerfile", "image"):
        if not isinstance(row[field], str) or not row[field] or len(row[field]) > 4096:
            raise ImageBuildError("invalid image build text field")
    context = (config.parent / row["context"]).resolve()
    source = context / row["dockerfile"]
    dockerfile = source.resolve()
    if not context.is_dir() or source.is_symlink() or not dockerfile.is_file():
        raise ImageBuildError("build context or Dockerfile is not a regular local path")
    if not dockerfile.is_relative_to(context) or dockerfile.stat().st_size > 1024 * 1024:
        raise ImageBuildError("Dockerfile must be within context and at most one MiB")
    # A single stage forms a dependency chain: per-step limits cannot multiply
    # through independently executing stages. Multi-stage builds need a separate
    # builder-wide concurrency/resource contract, not a misleading total ceiling.
    with dockerfile.open("rb") as source_file:
        source_bytes = source_file.read(1024 * 1024 + 1)
    if len(source_bytes) > 1024 * 1024:
        raise ImageBuildError("Dockerfile exceeds one MiB")
    source_text = source_bytes.decode("utf-8")
    if len(re.findall(r"^\s*FROM\s+", source_text, re.I | re.M)) != 1:
        raise ImageBuildError("bounded image builds require a single-stage Dockerfile")
    image = row["image"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9._/:\-]{0,230}:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", image):
        raise ImageBuildError("image must be an explicit repository:tag")
    cpus, memory = row["cpus"], row["memory_mib"]
    timeout = row.get("timeout_seconds", 7200)
    if type(cpus) not in (int, float) or not math.isfinite(cpus) or not 0.25 <= cpus <= 4:
        raise ImageBuildError("build cpus must be between 0.25 and 4")
    if type(memory) is not int or not 256 <= memory <= 16384:
        raise ImageBuildError("build memory_mib must be between 256 and 16384")
    if type(timeout) is not int or not 30 <= timeout <= 14400:
        raise ImageBuildError("build timeout_seconds must be between 30 and 14400")
    guard_fields = {"min_host_available_mib", "min_context_free_mib"}
    present = guard_fields & row.keys()
    if present and (present != guard_fields or sys.platform != "linux" or any(
            type(row[key]) is not int or not 1 <= row[key] <= 10**9 for key in guard_fields)):
        raise ImageBuildError("host guard requires both positive Linux memory and disk floors")
    platform = row.get("platform", "linux/amd64")
    if not isinstance(platform, str) or platform not in ("linux/amd64", "linux/arm64"):
        raise ImageBuildError("bounded image builds require a Linux platform")
    argv = ["docker", "buildx", "build", "--load", "--platform", platform,
            "--resource", f"memory={memory}m", "--resource", f"memory-swap={memory}m",
            "--resource", "cpu-period=100000", "--resource", f"cpu-quota={int(cpus * 100000)}",
            "--progress", "plain", "--file", str(dockerfile), "--tag", image, str(context)]
    return {"schema": "anvil-serving.image-build/v1", "name": name, "image": image,
            "dockerfile_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "cpus": cpus, "memory_mib": memory, "platform": platform,
            "timeout_seconds": timeout, "argv": argv,
            **({key: row[key] for key in sorted(guard_fields)} if present else {})}


def _private_run_directory(log_dir: Path, runner) -> Path:
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if log_dir.is_symlink() or not log_dir.is_dir():
        raise ImageBuildError("build log directory must be a regular directory")
    if os.name != "nt" and log_dir.stat().st_mode & 0o022:
        raise ImageBuildError("build log directory must not be writable by other users")
    directory = log_dir / uuid.uuid4().hex
    directory.mkdir(mode=0o700)
    if os.name == "nt":
        identity = runner(["whoami", "/user", "/fo", "csv", "/nh"],
                          capture_output=True, text=True, timeout=10)
        rows = list(csv.reader(identity.stdout.splitlines())) if identity.returncode == 0 else []
        if len(rows) != 1 or len(rows[0]) != 2 or not re.fullmatch(r"S-1-[0-9-]+", rows[0][1]):
            raise ImageBuildError("cannot identify the owner of the private build log")
        secured = runner(["icacls", str(directory), "/inheritance:r", "/grant:r",
                          f"*{rows[0][1]}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F",
                          "*S-1-5-32-544:(OI)(CI)F"],
                         capture_output=True, text=True, timeout=10)
        if secured.returncode:
            raise ImageBuildError("cannot protect the build log directory")
    return directory


def _stream_build(argv, *, source_bytes, log, timeout, max_log_bytes=MAX_LOG_BYTES, guard=None):
    """Feed immutable Dockerfile bytes and drain output with bounded retention."""
    state = {"log_truncated": False}
    if guard is not None:
        return _guarded_stream_build(argv, source_bytes=source_bytes, log=log,
                                     timeout=timeout, max_log_bytes=max_log_bytes, guard=guard)
    with tempfile.TemporaryFile() as source:
        source.write(source_bytes)
        source.seek(0)
        process = subprocess.Popen(argv, stdin=source, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, bufsize=0,
                                   **_process_group_options())
        def drain():
            retained = 0
            try:
                while chunk := process.stdout.read(65536):
                    keep = min(len(chunk), max(0, max_log_bytes - retained))
                    if keep:
                        log.write(chunk[:keep])
                        log.flush()
                        retained += keep
                    if keep < len(chunk):
                        state["log_truncated"] = True
            except (OSError, ValueError):
                state["output_error"] = True
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            state["returncode"] = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(process)
            state.update({"returncode": process.poll() if process.poll() is not None else -1,
                          "error": "build client timed out",
                          "cancellation": "client_tree_termination_requested; daemon_state_unverified"})
        finally:
            reader.join(timeout=5)
            if reader.is_alive():
                _terminate_process_tree(process)
                reader.join(timeout=2)
                state["output_error"] = True
                state["cancellation"] = "output_drain_incomplete; daemon_state_unverified"
            process.stdout.close()
    return state



def _guarded_stream_build(argv, *, source_bytes, log, timeout, max_log_bytes, guard):
    """Route SIGTERM through owned cleanup without interrupting child registration."""
    interrupted = [False]
    previous = signal.getsignal(signal.SIGTERM)
    def handle_termination(signum, frame):
        interrupted[0] = True
    # signal.signal rejects non-main threads before any child is launched.
    signal.signal(signal.SIGTERM, handle_termination)
    try:
        return _guarded_stream_build_inner(argv, source_bytes=source_bytes, log=log,
                                           timeout=timeout, max_log_bytes=max_log_bytes,
                                           guard=guard, interrupted=interrupted)
    finally:
        signal.signal(signal.SIGTERM, previous)


def _guarded_stream_build_inner(argv, *, source_bytes, log, timeout, max_log_bytes, guard, interrupted):
    """Own the observer and build client; daemon cancellation remains unverified."""
    from .reasoning_probe import _read_bytes
    observer_path = Path(__file__).with_name("image_build_observer.py")
    observer_source = _read_bytes(observer_path)
    observer_sha256 = hashlib.sha256(observer_source).hexdigest()
    selector = selectors.DefaultSelector()
    try:
        observer = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c",
                                     observer_source.decode("utf-8"), guard["context"]],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, env={"LANG": "C.UTF-8"},
                                    **_process_group_options())
    except BaseException:
        try:
            selector.close()
        except BaseException:
            pass
        raise
    process = None
    reader = None
    reader_started = False
    reader_start_attempted = False
    state = {"log_truncated": False, "observer_sha256": observer_sha256,
             "min_available_bytes": None, "min_free_bytes": None}
    pending = b""
    last_sample = None
    last_observed = time.monotonic()
    started = last_observed

    def cleanup_failure(stage, exc):
        state.setdefault("cleanup_errors", []).append({"stage": stage, "type": type(exc).__name__})
        state["cleanup"] = "unknown"
        state.setdefault("error", "owned process cleanup unknown")
        state["returncode"] = -1
        if process is not None:
            state.setdefault("cancellation", "daemon_state_unverified")

    def cleanup_step(stage, action):
        try:
            action()
        except BaseException as exc:
            cleanup_failure(stage, exc)

    def stop_owned(child, stage):
        try:
            _terminate_process_tree(child)
            if child.poll() is None:
                raise OSError("owned process still running")
        except BaseException as exc:
            cleanup_failure(stage, exc)

    def heartbeat(wait):
        nonlocal pending, last_sample, last_observed
        if interrupted[0]:
            raise InterruptedError("build interrupted by SIGTERM")
        if observer.poll() is not None:
            raise ValueError("host observer exited")
        if selector.select(wait):
            chunk = os.read(observer.stdout.fileno(), 4096)
            if not chunk:
                raise ValueError("host observer output closed")
            pending += chunk
            if len(pending) > 4096:
                raise ValueError("host observer output overflow")
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                sample = json.loads(line)
                if (not isinstance(sample, dict) or set(sample) != {"available_bytes", "free_bytes", "monotonic"}
                        or any(type(sample[k]) is not int or sample[k] < 0 for k in ("available_bytes", "free_bytes"))
                        or type(sample["monotonic"]) not in (int, float) or not math.isfinite(sample["monotonic"])):
                    raise ValueError("invalid host observer sample")
                now = time.monotonic()
                stamp = sample["monotonic"]
                if stamp > now or now - stamp > 5 or last_sample is not None and (stamp <= last_sample or stamp - last_sample > 5):
                    raise ValueError("host observer heartbeat gap")
                last_sample, last_observed = stamp, now
                for name, key in (("min_available_bytes", "available_bytes"), ("min_free_bytes", "free_bytes")):
                    state[name] = sample[key] if state[name] is None else min(state[name], sample[key])
                if sample["available_bytes"] < guard["min_host_available_mib"] * 1024 * 1024:
                    raise ValueError("host available memory below floor")
                if sample["free_bytes"] < guard["min_context_free_mib"] * 1024 * 1024:
                    raise ValueError("context filesystem free space below floor")
        if time.monotonic() - last_observed > 5:
            raise ValueError("host observer heartbeat gap")

    try:
        if interrupted[0]:
            raise InterruptedError("build interrupted by SIGTERM")
        selector.register(observer.stdout, selectors.EVENT_READ)
        os.set_blocking(observer.stdout.fileno(), False)
        while last_sample is None:
            heartbeat(0.2)
        heartbeat(0)
        with tempfile.TemporaryFile() as source:
            source.write(source_bytes)
            source.seek(0)
            if interrupted[0]:
                raise InterruptedError("build interrupted by SIGTERM")
            process = subprocess.Popen(argv, stdin=source, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, bufsize=0, **_process_group_options())
            if interrupted[0]:
                raise InterruptedError("build interrupted by SIGTERM")
            def drain():
                retained = 0
                try:
                    while chunk := process.stdout.read(65536):
                        keep = min(len(chunk), max(0, max_log_bytes - retained))
                        if keep:
                            log.write(chunk[:keep])
                            log.flush()
                            retained += keep
                        if keep < len(chunk):
                            state["log_truncated"] = True
                except (OSError, ValueError):
                    state["output_error"] = True
            reader = threading.Thread(target=drain, daemon=True)
            reader_start_attempted = True
            reader.start()
            reader_started = True
            while process.poll() is None:
                if time.monotonic() - started >= timeout:
                    raise TimeoutError("build client timed out")
                heartbeat(0.2)
            heartbeat(0)
            state["returncode"] = process.returncode
            if process.returncode != 0:
                state["cancellation"] = "daemon_state_unverified"
            reader.join(timeout=5)
            if reader.is_alive() or state.get("output_error"):
                raise ValueError("build output drain incomplete")
            if interrupted[0]:
                raise InterruptedError("build interrupted by SIGTERM")
            if _read_bytes(observer_path) != observer_source:
                raise ValueError("host observer source changed")
    except (OSError, ValueError, TimeoutError, json.JSONDecodeError) as exc:
        state.update({"returncode": -1, "error": str(exc)})
        if process is not None:
            stop_owned(process, "build_client")
            state["cancellation"] = "client_tree_termination_requested; daemon_state_unverified"
    except BaseException as exc:
        state.update({"returncode": -1, "error": "build interrupted: " + type(exc).__name__,
                      "failure_type": type(exc).__name__})
        if process is not None:
            stop_owned(process, "build_client")
            state["cancellation"] = "client_tree_termination_requested; daemon_state_unverified"
    finally:
        if reader_start_attempted and not reader_started:
            cleanup_failure("reader_start", RuntimeError("reader start outcome unknown"))
            try:
                reader_started = reader.ident is not None
            except BaseException as exc:
                cleanup_failure("reader_status", exc)
        if reader_started:
            cleanup_step("reader_join", lambda: reader.join(timeout=2))
            reader_alive = True
            try:
                reader_alive = reader.is_alive()
            except BaseException as exc:
                cleanup_failure("reader_status", exc)
            if reader_alive:
                cleanup_failure("reader_join", RuntimeError("reader still running"))
                if process is not None:
                    stop_owned(process, "build_client")
        if process is not None and process.stdout is not None:
            cleanup_step("build_output_close", process.stdout.close)
        cleanup_step("selector_close", selector.close)
        stop_owned(observer, "observer")
        cleanup_step("observer_output_close", observer.stdout.close)
    if interrupted[0] and "error" not in state:
        state.update({"returncode": -1, "error": "build interrupted by SIGTERM"})
        if process is not None:
            state.setdefault("cancellation", "daemon_state_unverified")
    return state

def execute_build(plan: dict, *, confirm=False, dry_run=False, log_dir: Path,
                  runner=subprocess.run, build_runner=_stream_build,
                  directory_factory=_private_run_directory) -> dict:
    """Build and load only; never stop, start, or replace an inference serve."""
    result = {**plan, "dry_run": dry_run or not confirm, "ok": True}
    if result["dry_run"]:
        return result
    try:
        help_result = runner(["docker", "buildx", "build", "--help"],
                             capture_output=True, text=True, timeout=30)
        if help_result.returncode or "--resource" not in help_result.stdout:
            raise ImageBuildError("installed Docker Buildx lacks --resource; no build started")
        host = runner(["docker", "info", "--format", "{{.OSType}}"],
                      capture_output=True, text=True, timeout=30)
        if host.returncode or host.stdout.strip() != "linux":
            raise ImageBuildError("Docker must run Linux containers; no build started")
        existing = runner(["docker", "image", "ls", "--quiet", "--no-trunc",
                           "--filter", "reference=" + plan["image"]],
                          capture_output=True, text=True, timeout=30)
        if existing.returncode or not isinstance(existing.stdout, str) or len(existing.stdout) > 300000:
            raise ImageBuildError("image tag availability unknown; no build started")
        identities = existing.stdout.splitlines()
        if any(not re.fullmatch(r"sha256:[0-9a-f]{64}", item) for item in identities):
            raise ImageBuildError("image tag availability unknown; no build started")
        if identities:
            raise ImageBuildError("image tag already exists; no build started")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ImageBuildError("Docker build prerequisites unavailable; no build started") from exc
    with Path(plan["argv"][plan["argv"].index("--file") + 1]).open("rb") as source_file:
        source_bytes = source_file.read(1024 * 1024 + 1)
    if hashlib.sha256(source_bytes).hexdigest() != plan["dockerfile_sha256"]:
        raise ImageBuildError("Dockerfile changed after the preview; no build started")
    run_dir = directory_factory(log_dir, runner)
    log_path = run_dir / "build.log"
    iid_path = run_dir / "image.iid"
    argv = [*plan["argv"][:-1], "--iidfile", str(iid_path), plan["argv"][-1]]
    argv[argv.index("--file") + 1] = "-"
    started = time.monotonic()
    result.update({"log_path": str(log_path), "image_id": None})
    descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as log:
        try:
            guard = ({"context": plan["argv"][-1],
                      "min_host_available_mib": plan["min_host_available_mib"],
                      "min_context_free_mib": plan["min_context_free_mib"]}
                     if "min_host_available_mib" in plan else None)
            options = {"source_bytes": source_bytes, "log": log, "timeout": plan["timeout_seconds"]}
            if guard is not None:
                options["guard"] = guard
            built = build_runner(argv, **options)
            result.update(built)
            result["ok"] = built["returncode"] == 0 and not built.get("output_error") and not built.get("error")
        except subprocess.TimeoutExpired:
            result.update({"ok": False, "error": "build timed out",
                           "cancellation": "daemon_state_unverified"})
        except OSError:
            result.update({"ok": False, "error": "build process unavailable"})
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    if result["ok"]:
        image_id = iid_path.read_text(encoding="utf-8").strip() if iid_path.is_file() else ""
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            result.update({"ok": False, "error": "build returned no immutable image identity"})
        else:
            result["image_id"] = image_id
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("--config", type=Path, default=Path(config_path("image-builds.toml")))
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        plan = build_plan(args.name, args.config)
        result = execute_build(plan, confirm=args.confirm, dry_run=args.dry_run,
                               log_dir=Path(config_path("build-logs")))
    except (ImageBuildError, OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
