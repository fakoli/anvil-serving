"""Closed, read-only runtime capture for the pinned GLM reasoning diagnostic."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
import urllib.request
from pathlib import Path

from . import serve_recipes
from .models import _recipe_container_identity
from .transports import _run_bounded_process
from .router.backends.relay import _direct_open
from .reasoning_probe import _json, _read_bytes

MODEL = "nvidia/GLM-5.3-Flash-FP4-Local"
REVISION = "76348ddb2be2e35e39ecfc00e75dc07703f4f3d5"
TEMPLATE = "/opt/lil/runtime/templates/glm53-flash.jinja"
MODEL_PATH = "/root/.cache/huggingface/hub/models--nvidia--GLM-5.3-Flash-FP4-Local/snapshots/" + REVISION
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
                                  "VLLM_USE_FLASHINFER_SAMPLER"}:
            if key in environment:
                raise ValueError("ambiguous diagnostic environment")
            environment[key] = value
    return {"container_id": row.get("Id"), "image_id": row.get("Image"),
            "started_at": (row.get("State") or {}).get("StartedAt"),
            "running": (row.get("State") or {}).get("Running"),
            "flags": flags, "selected_environment": environment,
            "bound_endpoints": serve_recipes._recipe_bound_endpoints(row),
            "command_sha256": hashlib.sha256(json.dumps(command).encode()).hexdigest(),
            "mounts_sha256": hashlib.sha256(json.dumps(row.get("Mounts"), sort_keys=True).encode()).hexdigest()}


def resource_snapshot(port, *, runner=_run_bounded_process):
    """Read local counters; only the two engine occupancy gauges are mandatory."""
    from .benchmarking.campaign_telemetry import metric
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid local metrics port")
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        name, value = line.split(":", 1)
        if name in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree", "Cached", "SReclaimable"}:
            memory[name + "_bytes"] = int(value.split()[0]) * 1024
    pressure = Path("/proc/pressure/memory").read_text()
    swap_io = {name: int(value) for name, value in
               (line.split() for line in Path("/proc/vmstat").read_text().splitlines())
               if name in {"pswpin", "pswpout", "oom_kill"}}
    gpu = runner(["nvidia-smi", "--query-gpu=index,memory.used,memory.total,utilization.gpu,power.draw",
                  "--format=csv,noheader,nounits"], timeout=5, max_output_bytes=8192)
    if gpu.returncode:
        raise ValueError("GPU snapshot unavailable")
    with _direct_open(urllib.request.Request(f"http://127.0.0.1:{port}/metrics"), timeout=5) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("oversized engine metrics")
    text = raw.decode()
    engine = {name: metric(text, "vllm:num_requests_" + name) for name in ("running", "waiting")}
    return {"observed_at_unix": time.time(), "host_memory": memory, "memory_pressure": pressure,
            "swap_io": swap_io, "gpu_csv": gpu.stdout.decode(), "engine": engine}


def inspect_runtime(registry_path, container, plan_path, *, runner=_run_bounded_process):
    plan = _json(_read_bytes(Path(plan_path)))
    runtime = plan["runtime"]
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
        return _launch_projection(rows[0])

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
    settings = {"source_files": SOURCE_FILES, "template": TEMPLATE, "model_path": MODEL_PATH}
    captured = json.loads(run(["docker", "exec", "--env", "CUDA_VISIBLE_DEVICES=",
                              "--env", "HF_HUB_OFFLINE=1", "--env", "TRANSFORMERS_OFFLINE=1",
                              "--env", "PYTHONDONTWRITEBYTECODE=1", identity, "python3", "-B", "-c",
                              CAPTURE_PROGRAM, json.dumps(settings)]).stdout)
    if captured["files"]["runtime-template"]["sha256"] != runtime["template_sha256"]:
        raise ValueError("runtime template hash mismatch")
    resources = resource_snapshot(port, runner=runner)
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
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}))
        return 1
    print(json.dumps(result, indent=2))
    return 0
