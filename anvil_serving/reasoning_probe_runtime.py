"""Closed, read-only runtime capture for the pinned GLM reasoning diagnostic."""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import serve_recipes
from .reasoning_marker_trace import (MARKER_ENTRYPOINT, MARKER_SOURCE_FILES,
    MARKER_HELPER_FILES, MARKER_MANIFEST_SHA256, validate_marker_trace)
from .models import _recipe_container_identity
from .router.request_control import RequestControlError
from .transports import _run_bounded_process
from .reasoning_probe import _bounded_get, _json, _read_bytes, _validate_runtime_patch, _PATCH_BASE_SOURCE

MODEL = "nvidia/GLM-5.3-Flash-FP4-Local"
REVISION = "76348ddb2be2e35e39ecfc00e75dc07703f4f3d5"
TEMPLATE = "/opt/lil/runtime/templates/glm53-flash.jinja"
MODEL_PATH = "/root/.cache/huggingface/hub/models--nvidia--GLM-5.3-Flash-FP4-Local/snapshots/" + REVISION
_PATCH_ENTRYPOINT = ["python3", "/opt/anvil-sampling/launch.py", "vllm", "serve"]
_PATCH_FILES = {
    "launch.py": "24203cc50619e0b4116be44cecb14d800f6b2845298f998e1384c6adeb369cd1",
    "kernel_regression.py": "86a15934b4b0a30f4514b410a51393d822e01f272eff65cfee07df97ca298159",
    "verify_patch.py": "3ed8f790bf9a5d03a7de6175c34f1d8f2b91f6182e213c681fc89114e0bc6d2c",
    "reference.py": "69b39e22aca53be843b0fc2d4e5c6adf2c9d1e23e4e8d0a95dea1b9f598e64f8",
    "native_comparator.py": "1125d27ad9314ddd5ee27bf3f651e18fa0adfb5c3c9364765e9e17683b7e34c6",
    "baseline.py": _PATCH_BASE_SOURCE,
}
_PROCESS_LIMIT = 4096
_PROC_READ_BYTES = 64 * 1024
SOURCE_FILES = (
    "sampling_params.py", "config/reasoning.py",
    "entrypoints/openai/chat_completion/protocol.py",
    "entrypoints/openai/chat_completion/serving.py",
    "v1/worker/gpu/sample/thinking_budget.py", "v1/worker/gpu/sample/sampler.py",
    "v1/worker/gpu/spec_decode/rejection_sampler.py", "v1/sample/ops/topk_topp_triton.py",
    "tokenizers/registry.py", "tokenizers/fastokens.py", "tokenizers/hf.py",
    "reasoning/__init__.py", "reasoning/abs_reasoning_parsers.py",
    "reasoning/glm47_moe_reasoning_parser.py", "parser/glm47_moe.py",
    "parser/engine/registered_adapters.py", "parser/engine/adapters.py",
    "parser/engine/parser_engine.py", "parser/engine/streaming_parser_engine.py",
    "parser/engine/parser_engine_config.py",
)

