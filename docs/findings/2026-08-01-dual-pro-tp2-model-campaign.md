# Dual RTX PRO 6000 TP=2 model campaign

**Captured:** 2026-08-01 on Fakoli Dark. **Decision:** fresh exclusive-TP=2
qualification evidence; `no-promotion` for every candidate. No production
alias or router profile changed.

This campaign is the first model-by-model measurement after Fakoli Dark moved
from one RTX PRO 6000 plus one RTX 5090 to two equal RTX PRO 6000 Blackwell
Max-Q cards. Five models completed the full functional, capacity, and repeated
quality sequence: Qwen3.5 122B, Nemotron 3 Super 120B, Laguna S 2.1,
DeepSeek V4 Flash 0731, and Inkling Small NVFP4.

## Hardware and isolation

- 2× NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition, 97,887 MiB each,
  sm_120, 300 W power limit, driver 610.88.
- PXB PCIe path between cards; no NVLink. The 192 GB figure is aggregate VRAM,
  not unified memory.
- Windows 11, Docker Desktop/WSL2, Docker client/server 29.6.2.
- Exclusive TP=2 mode assigned both declared GPU roles to exactly one campaign
  owner. Split-mode workloads, Omni, voice, router, and all other inference
  were offline during each measured lane.
- Every engine admitted one request. Burst probes are diagnostic only and are
  not presented as a throughput win over the c1 contract.

Published evidence uses hardware roles and the generic address
`100.64.0.10`; host GPU UUIDs, private addresses, and machine-local control
paths were removed without changing measurements.

## Research and quantization decision

The pre-launch [compatibility brief](2026-08-01-dual-pro-tp2-campaign-evidence/compatibility-brief.md)
and machine-readable [source registry](2026-08-01-dual-pro-tp2-campaign-evidence/source-registry.json)
record each source URL, observation date, source date, age class, evidence
type, engine/hardware relevance, and decision impact.

No current artifact using a genuine `NVFP8` format or label was found for
DeepSeek V4 Flash 0731 or Inkling Small. DeepSeek's publisher checkpoint is a
hybrid that declares FP4 experts and FP8 quantization. Inkling's official
Blackwell-native artifact is NVFP4. Its ordinary dynamic-FP8 conversion is
about 266B safetensor parameters and does not fit 192 GB aggregate VRAM with
runtime and KV headroom. The campaign therefore does not relabel either model
as NVFP8.

## Pinned configurations

| Candidate / served name | Exact revision | Engine and image | Quantization, KV, context | Distributed lane |
|---|---|---|---|---|
| `nvidia/Qwen3.5-122B-A10B-NVFP4` / `qwen35-122b-a10b-nvfp4-tp2` | `98915d837c4e7c87ac8296d02e89de19b3207e6d` | NVIDIA vLLM, `sha256:bebcf9576b1720214319ee5c7ee4f7661954cbbf59ed3fcd188cd79a67f1967e` | ModelOpt NVFP4, BF16 KV, 262,144 served | TP=2, c1, MTP off |
| `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4` / `nemotron3-super-120b-a12b-nvfp4-tp2` | `4f0cf9daaeb7a4d5e23f80a00e7ed15f0e03caf6` | NVIDIA vLLM, same pinned image | NVFP4, FP8 KV, 65,536 served | TP=2 + EP=2, c1, MTP off |
| `poolside/Laguna-S-2.1-NVFP4` / `laguna-s-2.1-nvfp4-tp2` | `07614121b31898586430f189d27a25a0be310843` | vLLM 0.25.1, `sha256:e4f88a835143cd22aee2397a26ec6bb80b3a4a6fe0c882bcbc63822904766089` | NVFP4, FP8 KV, 262,144 served | TP=2, c1, no DFlash |
| `deepseek-ai/DeepSeek-V4-Flash-0731` / `deepseek-v4-flash-0731-tp2` | `7872f01b1d1fe23eabc4c98b48bffcef5a386062` | SGLang 0.5.16 derived image `sha256:0aa5324c4f38bc66f4b55e1e12efab821ef614b1a8629259b2810ff72a6570e6` | publisher FP4-expert/FP8 hybrid, FP8 E4M3 KV, 32,768 served | TP=2, c1, speculative decode off |
| `thinkingmachines/Inkling-Small-NVFP4` / `inkling-small-nvfp4-tp2` | `b6a99534467840620d411e4cd4ad5819b2610d9c` | SGLang `b7252cc6b` derived image `sha256:6a8afc5ca0036c1be8810443636d6f835702d1e2ae5a1d717990b0baf8e70a2f` | ModelOpt NVFP4, BF16 KV/SWA, 32,768 served | TP=2, c1, Marlin FP4/MoE and Triton attention |

