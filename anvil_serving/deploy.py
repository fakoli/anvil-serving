"""Render a tuned SGLang docker-compose for a given GPU + model (the hard-won defaults baked in)."""
import os
import argparse

TEMPLATE = os.path.join(os.path.dirname(__file__), "..", "templates", "docker-compose.yml.tmpl")

def render(model_path, gpu_index=0, context=131072, served_name="local-specialist",
           kv_dtype="fp8_e5m2", max_running=16, mem_fraction=0.88, image="lmsysorg/sglang:latest",
           reasoning_parser="qwen3", tool_call_parser="qwen3_coder", language_only=True, port=30000):
    tmpl = open(TEMPLATE, encoding="utf-8").read() if os.path.isfile(TEMPLATE) else _FALLBACK
    extra = []
    if reasoning_parser: extra.append(f"      --reasoning-parser {reasoning_parser}")
    if tool_call_parser: extra.append(f"      --tool-call-parser {tool_call_parser}")
    if language_only:    extra.append("      --language-only")
    return tmpl.format(image=image, gpu=gpu_index, port=port, model=model_path,
                       kv=kv_dtype, ctx=context, maxrun=max_running, memfrac=mem_fraction,
                       served=served_name, extra_flags="\n".join(extra))

_FALLBACK = """services:
  sglang:
    image: {image}
    container_name: sglang
    restart: unless-stopped
    shm_size: "16g"
    ports: ["{port}:{port}"]
    volumes: ["{model}:/models/local"]
    deploy: {{resources: {{reservations: {{devices: [{{driver: nvidia, device_ids: ["{gpu}"], capabilities: [gpu]}}]}}}}}}
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
    ap.add_argument("--gpu", type=int, default=0)
    ap.add_argument("--context", type=int, default=131072)
    ap.add_argument("--served-name", default="local-specialist")
    ap.add_argument("--out", default="docker-compose.yml")
    a = ap.parse_args(argv)
    open(a.out, "w", encoding="utf-8").write(render(a.model, a.gpu, a.context, a.served_name))
    print("wrote", a.out, "\nLaunch:  docker compose -f", a.out, "up -d")
