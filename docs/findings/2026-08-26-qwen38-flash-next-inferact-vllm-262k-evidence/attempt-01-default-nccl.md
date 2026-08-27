# Attempt 01: default NCCL transport

- Date: 2026-08-26
- Model: `Inferact/Qwen3.8-Flash-Next-NVFP4`
- Revision: `103a7608316173ca6edd49929544244de7ffda70`
- Served name: `qwen38-flash-next-inferact-nvfp4-vllm-tp2-262k-nospec`
- Image digest: `sha256:0aea30240f3e3d9ffae8526643950e170eb5fa07fc427016a9dd90892afa2aa3`
- vLLM build: `0.1.dev20073+g8e685d198`
- Topology: dual SM120, TP=2, PCIe under WSL2
- Context: 262144
- GPU memory utilization: 0.90
- KV cache: auto
- Speculation: disabled

The managed load resolved `Qwen4ExpForConditionalGeneration`, detected the
ModelOpt NVFP4 checkpoint, selected the 262144-token context, and started both
TP workers. Both ranks then failed at `ncclCommInitRank` with
`RuntimeError: NCCL error: unhandled cuda error` before model weights loaded.

This is a transport-initialization failure, not evidence about model fit,
262K capacity, speed, or quality. The next attempt changes only the established
WSL2 NCCL transport controls. It does not add CPU PLE offload, FP8 KV, forced
P2P levels, privileged mode, MTP, or a higher GPU-memory fraction.
