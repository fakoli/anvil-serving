"""Inspect a fixed accounting source profile in one bound managed runtime."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

from . import serve_recipes
from .models import _recipe_container_identity
from .reasoning_probe import _json, _read_bytes
from .transports import _run_bounded_process

FILES = ("v1/metrics/loggers.py", "v1/metrics/stats.py", "v1/engine/async_llm.py",
         "v1/engine/output_processor.py", "v1/core/sched/scheduler.py")

# Fixed program: paths are product-owned, never plan-controlled. Open each component
# without following links; compare hashes from two complete reads.
CAPTURE = r'''
import signal
signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("capture deadline")))
signal.setitimer(signal.ITIMER_REAL, 10)
import hashlib, json, os, stat, sys
roots = {"checkout": "/opt/vllm/vllm", "venv": "/opt/venv/lib/python%d.%d/site-packages/vllm" % sys.version_info[:2]}
files = ("v1/metrics/loggers.py", "v1/metrics/stats.py", "v1/engine/async_llm.py", "v1/engine/output_processor.py", "v1/core/sched/scheduler.py")
def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
def open_root(root):
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root.strip("/").split("/"):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise
def read(root_fd, name):
    fd = os.dup(root_fd)
    try:
        root_identity = identity(os.fstat(fd))
        parts = name.split("/")
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            initial = os.fstat(child)
            if not stat.S_ISREG(initial.st_mode):
                raise ValueError("nonregular file")
            with os.fdopen(child, "rb") as stream:
                child = -1
                data = stream.read(2 * 1024 * 1024 + 1)
                if identity(os.fstat(stream.fileno())) != identity(initial):
                    raise ValueError("file changed during read")
            if len(data) > 2 * 1024 * 1024:
                raise ValueError("oversized file")
            return ({"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()},
                    identity(initial), root_identity)
        finally:
            if child != -1: os.close(child)
    finally:
        os.close(fd)
def snapshot():
    found = {}
    for label, root in roots.items():
        try:
            fd = open_root(root)
        except FileNotFoundError:
            continue
        try:
            found[label] = {name: read(fd, name) for name in files}
        finally:
            os.close(fd)
    if not found:
        raise ValueError("no supported source root")
    return found
first = snapshot()
if first != snapshot():
    raise ValueError("source changed")
print(json.dumps({"roots": {label: {name: value[0] for name, value in entries.items()}
                            for label, entries in first.items()}}))
'''


def _plan(path):
    plan = _json(_read_bytes(Path(path)))
    if set(plan) != {"schema", "profile", "model", "revision", "image_id", "recipe_digest", "container_id", "started_at"}:
        raise ValueError("invalid plan fields")
    if plan["schema"] != "anvil-serving.runtime-source-inspection/v1" or plan["profile"] != "vllm-accounting":
        raise ValueError("unsupported profile")
    for key in ("model", "revision"):
        if not isinstance(plan[key], str) or not 1 <= len(plan[key]) <= 512:
            raise ValueError("invalid identity")
    for key in ("container_id", "recipe_digest"):
        if not isinstance(plan[key], str) or not re.fullmatch(r"[0-9a-f]{64}", plan[key]):
            raise ValueError("invalid identity")
    if not isinstance(plan["image_id"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", plan["image_id"]):
        raise ValueError("invalid image")
    if not isinstance(plan["started_at"], str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z", plan["started_at"]):
        raise ValueError("invalid start time")
    return plan


def inspect_runtime(registry, container, plan_path, *, runner=_run_bounded_process):
    plan = _plan(plan_path)
    recipes = serve_recipes.load_registry(registry)["recipe"]
    matches = [item for item in recipes if item["model"] == plan["model"]]
    if len(matches) != 1:
        raise ValueError("recipe selection mismatch")
    recipe = matches[0]
    if recipe.get("download", {}).get("revision") != plan["revision"] or serve_recipes.recipe_digest(recipe) != plan["recipe_digest"]:
        raise ValueError("recipe identity mismatch")
    deadline = time.monotonic() + 45
    def run(argv, **kwargs):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("inspection deadline")
        result = runner(argv, timeout=remaining, max_output_bytes=2 * 1024 * 1024)
        if result.returncode:
            raise ValueError("inspection command failed")
        return subprocess.CompletedProcess(argv, 0, result.stdout.decode(), result.stderr.decode())
    def identity():
        owner = _recipe_container_identity(recipe, container, _run=run, require_recipe_digest=True)
        if (owner["container_id"] != plan["container_id"] or not owner["running"]
                or owner["image_id"] != plan["image_id"] or owner["revision"] != plan["revision"]):
            raise ValueError("container identity mismatch")
        rows = json.loads(run(["docker", "inspect", plan["container_id"]]).stdout)
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError("invalid container inspection")
        row = rows[0]
        if (row.get("Id") != plan["container_id"] or row.get("Image") != plan["image_id"]
                or (row.get("State") or {}).get("Running") is not True
                or (row.get("State") or {}).get("StartedAt") != plan["started_at"]):
            raise ValueError("container generation mismatch")
    identity()
    result = run(["docker", "exec", "--env", "CUDA_VISIBLE_DEVICES=", "--env", "HF_HUB_OFFLINE=1",
                  "--env", "TRANSFORMERS_OFFLINE=1", "--env", "PYTHONDONTWRITEBYTECODE=1",
                  "--env", "LD_PRELOAD=", "--env", "LD_AUDIT=",
                  plan["container_id"], "/opt/venv/bin/python3", "-I", "-S", "-B", "-c", CAPTURE])
    captured = _json(result.stdout.encode())
    if set(captured) != {"roots"} or not isinstance(captured["roots"], dict):
        raise ValueError("invalid capture")
    roots = captured["roots"]
    if not roots or set(roots) - {"checkout", "venv"}:
        raise ValueError("invalid capture roots")
    for files in roots.values():
        if not isinstance(files, dict) or set(files) != set(FILES):
            raise ValueError("invalid capture files")
        for item in files.values():
            if (not isinstance(item, dict) or set(item) != {"bytes", "sha256"} or type(item["bytes"]) is not int
                    or not 0 <= item["bytes"] <= 2 * 1024 * 1024 or not isinstance(item["sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
                raise ValueError("invalid captured file")
    identity()
    return {"schema": "anvil-serving.runtime-source-inspection-result/v1", "identity": plan,
            "capture": captured, "source_parity": "requires_independent_comparison",
            "limitation": "selected_file_hashes_do_not_attest_executed_code_or_complete_image",
            "inference_requests": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--container", required=True)
    parser.add_argument("--plan", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = inspect_runtime(args.registry, args.container, args.plan)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError, serve_recipes.RecipeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}))
        return 1
    print(json.dumps(result, indent=2))
    return 0
