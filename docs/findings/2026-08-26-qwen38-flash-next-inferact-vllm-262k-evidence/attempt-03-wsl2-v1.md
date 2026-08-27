# Attempt 03: WSL2 V1 model runner

- Date: 2026-08-26
- Model revision: `Inferact/Qwen3.8-Flash-Next-NVFP4@103a7608316173ca6edd49929544244de7ffda70`
- Image digest: `sha256:0aea30240f3e3d9ffae8526643950e170eb5fa07fc427016a9dd90892afa2aa3`
- vLLM build: `0.1.dev20073+g8e685d198`
- Topology: dual SM120, TP=2, PCIe under WSL2
- Context: 262,144
- GPU memory utilization: 0.90
- KV cache: auto
- Speculation and PLE CPU offload: disabled

The third managed recipe retained the working WSL2 NCCL controls and selected
the V1 runner with `VLLM_USE_V2_MODEL_RUNNER=0`. Both ranks completed NCCL
initialization and loaded approximately 85.76 GiB of model state per card.
TorchInductor autotuning then requested another 47.69 GiB allocation and failed
with CUDA OOM before the server allocated KV cache or became healthy.

This result empirically disqualifies this exact 0.90/V1/autotuned recipe. It is
not proof that the checkpoint or a 262K configuration is universally
infeasible: no stable KV capacity or request was measured, and higher memory
fraction or compile-disabled variants were not tested. The candidate was
unloaded through the managed recipe lifecycle, and GPU plus shared-memory state
was rechecked before the campaign moved to the RadixArk/SGLang lane.