The complete machine-readable launch surface is
[`configs/tp2-model-campaign-recipes.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/tp2-model-campaign-recipes.toml).
All recipes verify the exact cached revision before GPU allocation, force the
serve offline after verification, and use the WSL2-safe NCCL contract proven
during bring-up.

## Functional and repeated quality gates

| Candidate | Functional gate | Repeated protocol-v3 quality | Gate result |
|---|---|---|---|
| Qwen3.5 | smoke, JSON, 30K retrieval, tools, streaming tools, tool-result continuation; Responses returned an empty/tiny-reasoning output in the extended subset | thinking disabled; intelligence 6/6, session 3/3, tools 3/3 | functional and declared quality contract pass; extended Responses caveat retained |
| Nemotron Super | smoke, JSON, 30K retrieval, tools 20/20, streaming tools, tool result, Responses | thinking disabled; intelligence 6/6, session 3/3, tools 3/3 | pass |
| Laguna S | smoke, JSON, 30K retrieval, tools 20/20, streaming tools, tool result, Responses | thinking disabled; intelligence 6/6, session 3/3, tools 3/3 | pass |
| DeepSeek 0731 | low-reasoning smoke/JSON/30K retrieval/tools, streaming tools, tool result, Responses | `reasoning_effort=low`, 512 visible + 4,096 headroom; intelligence 6/6, session 3/3, tools 3/3 | pass |
| Inkling Small | core smoke/JSON/30K retrieval/tools 20/20 passed with both `none` and `low`; low-reasoning streaming tools and tool-result continuation passed | `reasoning_effort=low`, 512 visible + 4,096 headroom; intelligence 6/6, session 3/3, tools 3/3 | declared low-reasoning contract pass; extended `none` Responses subset leaked internal reasoning and failed its stricter evidence policy |

Raw functional and quality artifacts:

- Qwen: [preflight](2026-08-01-dual-pro-tp2-campaign-evidence/qwen35-preflight-thinking-disabled.json),
  [extended tools](2026-08-01-dual-pro-tp2-campaign-evidence/qwen35-preflight-agent-tools.json),
  [quality](2026-08-01-dual-pro-tp2-campaign-evidence/qwen35-quality-tp2.json), and
  [control proof](2026-08-01-dual-pro-tp2-campaign-evidence/qwen35-thinking-disabled-control.json).
- Nemotron: [preflight](2026-08-01-dual-pro-tp2-campaign-evidence/nemotron3-super-preflight-thinking-disabled.json),
  [extended tools](2026-08-01-dual-pro-tp2-campaign-evidence/nemotron3-super-preflight-agent-tools.json),
  [quality](2026-08-01-dual-pro-tp2-campaign-evidence/nemotron3-super-quality-tp2.json), and
  [control proof](2026-08-01-dual-pro-tp2-campaign-evidence/nemotron3-super-thinking-disabled-control.json).
- Laguna: [preflight](2026-08-01-dual-pro-tp2-campaign-evidence/laguna-s-21-preflight-thinking-disabled.json),
  [extended tools](2026-08-01-dual-pro-tp2-campaign-evidence/laguna-s-21-preflight-agent-tools.json),
  [quality](2026-08-01-dual-pro-tp2-campaign-evidence/laguna-s-21-quality-tp2.json), and
  [control proof](2026-08-01-dual-pro-tp2-campaign-evidence/laguna-s-21-thinking-disabled-control.json).
- DeepSeek: [preflight](2026-08-01-dual-pro-tp2-campaign-evidence/deepseek-v4-flash-0731-preflight-reasoning-low.json),
  [extended tools](2026-08-01-dual-pro-tp2-campaign-evidence/deepseek-v4-flash-0731-tools-extended-reasoning-low.json),
  [quality](2026-08-01-dual-pro-tp2-campaign-evidence/deepseek-v4-flash-0731-quality-tp2-reasoning-low.json), and
  [control proof](2026-08-01-dual-pro-tp2-campaign-evidence/deepseek-v4-flash-0731-reasoning-low-control.json).
- Inkling: [`none` core preflight](2026-08-01-dual-pro-tp2-campaign-evidence/inkling-functional-none.json),
  [`none` extended failure](2026-08-01-dual-pro-tp2-campaign-evidence/inkling-extended-none.json),
  [`low` preflight](2026-08-01-dual-pro-tp2-campaign-evidence/inkling-functional-low.json),
  [`low` quality](2026-08-01-dual-pro-tp2-campaign-evidence/inkling-quality-low.json), and
  [control proof](2026-08-01-dual-pro-tp2-campaign-evidence/inkling-reasoning-low-control.json).

## Capacity results

The matched baseline uses c1 and a requested 32K context. Actual prompts are
shown because tokenizer and completion-margin clamping differ by family.
`capacity-v3` begins generation timing at first visible content.
`capacity-v4-reasoning` begins at the first reasoning or visible delta and
also retains first-visible TTFT.

| Candidate | Protocol | Completion | Actual prompt p50 | TTFO p50 | First-visible TTFT p50 | Effective prefill p50 | Decode p50 | Aggregate output |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Qwen3.5 | `capacity-v3` | 12/12 | 29,804 | same as TTFT | 2.324 s | 12,821 tok/s | 67.5 tok/s | 17.83 tok/s |
| Nemotron Super | `capacity-v3` | 12/12 | 28,438 | same as TTFT | 2.836 s | 10,025 tok/s | 59.5 tok/s | 17.19 tok/s |
| Laguna S | `capacity-v3` | 12/12 | 29,834 | same as TTFT | **1.971 s** | **15,134 tok/s** | **70.9 tok/s** | **21.14 tok/s** |
| DeepSeek 0731, low reasoning | `capacity-v4-reasoning` | 11/12 | 21,144 | 2.705 s | 29.106 s | 7,818 tok/s | 11.5 tok/s | 7.60 tok/s |
| Inkling Small, reasoning off | `capacity-v3` | 12/12 | 22,421 | same as TTFT | 2.838 s | 7,887 tok/s | 74.6 tok/s | 16.78 tok/s |
| Inkling Small, low reasoning | `capacity-v4-reasoning` | 12/12 | 21,879 | 2.789 s | 4.630 s | 7,844 tok/s | 73.5 tok/s | 34.55 tok/s |

DeepSeek's completion token count includes reasoning tokens, so its 11.5 tok/s
is a combined reasoning/visible rate rather than visible-only decode. Its one
failed request used the full 2,048-token completion allowance in reasoning and
returned no visible answer. The failure is preserved; it is not averaged away.

Longer-context checks stayed within each final served profile:

| Candidate | Completion | Actual prompt p50 | TTFT p50 | Effective prefill p50 | Decode p50 |
|---|---:|---:|---:|---:|---:|
| Qwen3.5 at requested 128K | 4/4 | 125,444 | 14.593 s | 8,570 tok/s | 65.0 tok/s |
| Nemotron Super at requested 60K | 4/4 | 53,820 | 5.579 s | 9,646 tok/s | 60.0 tok/s |
| Laguna S at requested 240K | 4/4 | 231,457 | 31.851 s | 7,252 tok/s | 66.0 tok/s |

Raw capacity artifacts: Qwen [32K](2026-08-01-dual-pro-tp2-campaign-evidence/qwen35-capacity-32k-c1-sequential.json)
and [128K](2026-08-01-dual-pro-tp2-campaign-evidence/qwen35-capacity-128k-c1.json);
Nemotron [32K](2026-08-01-dual-pro-tp2-campaign-evidence/nemotron3-super-capacity-32k-c1.json)
and [60K](2026-08-01-dual-pro-tp2-campaign-evidence/nemotron3-super-capacity-60k-c1.json);
Laguna [32K](2026-08-01-dual-pro-tp2-campaign-evidence/laguna-s-21-capacity-32k-c1.json)
and [240K](2026-08-01-dual-pro-tp2-campaign-evidence/laguna-s-21-capacity-240k-c1.json);
DeepSeek [final reasoning-aware 32K](2026-08-01-dual-pro-tp2-campaign-evidence/deepseek-v4-flash-0731-capacity-32k-c1-reasoning-v4-max2048.json).
Inkling [reasoning-off 32K](2026-08-01-dual-pro-tp2-campaign-evidence/inkling-capacity-32k-none.json)
and [low-reasoning 32K](2026-08-01-dual-pro-tp2-campaign-evidence/inkling-capacity-32k-low.json).
Earlier DeepSeek capacity files in the packet are protocol-calibration attempts,
not comparison rows.

## Troubleshooting that changed the durable surface

The campaign fixed product gaps rather than carrying private one-off launch
commands forward:

- multi-GPU Docker device-request quoting and a portable container-relative
  `CUDA_VISIBLE_DEVICES=0,1` contract;
- managed load fail-fast when an owned container exits before readiness;
- exact cache-completeness preflight plus enforced offline serving;
- recipe-specific bounded startup timeouts;
- reasoning-aware capacity timing and combined visible/reasoning quality
  context budgeting;
- portable quality control-evidence references; and
- offline recipe inspection without weakening exact GPU-count validation at
  real load time.

Every diagnosed defect has a dated record under `.tickets/`. DeepSeek also
needed a pinned SGLang derived image because PyTorch symmetric-memory logits
gather raised SIGFPE through the WSL2 CUDA proxy. The patch disables only that
optimization and keeps the existing NCCL fallback. Missing DeepSeek FP8 KV
scaling factors remain a published accuracy caveat. Laguna's tokenizer-regex
warning was retained as non-blocking only after an exact differential check.

Inkling's exact native NVFP4 snapshot first exposed a missing `accelerate`
dependency and two revision-blind ModelOpt cache lookups. After those were
fixed, its upstream three-stage grouped GEMM exceeded the SM120 101,376-byte
shared-memory limit, and its interleaved activation path requested a Helion
configuration that the pinned image does not ship for SM120. The final derived
image uses two stages for the affected SM120 grouped GEMMs and the existing
two-stage Triton SiLU/multiply fallback for SM120 interleaved activations. It
also carries the same WSL2 logits-only symmetric-memory guard as DeepSeek.
These are narrowly gated compatibility fixes, not a claimed kernel tune or
speedup. Both ranks then loaded roughly 86 GiB of weights plus 3.14 GiB of
BF16 KV/SWA state and completed decode-graph warmup. The full startup chain is
retained in [compatibility evidence](2026-08-01-dual-pro-tp2-campaign-evidence/inkling-startup-compatibility.json).

Final mode restoration exposed two more lifecycle defects. Docker Desktop
could leave the model shutdown call attached long enough to block the mode
transaction, so exclusive-mode release now uses a bounded force-remove path.
The first healthy Omni restore then rolled back because the intentionally
exited router was still treated as a live admission plane requiring
authenticated readmission. The final managed retry reconciled router lifecycle
state, skipped readmission only for the stopped default router, and completed
with Omni healthy. Explicit router URLs and active routers still fail closed on
readmission errors.

## Decision boundary

These results establish that the declared model/configuration can run on both
PRO 6000 cards in exclusive TP=2 and describe its bounded functional, capacity,
and quality behavior. They do not establish that TP=2 is faster than the prior
single-card profiles: the engines, windows, and workload packets are not a
clean topology-only A/B. They also do not authorize promotion. Production
aliases remained unchanged, and the pre-campaign Omni-only state was restored
after the final lane. The independent [final-state artifact](2026-08-01-dual-pro-tp2-campaign-evidence/final-restore.json)
records split mode, healthy Omni as the only GPU owner, Primary and Inkling
absent, the router still exited, and no unresolved reservation.
