"""Inspect or export regular trace files from a recipe's /diagnostics volume."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from . import serve_recipes
from .models import _recipe_container_identity


_TRACE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\.(nsys-rep|sqlite|qdstrm)")
_LIFECYCLE_FORMAT = (
    '{"State":{{json .State}},"RestartCount":{{json .RestartCount}}}'
)


def _source_snapshot(root_fd, max_bytes):
    """Return immutable-custody metadata for every flat source entry."""
    names = sorted(os.listdir(root_fd))
    if len(names) > 256:
        raise ValueError("trace directory exceeds 256 entries")
    rows, total, inodes = [], 0, set()
    for name in names:
        info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("trace directory contains a non-regular entry")
        if info.st_nlink != 1:
            raise ValueError("trace directory contains a multiply linked entry")
        inode = (info.st_dev, info.st_ino)
        if inode in inodes:
            raise ValueError("trace directory contains duplicate source inodes")
        inodes.add(inode)
        total += info.st_size
        if total > max_bytes:
            raise ValueError("trace directory exceeds byte bound")
        rows.append((name, info.st_dev, info.st_ino, info.st_size,
                     info.st_mtime_ns, info.st_ctime_ns, info.st_nlink))
    return rows, total


def _open_output(output):
    fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    return fd, (info.st_dev, info.st_ino)


def _verify_output_path(output, identity):
    info = os.stat(output, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != identity:
        raise ValueError("trace output directory changed during export")


def _lifecycle_snapshot(observed):
    keys = ("StartedAt", "FinishedAt", "RestartCount")
    if not isinstance(observed, dict) or set(observed) != {"State", "RestartCount"}:
        raise ValueError("Docker returned incomplete candidate lifecycle projection")
    state = observed["State"]
    if not isinstance(state, dict) or set(keys[:2]) - set(state):
        raise ValueError("Docker returned incomplete candidate lifecycle state")
    started, finished, restart_count = state["StartedAt"], state["FinishedAt"], observed["RestartCount"]
    if not isinstance(started, str) or not isinstance(finished, str):
        raise ValueError("Docker returned invalid candidate lifecycle timestamps")
    if isinstance(restart_count, bool) or not isinstance(restart_count, int) or restart_count < 0:
        raise ValueError("Docker returned invalid candidate restart count")
    return {"StartedAt": started, "FinishedAt": finished, "RestartCount": restart_count,
            "ExitCode": state.get("ExitCode")}


def collect(root, output=None, *, max_bytes=10 << 30, _output_fd=None, _output_identity=None):
    """Read a stable, flat trace directory without following links."""
    deadline = time.monotonic() + 120
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    own_output_fd = None
    try:
        if output is not None and _output_fd is None:
            own_output_fd, _output_identity = _open_output(output)
            _output_fd = own_output_fd
        snapshot, total = _source_snapshot(root_fd, max_bytes)
        rows = []
        for name, *identity in snapshot:
            size = identity[2]
            row = {"name": name, "bytes": size}
            rows.append(row)
            if _output_fd is None or _TRACE_NAME.fullmatch(name) is None:
                if _output_fd is not None:
                    row["exported"] = False
                continue
            source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
            with os.fdopen(source, "rb") as src:
                before = os.fstat(src.fileno())
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                        before.st_ctime_ns, before.st_nlink) != tuple(identity):
                    raise ValueError("trace changed before export")
                target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=_output_fd)
                digest, copied = hashlib.sha256(), 0
                with os.fdopen(target, "wb") as dst:
                    while chunk := src.read(1 << 20):
                        copied += len(chunk)
                        if copied > size or time.monotonic() > deadline:
                            raise ValueError("trace changed or export timed out; partial output retained")
                        digest.update(chunk)
                        dst.write(chunk)
                    dst.flush()
                    os.fsync(dst.fileno())
                after = os.fstat(src.fileno())
                if (copied != size or (after.st_dev, after.st_ino, after.st_size,
                                       after.st_mtime_ns, after.st_ctime_ns,
                                       after.st_nlink) != tuple(identity)):
                    raise ValueError("trace changed during export; partial output retained")
                row.update(exported=True, sha256=digest.hexdigest())
        after_snapshot, after_total = _source_snapshot(root_fd, max_bytes)
        if after_snapshot != snapshot or after_total != total:
            raise ValueError("trace directory changed during collection")
        if _output_fd is not None:
            os.fsync(_output_fd)
            _verify_output_path(output, _output_identity)
    finally:
        if own_output_fd is not None:
            os.close(own_output_fd)
        os.close(root_fd)
    return {"files": rows, "total_bytes": total}


def trace_artifacts(model, container, registry, output=None, *, retained=False):
    endpoint = serve_recipes.recipe_memory._docker_endpoint(subprocess.run)
    if not isinstance(endpoint, str) or not endpoint.startswith("unix://"):
        raise ValueError("trace inspection requires the local Linux Docker endpoint")
    recipe = serve_recipes.find_recipe(serve_recipes.load_registry(registry), model)
    if recipe is None:
        raise ValueError("recipe not found")
    identity = None if retained else _recipe_container_identity(recipe, container, require_recipe_digest=True)
    volumes = [serve_recipes._parse_named_volume(v) for v in recipe["serve"].get("named_volumes", [])]
    selected = [name for name, target, _ in volumes if target == "/diagnostics"]
    if len(selected) != 1:
        raise ValueError("recipe must declare exactly one /diagnostics named volume")
    volume = selected[0]

    def inspect(argv):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=True)
        return json.loads(result.stdout)

    lifecycle = None
    if identity is not None:
        mounts = inspect(["docker", "inspect", "--format", "{{json .Mounts}}", identity["container_id"]])
        lifecycle = _lifecycle_snapshot(inspect(
            ["docker", "inspect", "--format", _LIFECYCLE_FORMAT, identity["container_id"]]
        ))
        if not any(m.get("Type") == "volume" and m.get("Name") == volume and m.get("Destination") == "/diagnostics" for m in mounts):
            raise ValueError("declared trace mount does not match container")
    metadata = inspect(["docker", "volume", "inspect", volume])[0]
    if metadata.get("Driver") != "local" or metadata.get("Options"):
        raise ValueError("only plain local Docker volumes are supported")
    attached = subprocess.run(["docker", "ps", "-aq", "--no-trunc", "--filter", "volume=" + volume], capture_output=True, text=True, timeout=10, check=True)
    expected = [] if retained else [identity["container_id"]]
    if attached.stdout.split() != expected:
        raise ValueError("trace volume must belong exclusively to the selected candidate")
    output_fd = output_identity = None
    try:
        if output is not None:
            if identity is not None and (identity["state"] != "exited" or identity["running"]):
                raise ValueError("export requires an exited candidate; inspect remains available while running")
            Path(output).mkdir(mode=0o700)
            output_fd, output_identity = _open_output(output)
        result = collect(metadata["Mountpoint"], output, _output_fd=output_fd,
                         _output_identity=output_identity)
        if identity is not None:
            current = _recipe_container_identity(recipe, container, require_recipe_digest=True)
            if any(current[k] != identity[k] for k in ("container_id", "image_id", "state", "running")):
                raise ValueError("candidate identity or state changed during trace collection")
            after_state = inspect(["docker", "inspect", "--format", _LIFECYCLE_FORMAT,
                                   identity["container_id"]])
            if _lifecycle_snapshot(after_state) != lifecycle:
                raise ValueError("candidate lifecycle changed during trace collection")
        again = inspect(["docker", "volume", "inspect", volume])[0]
        attached_after = subprocess.run(["docker", "ps", "-aq", "--no-trunc", "--filter", "volume=" + volume], capture_output=True, text=True, timeout=10, check=True)
        if again != metadata or attached_after.stdout.split() != expected:
            raise ValueError("trace volume identity or attachments changed during collection")
        if output_fd is not None:
            os.fsync(output_fd)
            _verify_output_path(output, output_identity)
    finally:
        if output_fd is not None:
            os.close(output_fd)
    return {"schema": "recipe-trace-artifacts/v1", "identity": identity, "volume": volume,
            "retained_volume": retained, "recipe_sha256": serve_recipes.recipe_digest(recipe),
            "volume_created_at": metadata.get("CreatedAt"),
            "started_at": lifecycle and lifecycle["StartedAt"],
            "finished_at": lifecycle and lifecycle["FinishedAt"],
            "exit_code": lifecycle and lifecycle["ExitCode"], **result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model")
    parser.add_argument("--container")
    parser.add_argument("--retained-volume", action="store_true")
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if bool(args.container) == bool(args.retained_volume):
        parser.error("choose --container or --retained-volume")
    try:
        result = trace_artifacts(args.model, args.container, args.registry, args.output, retained=args.retained_volume)
    except (ValueError, OSError, KeyError, IndexError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps({"ok": True, **result}))
    return 0