# Fixed program and fixed paths: no shell, caller-supplied code or model imports.
CAPTURE_PROGRAM = '''
import hashlib, importlib.metadata, importlib.util, json, os, stat, sys
result_stdout = sys.stdout
sys.stdout = sys.stderr
from pathlib import Path
import fastokens
fastokens.patch_transformers()
from transformers import AutoTokenizer
settings = json.loads(sys.argv[1])
root = Path(importlib.util.find_spec("vllm").origin).parent
files = {}
paths = {}
def read_file(label, path, maximum):
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("nonregular runtime artifact")
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("oversized runtime artifact")
    files[label] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    paths[label] = (path, maximum)
    return data
for name in settings["source_files"]:
    read_file("vllm/" + name, root / name, 2 * 1024 * 1024)
for name in settings["patch_files"]:
    read_file("patch/" + name, Path("/opt/anvil-sampling") / name, 256 * 1024)
for name in settings.get("marker_files", []):
    read_file("marker/" + name, Path("/opt/anvil-marker-trace") / name, 256 * 1024)
read_file("runtime-template", Path(settings["template"]), 256 * 1024)
model = Path(settings["model_path"])
read_file("tokenizer.json", model / "tokenizer.json", 32 * 1024 * 1024)
read_file("tokenizer_config.json", model / "tokenizer_config.json", 1024 * 1024)
tokenizer = AutoTokenizer.from_pretrained(str(model), local_files_only=True, trust_remote_code=False)
from vllm.reasoning import ReasoningParserManager
parser = ReasoningParserManager.get_reasoning_parser("glm45")(tokenizer)
if parser.reasoning_start_str != "<think>" or parser.reasoning_end_str != "</think>":
    raise ValueError("unexpected configured parser markers")
markers = {name: tokenizer.encode(text, add_special_tokens=False)
           for name, text in (("start", parser.reasoning_start_str), ("end", parser.reasoning_end_str))}
markers["forced_end"] = markers["end"]
before = dict(files)
for label, (path, maximum) in list(paths.items()):
    read_file(label, path, maximum)
if files != before:
    raise ValueError("runtime bytes changed during tokenizer construction")
print(json.dumps({"files": files, "markers": markers,
    "tokenizer_method": "fastokens.patch_transformers + AutoTokenizer local_files_only; add_special_tokens=False",
    "package_version": importlib.metadata.version("vllm")}), file=result_stdout)
'''


def _launch_projection(row):
    """Retain only closed diagnostic controls, never arbitrary argv or secrets."""
    config = row.get("Config") or {}
    command = config.get("Cmd") or []
    allowed = ("--reasoning-parser", "--chat-template", "--speculative-config",
               "--revision", "--served-model-name", "--reasoning-config",
               "--tokenizer", "--tokenizer-mode", "--tokenizer-revision")
    flags = {}
    for index, token in enumerate(command):
        if token.startswith(("--reasoning-config.", "--reasoning-start-str", "--reasoning-end-str",
                             "--reasoning-parser-plugin")):
            raise ValueError("unsupported reasoning override")
        for flag in allowed:
            if token == flag:
                if flag in flags or index + 1 == len(command):
                    raise ValueError("ambiguous diagnostic launch flag")
                flags[flag] = command[index + 1]
            elif token.startswith(flag + "="):
                if flag in flags:
                    raise ValueError("duplicate diagnostic launch flag")
                flags[flag] = token.split("=", 1)[1]
    environment = {}
    for item in config.get("Env") or []:
        key, separator, value = item.partition("=")
        if separator and key in {"VLLM_USE_V2_MODEL_RUNNER", "VLLM_USE_FASTOKENS",
                                  "VLLM_USE_FLASHINFER_SAMPLER", "ANVIL_SAMPLING_TRACE", "ANVIL_MARKER_TRACE"}:
            if key in environment:
                raise ValueError("ambiguous diagnostic environment")
            environment[key] = value
    return {"container_id": row.get("Id"), "image_id": row.get("Image"),
            "started_at": (row.get("State") or {}).get("StartedAt"),
            "running": (row.get("State") or {}).get("Running"),
            "flags": flags, "selected_environment": environment,
            "bound_endpoints": serve_recipes._recipe_bound_endpoints(row),
            "command_sha256": hashlib.sha256(json.dumps(command).encode()).hexdigest(),
            "entrypoint_sha256": hashlib.sha256(json.dumps(config.get("Entrypoint")).encode()).hexdigest(),
            "mounts_sha256": hashlib.sha256(json.dumps(row.get("Mounts"), sort_keys=True).encode()).hexdigest()}


