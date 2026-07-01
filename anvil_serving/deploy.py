"""Render a tuned SGLang docker-compose for a given GPU + model (the hard-won defaults baked in).

GPU pinning (genericity:T007): `--gpu` accepts an index (``0``, ``"0"``) or a
``GPU-...`` UUID. It is resolved to a stable UUID via ``anvil_serving.gpus``
(shared with `multiplexer`) so the emitted compose pins the card the reliable
way — `CUDA_DEVICE_ORDER=PCI_BUS_ID` + `CUDA_VISIBLE_DEVICES=<uuid>` — because
Docker Desktop's WSL2 backend ignores `device_ids`-only pinning (CLAUDE.md
gotcha #13). When `nvidia-smi` is absent, resolution falls back to the bare
index/spec with a printed warning instead of crashing.
"""
import os
import subprocess
import sys
import argparse

from . import gpus as _gpus

TEMPLATE = os.path.join(os.path.dirname(__file__), "..", "templates", "docker-compose.yml.tmpl")


def _env_block(uuid):
    """Compose `environment:` block pinning by UUID, or "" when unresolved."""
    if not uuid:
        return ""
    return (
        "    environment:\n"
        "      CUDA_DEVICE_ORDER: PCI_BUS_ID\n"
        f"      CUDA_VISIBLE_DEVICES: {uuid}\n"
    )


def render(model_path, gpu=0, context=131072, served_name="local-specialist",
           kv_dtype="fp8_e5m2", max_running=16, mem_fraction=0.88, image="lmsysorg/sglang:latest",
           reasoning_parser="qwen3", tool_call_parser="qwen3_coder", language_only=True, port=30000,
           _run=subprocess.check_output):
    tmpl = open(TEMPLATE, encoding="utf-8").read() if os.path.isfile(TEMPLATE) else _FALLBACK
    extra = []
    if reasoning_parser: extra.append(f"      --reasoning-parser {reasoning_parser}")
    if tool_call_parser: extra.append(f"      --tool-call-parser {tool_call_parser}")
    if language_only:    extra.append("      --language-only")
    uuid, warning = _gpus.resolve_gpu(gpu, _run=_run)
    if warning:
        print(f"[anvil-serving] WARNING: {warning}", file=sys.stderr)
    device_id = uuid or str(gpu)
    return tmpl.format(image=image, port=port, model=model_path,
                       kv=kv_dtype, ctx=context, maxrun=max_running, memfrac=mem_fraction,
                       served=served_name, extra_flags="\n".join(extra),
                       env_block=_env_block(uuid), device_id=device_id)

_FALLBACK = """services:
  sglang:
    image: {image}
    container_name: sglang
    restart: unless-stopped
    shm_size: "16g"
    ports: ["{port}:{port}"]
    volumes: ["{model}:/models/local"]
{env_block}    deploy: {{resources: {{reservations: {{devices: [{{driver: nvidia, device_ids: ["{device_id}"], capabilities: [gpu]}}]}}}}}}
    command: >
      python3 -m sglang.launch_server
      --model-path /models/local
      --weight-loader-disable-mmap
      --kv-cache-dtype {kv}
{extra_flags}
      --context-length {ctx}
      --max-running-requests {maxrun}
      --mem-fraction-static {memfrac}
      --enable-metrics
      --served-model-name {served}
      --host 0.0.0.0 --port {port}
"""

def main(argv):
    ap = argparse.ArgumentParser(prog="anvil-serving deploy")
    ap.add_argument("--model", required=True, help="local model dir mounted into the container")
    ap.add_argument("--gpu", default="0", help="GPU index (e.g. 1) or GPU-UUID to pin the serve to")
    ap.add_argument("--context", type=int, default=131072)
    ap.add_argument("--served-name", default="local-specialist")
    ap.add_argument("--out", default="docker-compose.yml")
    a = ap.parse_args(argv)
    open(a.out, "w", encoding="utf-8").write(render(a.model, a.gpu, a.context, a.served_name))
    print("wrote", a.out, "\nLaunch:  docker compose -f", a.out, "up -d")
