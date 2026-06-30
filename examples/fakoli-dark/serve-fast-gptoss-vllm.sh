#!/usr/bin/env bash
# Fast tier: gpt-oss-20b on vLLM (RTX 5090 / GPU 0, port 30001).
#
# Reconstructed from the live `vllm-gptoss` container config so the fast serve has
# a canonical launch artifact (it previously lived only in a findings doc). Mirrors
# serve-fast-glm-vllm.sh. `anvil-serving serves up fast` runs this when the
# container does not yet exist (an already-stopped one is just `docker start`ed).
set -euo pipefail
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

GPU0_UUID="GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1"   # RTX 5090

docker rm -f vllm-gptoss 2>/dev/null || true

docker run -d --name vllm-gptoss \
  --gpus all \
  -e CUDA_VISIBLE_DEVICES="$GPU0_UUID" \
  -e CUDA_DEVICE_ORDER=PCI_BUS_ID \
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