def _collect_host_memory_snapshot(proc_root=Path("/proc")):
    """Fixed child-process collector; the parent enforces the hard wall deadline."""
    deadline = time.monotonic() + 4

    def read(relative):
        if time.monotonic() >= deadline:
            raise TimeoutError("host memory snapshot deadline")
        with (proc_root / relative).open("rb") as stream:
            data = stream.read(_PROC_READ_BYTES + 1)
        if len(data) > _PROC_READ_BYTES:
            raise ValueError("oversized proc observation")
        return data.decode()

    def start_time(pid):
        return int(read(f"{pid}/stat").rsplit(")", 1)[1].split()[19])

    memory = {}
    for line in read("meminfo").splitlines():
        name, value = line.split(":", 1)
        if name in {"MemTotal", "MemFree", "MemAvailable", "SwapTotal", "SwapFree",
                    "Cached", "Shmem", "SReclaimable", "SUnreclaim"}:
            memory[name + "_bytes"] = int(value.split()[0]) * 1024
    if not {"MemTotal_bytes", "MemAvailable_bytes", "SwapTotal_bytes", "SwapFree_bytes"} <= memory.keys():
        raise ValueError("incomplete host memory observation")
    swap_io = {name: int(value) for name, value in
               (line.split() for line in read("vmstat").splitlines())
               if name in {"pswpin", "pswpout", "oom_kill"}}
    if len(swap_io) != 3:
        raise ValueError("incomplete host swap observation")
    pressure = read("pressure/memory")
    processes = []
    start_times = {}
    unavailable = 0
    process_ids = heapq.nsmallest(_PROCESS_LIMIT + 1,
                                (int(path.name) for path in proc_root.iterdir() if path.name.isdigit()))
    for pid in process_ids[:_PROCESS_LIMIT]:
        try:
            incarnation = start_time(pid)
            fields = dict(line.split(":", 1) for line in read(f"{pid}/status").splitlines())
            processes.append({"pid": pid, "parent_pid": int(fields["PPid"]),
                "name": fields["Name"].strip()[:64],
                "rss_bytes": int(fields.get("VmRSS", "0 kB").split()[0]) * 1024})
            start_times[pid] = incarnation
        except (OSError, ValueError, KeyError, IndexError):
            unavailable += 1
            continue
    processes.sort(key=lambda row: row["rss_bytes"], reverse=True)
    for row in processes[:64]:
        row["pss_bytes"] = None
        try:
            if start_time(row["pid"]) != start_times[row["pid"]]:
                raise ValueError("process incarnation changed")
            for line in read(f"{row['pid']}/smaps_rollup").splitlines():
                if line.startswith("Pss:"):
                    row["pss_bytes"] = int(line.split()[1]) * 1024
            if start_time(row["pid"]) != start_times[row["pid"]]:
                raise ValueError("process incarnation changed")
        except (OSError, ValueError, IndexError):
            row["pss_bytes"] = None
    truncated = len(process_ids) > _PROCESS_LIMIT
    return {"observed_at_unix": time.time(), "host_memory": memory,
            "memory_pressure": pressure, "swap_io": swap_io,
            "process_count_observed": len(processes), "process_inventory_truncated": truncated,
            "process_inventory_incomplete": truncated or unavailable > 0,
            "process_rows_unavailable": unavailable,
            "largest_processes": processes[:64],
            "process_scope": "Top 64 by RSS among at most 4096 readable PIDs in numeric order; PSS only for unchanged PIDs. RSS and cgroup totals overlap; do not sum them."}


_HOST_SNAPSHOT_PROGRAM = ("import json; from anvil_serving.reasoning_probe_runtime import "
                          "_collect_host_memory_snapshot; print(json.dumps(_collect_host_memory_snapshot()))")


def _remaining_seconds(deadline, maximum=5):
    remaining = maximum if deadline is None else min(maximum, deadline - time.monotonic())
    if remaining <= 0:
        raise TimeoutError("runtime inspection deadline")
    return remaining


def host_memory_snapshot(*, runner=_run_bounded_process, deadline=None):
    """Bound the entire proc scan, including blocked kernel reads, in a child."""
    result = runner([sys.executable, "-c", _HOST_SNAPSHOT_PROGRAM],
                    timeout=_remaining_seconds(deadline), max_output_bytes=256 * 1024)
    if result.returncode:
        raise ValueError("host memory snapshot unavailable")
    return _json(result.stdout)


