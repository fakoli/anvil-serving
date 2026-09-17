"""Preview or build a declared local image with bounded Linux build steps."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
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
        "platform", "timeout_seconds"
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
            "timeout_seconds": timeout, "argv": argv}


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


def _stream_build(argv, *, source_bytes, log, timeout, max_log_bytes=MAX_LOG_BYTES):
    """Feed immutable Dockerfile bytes and drain output with bounded retention."""
    state = {"log_truncated": False}
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
            built = build_runner(argv, source_bytes=source_bytes, log=log,
                                 timeout=plan["timeout_seconds"])
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
