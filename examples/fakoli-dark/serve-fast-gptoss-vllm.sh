#!/usr/bin/env bash
# Fast tier: gpt-oss-20b on vLLM (RTX 5090 / GPU 0, port 30001).
#
# REFERENCE / SUPERSEDED — `anvil-serving serves up fast` NO LONGER runs this. The fast serve
# is now Docker-Compose-defined (examples/fakoli-dark/docker-compose.yml, service `fast`) per
# docs/adr/0002-serves-are-compose-defined.md; the manifest's `up` delegates to
# `docker compose up -d fast`. This script is kept only as a readable record of the `docker run`
# line the compose service was derived from. If you run it by hand, note the hard-won env vars
# below — especially VLLM_USE_V2_MODEL_RUNNER=0: WSL2 exposes no UVA, so vLLM's v2 model runner's
# UvaBuffer otherwise dies with "RuntimeError: UVA is not available" at engine init (gotcha #14).
set -euo pipefail
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

GPU0_UUID="GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1"   # RTX 5090

docker rm -f vllm-gptoss 2>/dev/null || true

docker run -d --name vllm-gptoss \
  --gpus all \
  -e CUDA_VISIBLE_DEVICES="$GPU0_UUID" \
  -e CUDA_DEVICE_ORDER=PCI_BUS_ID \
  -e VLLM_USE_V2_MODEL_RUNNER=0 \
  --ipc=host \
  -p 30001:30001 \
  -v "C:/Users/sdoum/models/gpt-oss-20b:/models/gpt-oss-20b:ro" \
  vllm/vllm-openai:nightly \
  serve /models/gpt-oss-20b \
  --served-model-name gpt-oss-20b \
  --max-model-len 65536 \
  --gpu-memory-utilization 0.90 \
  --enable-auto-tool-choice \
  --tool-call-parser openai \
  --host 0.0.0.0 --port 30001

echo "vllm-gptoss starting on GPU 0 (RTX 5090). Watch: docker logs -f vllm-gptoss"
echo "Health: curl http://localhost:30001/health   Models: curl http://localhost:30001/v1/models"