def resource_snapshot(port, *, runner=_run_bounded_process, deadline=None):
    """Read local counters; only the two engine occupancy gauges are mandatory."""
    from .benchmarking.campaign_telemetry import metric
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid local metrics port")
    host = host_memory_snapshot(runner=runner, deadline=deadline)
    gpu = runner(["nvidia-smi", "--query-gpu=index,memory.used,memory.total,utilization.gpu,power.draw",
                  "--format=csv,noheader,nounits"], timeout=_remaining_seconds(deadline), max_output_bytes=8192)
    if gpu.returncode:
        raise ValueError("GPU snapshot unavailable")
    raw = _bounded_get(f"http://127.0.0.1:{port}/metrics", {},
                       timeout_s=_remaining_seconds(deadline), maximum_bytes=1024 * 1024)
    text = raw.decode()
    engine = {name: metric(text, "vllm:num_requests_" + name) for name in ("running", "waiting")}
    _remaining_seconds(deadline)
    return {**host, "gpu_csv": gpu.stdout.decode(), "engine": engine}


def inspect_runtime(registry_path, container, plan_path, *, runner=_run_bounded_process):
    plan = _json(_read_bytes(Path(plan_path)))
    runtime = plan["runtime"]
    patch = _validate_runtime_patch(plan)
    marker_trace = validate_marker_trace(plan)
    expected_entrypoint = MARKER_ENTRYPOINT if marker_trace else _PATCH_ENTRYPOINT
    recipe = serve_recipes.find_recipe(serve_recipes.load_registry(registry_path), MODEL)
    if (recipe is None or recipe["download"]["revision"] != REVISION
            or recipe["serve"].get("model_path") != MODEL_PATH
            or runtime["model_revision"] != REVISION):
        raise ValueError("unsupported runtime inspection recipe")
    port = recipe["serve"].get("port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid recipe metrics port")
    deadline = time.monotonic() + 45

    def run(argv, **ignored):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("runtime inspection deadline")
        result = runner(argv, timeout=remaining, max_output_bytes=2 * 1024 * 1024)
        if result.returncode:
            raise ValueError("runtime inspection command failed")
        return subprocess.CompletedProcess(argv, result.returncode, result.stdout.decode(), result.stderr.decode())

    before = _recipe_container_identity(recipe, container, _run=run, require_recipe_digest=True)
    if not before["running"] or before["image_id"] != runtime["image_digest"]:
        raise ValueError("runtime image or running identity mismatch")
    identity = before["container_id"]

    def launch():
        rows = json.loads(run(["docker", "inspect", identity]).stdout)
        if len(rows) != 1:
            raise ValueError("ambiguous runtime identity")
        if patch is not None:
            config = rows[0].get("Config") or {}
            serve = recipe["serve"]
            command = expected_entrypoint[1:] + [MODEL_PATH] + [
                token for flag in serve.get("flags", []) for token in shlex.split(flag)]
            trace_settings = [item for item in serve.get("env", [])
                              if item.partition("=")[0] == "ANVIL_SAMPLING_TRACE"]
            if (serve.get("entrypoint") != expected_entrypoint
                    or serve.get("model_flag") or serve.get("model_env")
                    or config.get("Entrypoint") != expected_entrypoint[:1]
                    or config.get("Cmd") != command
                    or trace_settings != ["ANVIL_SAMPLING_TRACE=1"]):
                raise ValueError("runtime patch startup wrapper mismatch")
            labels = config.get("Labels") or {}
            expected = {"io.anvil-serving.patch.base-image": patch["base_image_digest"],
                        "io.anvil-serving.patch.upstream": patch["upstream_commit"],
                        "io.anvil-serving.patch.source-sha256": patch["source_sha256"]}
            if marker_trace:
                expected["io.anvil-serving.marker-trace.manifest-sha256"] = MARKER_MANIFEST_SHA256
            if any(labels.get(key) != value for key, value in expected.items()):
                raise ValueError("runtime patch image labels mismatch")
        observed = _launch_projection(rows[0])
        if patch is not None and observed["selected_environment"].get("ANVIL_SAMPLING_TRACE") != "1":
            raise ValueError("runtime patch trace environment mismatch")
        marker_settings = [item for item in recipe["serve"].get("env", [])
                           if item.partition("=")[0] == "ANVIL_MARKER_TRACE"]
        if marker_trace and (marker_settings != ["ANVIL_MARKER_TRACE=1"]
                or observed["selected_environment"].get("ANVIL_MARKER_TRACE") != "1"):
            raise ValueError("runtime marker trace environment mismatch")
        if not marker_trace and (marker_settings or "ANVIL_MARKER_TRACE" in observed["selected_environment"]):
            raise ValueError("undeclared marker trace environment")
        return observed

    projection = launch()
    flags = projection["flags"]
    speculative = {"method": "mtp", "draft_sample_method": "probabilistic",
                   "rejection_sample_method": "standard", "moe_backend": "b12x",
                   "attention_backend": "B12X", "num_speculative_tokens": 3}
    if (flags.get("--reasoning-parser") != "glm45" or flags.get("--chat-template") != TEMPLATE
            or flags.get("--revision") != REVISION
            or any(key in flags for key in ("--reasoning-config", "--tokenizer", "--tokenizer-revision"))
            or flags.get("--tokenizer-mode", "auto") != "auto"
            or projection["selected_environment"].get("VLLM_USE_V2_MODEL_RUNNER") != "1"
            or projection["selected_environment"].get("VLLM_USE_FASTOKENS") != "1"
            or json.loads(flags.get("--speculative-config", "{}")) != speculative
            or any(value not in {"0", "1"} for value in projection["selected_environment"].values())
            or projection["bound_endpoints"] != [{"host_ip": "127.0.0.1", "host_port": port, "container_port": port}]):
        raise ValueError("unsupported reasoning or tokenizer launch configuration")
    settings = {"source_files": tuple(dict.fromkeys((*SOURCE_FILES, *(MARKER_SOURCE_FILES if marker_trace else {})))), "template": TEMPLATE, "model_path": MODEL_PATH,
                "patch_files": list(_PATCH_FILES) if patch else [],
                "marker_files": list(MARKER_HELPER_FILES) if marker_trace else []}
    captured = json.loads(run(["docker", "exec", "--env", "CUDA_VISIBLE_DEVICES=",
                              "--env", "HF_HUB_OFFLINE=1", "--env", "TRANSFORMERS_OFFLINE=1",
                              "--env", "PYTHONDONTWRITEBYTECODE=1", identity, "python3", "-B", "-c",
                              CAPTURE_PROGRAM, json.dumps(settings)]).stdout)
    if captured["files"]["runtime-template"]["sha256"] != runtime["template_sha256"]:
        raise ValueError("runtime template hash mismatch")
    if patch is not None and captured["files"]["vllm/v1/sample/ops/topk_topp_triton.py"]["sha256"] != patch["source_sha256"]:
        raise ValueError("runtime patched source hash mismatch")
    if patch is not None and any(captured["files"]["patch/" + name]["sha256"] != digest
                                 for name, digest in _PATCH_FILES.items()):
        raise ValueError("runtime patch regression helper hash mismatch")
    if marker_trace:
        for prefix, bindings in (("vllm/", MARKER_SOURCE_FILES), ("marker/", MARKER_HELPER_FILES)):
            if any(captured["files"][prefix + name]["sha256"] != digest for name, digest in bindings.items()):
                raise ValueError("runtime marker trace source/helper hash mismatch")
    resources = resource_snapshot(port, runner=runner, deadline=deadline)
    after = _recipe_container_identity(recipe, container, _run=run, require_recipe_digest=True)
    if (launch() != projection or after["container_id"] != identity or not after["running"]
            or after["image_id"] != before["image_id"]):
        raise ValueError("runtime changed during capture")
    safe_projection = {key: value for key, value in projection.items()
                       if key not in {"flags", "selected_environment", "bound_endpoints"}}
    safe_projection["reasoning_parser"] = "glm45"
    safe_projection["speculative_tokens"] = 3
    safe_projection["selected_environment"] = {key: value == "1" for key, value in projection["selected_environment"].items()}
    return {"schema": "anvil-serving.reasoning-runtime-inspection/v1",
            "identity": before, "launch": safe_projection, "capture": captured,
            "resources": resources,
            "runtime_patch": patch, "marker_trace": marker_trace,
            "source_parity": "requires_comparison_with_pinned_source",
            "inference_requests": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--container", required=True)
    parser.add_argument("--plan", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = inspect_runtime(args.registry, args.container, args.plan)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError, RequestControlError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}))
        return 1
    print(json.dumps(result, indent=2))
    return 0
