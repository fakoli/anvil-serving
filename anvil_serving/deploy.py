"""Render a tuned SGLang docker-compose for a given GPU + model (the hard-won defaults baked in).

GPU pinning (genericity:T007): `--gpu` accepts an index (``0``, ``"0"``) or a
``GPU-...`` UUID. It is resolved to a stable UUID via ``anvil_serving.gpus``
(shared with `multiplexer`) so the emitted compose pins the card the reliable
way — `CUDA_DEVICE_ORDER=PCI_BUS_ID` + `CUDA_VISIBLE_DEVICES=<uuid>` — because
Docker Desktop's WSL2 backend ignores `device_ids`-only pinning (CLAUDE.md
gotcha #13). When `nvidia-smi` is absent, resolution falls back to the bare
index/spec with a printed warning instead of crashing.

Loopback by default (genericity:T008): the rendered compose publishes on
`127.0.0.1:{port}` — the SGLang endpoint is unauthenticated, so a LAN/public
bind hands any peer on the network unauthenticated model access. Pass
`--expose-lan` (or `--bind <addr>`) to opt in to `0.0.0.0`; both print a
security warning pointing at SECURITY.md (CLAUDE.md gotcha #1).
"""
import ipaddress
import os
import subprocess
import sys
import argparse

from . import gpus as _gpus

TEMPLATE = os.path.join(os.path.dirname(__file__), "..", "templates", "docker-compose.yml.tmpl")

LOOPBACK_BIND = "127.0.0.1"
LAN_BIND = "0.0.0.0"


def _is_loopback_bind(bind):
    """True if `bind` is a confirmed loopback address; non-numeric hostnames
    and wildcard binds ("", "0.0.0.0", "::") are treated as NOT loopback."""
    if bind in ("", LAN_BIND, "::"):
        return False
    try:
        return ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return False


def _warn_if_public_bind(bind):
    if _is_loopback_bind(bind):
        return
    print(
        f"\n[anvil-serving] WARNING: publishing on {bind!r} exposes the "
        f"unauthenticated model endpoint on the network — any peer can send "
        f"inference requests and consume your GPU.\n"
        f"  Keep the default (127.0.0.1) unless you have placed your own "
        f"authentication/network controls in front of it. See SECURITY.md.\n",
        file=sys.stderr,
        flush=True,
    )


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
           bind=LOOPBACK_BIND, _run=subprocess.check_output):
    tmpl = open(TEMPLATE, encoding="utf-8").read() if os.path.isfile(TEMPLATE) else _FALLBACK
    extra = []
    if reasoning_parser: extra.append(f"      --reasoning-parser {reasoning_parser}")
    if tool_call_parser: extra.append(f"      --tool-call-parser {tool_call_parser}")
    if language_only:    extra.append("      --language-only")
    uuid, warning = _gpus.resolve_gpu(gpu, _run=_run)
    if warning:
        print(f"[anvil-serving] WARNING: {warning}", file=sys.stderr)
    _warn_if_public_bind(bind)
    device_id = uuid or str(gpu)
    return tmpl.format(image=image, port=port, model=model_path, bind=bind,
                       kv=kv_dtype, ctx=context, maxrun=max_running, memfrac=mem_fraction,
                       served=served_name, extra_flags="\n".join(extra),
                       env_block=_env_block(uuid), device_id=device_id)

_FALLBACK = """services:
  sglang:
    image: {image}
    container_name: sglang
    restart: unless-stopped
    shm_size: "16g"
    ports: ["{bind}:{port}:{port}"]
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
    ap.add_argument("--bind", default=None,
                    help="publish address (default 127.0.0.1; loopback-only). "
                         "Pass 0.0.0.0 (or --expose-lan) to LAN-expose the "
                         "unauthenticated endpoint — see SECURITY.md.")
    ap.add_argument("--expose-lan", action="store_true",
                    help="shorthand for --bind 0.0.0.0")
    a = ap.parse_args(argv)
    bind = a.bind or (LAN_BIND if a.expose_lan else LOOPBACK_BIND)
    open(a.out, "w", encoding="utf-8").write(
        render(a.model, a.gpu, a.context, a.served_name, bind=bind))
    print("wrote", a.out, "\nLaunch:  docker compose -f", a.out, "up -d")
