# Attempt 02: WSL2 NCCL transport controls

- Date: 2026-08-26
- Model revision: `Inferact/Qwen3.8-Flash-Next-NVFP4@103a7608316173ca6edd49929544244de7ffda70`
- Recipe digest: `4fe2b6f2810a80f8df79e35540f39ffc6d144cccb50e1b830de05b52e4f06af4`
- Served name: `qwen38-flash-next-inferact-nvfp4-vllm-tp2-262k-nospec-wsl2-nccl`
- vLLM build: `0.1.dev20073+g8e685d198`

The WSL2 controls fixed the first attempt's NCCL initialization failure. Both
ranks reported `ncclCommInitRank ... Init COMPLETE`, and vLLM selected the
PYNCCL all-reduce backend.

Startup then failed before weight loading in `GPUModelRunnerV2` while creating
the request-state staging buffer:

```text
RuntimeError: UVA is not available
```

This is the upstream vLLM WSL2 Model Runner V2 failure tracked by issues
`#47292`, `#47579`, and `#50239`. It is independent of context size, model
memory fit, and generated-answer quality. The next attempt retains the working
NCCL controls and sets the upstream-documented workaround
`VLLM_USE_V2_MODEL_RUNNER=0`. No capacity or quality-loss setting changes.

Sources:

- <https://github.com/vllm-project/vllm/issues/47292>
- <https://github.com/vllm-project/vllm/pull/47579>
- <https://github.com/vllm-project/vllm/issues/50239>
