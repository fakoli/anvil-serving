# Qwen3.5 122B NVFP4 Primary qualification

**Observed:** 2026-07-28
**Host:** Fakoli Dark, Windows/WSL2, one NVIDIA RTX PRO 6000 Blackwell Max-Q
Workstation Edition (96 GB, sm_120)
**Decision:** qualified for a human-gated Primary promotion, with Laguna S 2.1
retained as the immediate managed rollback.

## Exact identity and source review

The tested checkpoint is
[`nvidia/Qwen3.5-122B-A10B-NVFP4`](https://huggingface.co/nvidia/Qwen3.5-122B-A10B-NVFP4)
at revision `98915d837c4e7c87ac8296d02e89de19b3207e6d`. The Hugging Face API reported
that revision as the repository head on 2026-07-28; the repository was last
modified 2026-06-02 and the model card identifies a 2026-06-01 release. This
review found no newer official NVIDIA checkpoint.

The checkpoint `config.json` declares `max_position_embeddings=262144`.
Therefore the exact native limit is **262,144 tokens**, commonly rounded to
262K or approximately 264K. The local cache contained the complete 77.8 GiB
pinned snapshot before startup.

NVIDIA's card provides an NGC vLLM 26.04 reference command with ModelOpt FP4,
FP8 KV cache, tensor parallelism one, and Qwen reasoning/tool parsers. The
qualified local recipe uses the newer pinned
`nvcr.io/nvidia/vllm:26.06-py3` image and BF16 KV cache. A
[single-RTX-PRO-6000 community recipe](https://www.reddit.com/r/LocalLLaMA/comments/1s0m95a/docker_vllm_config_for_qwen35122ba10bnvfp4/)
and a
[Hugging Face operator discussion](https://huggingface.co/Sehyo/Qwen3.5-122B-A10B-NVFP4/discussions/10)
independently report the full 262,144-token shape.

MTP was deliberately not enabled. It is absent from NVIDIA's reference command,
and current reports include
[zero draft-token acceptance](https://github.com/vllm-project/vllm/issues/36331)
and checkpoint-specific output-quality concerns. An
[official-checkpoint discussion](https://huggingface.co/nvidia/Qwen3.5-122B-A10B-NVFP4/discussions/3)
also reports occasional extended or unproductive reasoning. These community
reports are recipe and risk priors, not local benchmark results.

## Qualified serve

| Setting | Qualified value |
|---|---|
| Image | `nvcr.io/nvidia/vllm:26.06-py3` |
| Runtime observed | vLLM `0.22.1+7b9cb5b7.dev` |
| Quantization / KV | `modelopt_fp4` / BF16 |
| Served name | `qwen35-122b-a10b-nvfp4` |
| Context / admission | 262,144 tokens / one sequence |
| Batch-token cap | 8,192 |
| GPU memory utilization | 0.94 |
| Tools / reasoning | `qwen3_coder` / `qwen3` |
| Multimodal policy | one image per prompt; video disabled |
| Production thinking policy | enabled by default; per-request disable supported |

The final multimodal startup completed in approximately 203 seconds. vLLM
reported 73.22 GiB of model memory, 13.85 GiB available for KV cache, 571,950
GPU KV-cache tokens, and 2.18-times maximum concurrency at the full
262,144-token window. Host telemetry observed approximately 88.5 GiB of VRAM
in use.

## Independent gates

- Thinking-disabled preflight passed smoke, structured JSON, approximately
  240K retrieval, and 10/10 tool calls.
- Thinking-enabled preflight passed smoke, JSON, approximately 128K retrieval,
  and 3/3 tools while retaining visible reasoning evidence.
- With the vision tower enabled, general image understanding and verbatim OCR
  passed with thinking disabled and enabled. Default-mode smoke and JSON also
  produced the required reasoning evidence.
- After managed promotion, the direct Primary endpoint repeated image and OCR
  successfully with default thinking and with thinking explicitly disabled.
  The authenticated `llm.primary` route also passed both image checks. Its
  response adapter omits the upstream reasoning field, so direct-serve
  evidence is the authoritative check that default thinking executed.
- The repeated protocol-v3 suite passed chat, context at 32K, 128K, and 240K,
  tools, session recall, unified-diff formatting, and timeout triage at the
  required 100% pass rate.
- A separate near-ceiling 240K request completed with 69.58 seconds TTFT and
  70.41 seconds end-to-end latency.
- Short-output capacity completed 10/10 at concurrency one. The warm repeat
  measured 0.15/0.26 seconds TTFT p50/p95 and 65 output tokens/second aggregate.

Raw artifacts:

- [Thinking-disabled 240K preflight](2026-07-28-qwen35-122b-primary-evidence/preflight-thinking-disabled-240k.json)
- [Thinking-enabled 128K preflight](2026-07-28-qwen35-122b-primary-evidence/preflight-thinking-enabled-128k.json)
- [Repeated protocol-v3 quality](2026-07-28-qwen35-122b-primary-evidence/quality-thinking-disabled.json)
- [Thinking-default control](2026-07-28-qwen35-122b-primary-evidence/thinking-disabled-control.json)
- [Multimodal, thinking disabled](2026-07-28-qwen35-122b-primary-evidence/multimodal-thinking-disabled.json)
- [Multimodal, thinking enabled](2026-07-28-qwen35-122b-primary-evidence/multimodal-thinking-enabled.json)
- [Multimodal profile, 240K text gate](2026-07-28-qwen35-122b-primary-evidence/multimodal-text-thinking-disabled-240k.json)
- [Multimodal profile, default thinking](2026-07-28-qwen35-122b-primary-evidence/multimodal-default-thinking.json)

## Caveats and rollback

The runtime labels the ModelOpt FP4 checkpoint format and Mamba align-mode
prefix caching as experimental. Image input is deliberately bounded to one
item, video is disabled, and thinking requires completion headroom. Near-ceiling
prefill is functional but slow.
The quality suite is a deterministic serving-contract gate, not a broad claim
that Qwen is generally more intelligent than Laguna.

The promotion plan changes only the Primary service and `llm.primary` route.
`poolside/Laguna-S-2.1-NVFP4` remains pinned at revision
`07614121b31898586430f189d27a25a0be310843` as
`primary-laguna-rollback`; the managed transaction restores it if the Qwen
startup, 240K gate, router reload, or routed identity check fails.
