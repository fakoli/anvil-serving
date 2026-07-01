#!/usr/bin/env bash
# Serve cyankiwi/GLM-4.7-Flash-AWQ-4bit (compressed-tensors W4A16, model_type=glm4_moe_lite,
# 30B-A3B MoE, MLA attention) on GPU 0 (RTX 5090, 32GB, Blackwell sm_120), port 30001, via vLLM.
#
# WORKING configuration. Two Windows/Docker-Desktop gotchas baked in:
#
#   1. GPU isolation: Docker Desktop's WSL2 backend does NOT honor `--gpus '"device=0"'`
#      (or device=UUID) -- it passes ALL GPUs into the container. nvidia-smi inside the
#      container still shows both cards. The reliable fix is CUDA_VISIBLE_DEVICES set to the
#      target card's UUID (order-independent), so the CUDA runtime / vLLM can only see GPU 0.
#      UUID for the RTX 5090 here: GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1
#      (re-check with: nvidia-smi --query-gpu=index,name,gpu_uuid --format=csv)
#      This keeps the heavy SGLang serve on GPU 1 / :30000 untouched.
#
#   2. The image entrypoint is ["vllm","serve"], so the model path is a POSITIONAL arg
#      (NOT --model). On Git Bash / MSYS a leading-slash positional like /models/... gets
#      path-mangled into C:/Program Files/Git/models/... -> run via PowerShell, OR prefix the
#      bash invocation with MSYS_NO_PATHCONV=1 (and MSYS2_ARG_CONV_EXCL='*'). This script
#      sets the MSYS guards so it works when run from Git Bash too.
#
# MTP / speculative is deliberately OFF (the quant checkpoint lacks MTP weights; the model
# card's official command uses --speculative-config.method mtp which we omit).
set -euo pipefail
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

GPU0_UUID="GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1"   # REPLACE: your GPU's UUID — `nvidia-smi -L`

docker rm -f vllm-glm 2>/dev/null || true

docker run -d --name vllm-glm \
  --gpus all \
  -e CUDA_VISIBLE_DEVICES="$GPU0_UUID" \
  -e CUDA_DEVICE_ORDER=PCI_BUS_ID \
  --ipc=host \
  -p 127.0.0.1:30001:30001 \
  -v "C:/Users/sdoum/models/glm47-flash-awq:/models/glm47-flash-awq:ro" \
  `# REPLACE: your local model directory (this is a machine-specific Windows path)` \
  vllm/vllm-openai:nightly \
  /models/glm47-flash-awq \
  --served-model-name glm-4.7-flash \
  --max-model-len 65536 \
  --gpu-memory-utilization 0.92 \
  --tool-call-parser glm47 \
  --reasoning-parser glm45 \
  --enable-auto-tool-choice \
  --host 0.0.0.0 \
  --port 30001

# Poll readiness:
#   curl http://127.0.0.1:30001/v1/models
# Smoke test:
#   curl http://127.0.0.1:30001/v1/chat/completions -H 'Content-Type: application/json' \
#     -d '{"model":"glm-4.7-flash","messages":[{"role":"user","content":"say ready"}],"max_tokens":8,"temperature":0}'
