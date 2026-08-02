# DeepSeek V4 Flash 0731 650K/1M Pi qualification

**Date:** 2026-08-02

**Host:** Fakoli Dark, Windows 11 + Docker Desktop/WSL2

**Measured hardware:** 2x RTX PRO 6000 Blackwell Max-Q, exclusive TP=2 over PCIe without NVLink

**Use case:** one Pi coding user with a small burst of agents and tool calls
**Decision:** 650K/maxseq16 is the preferred everyday Pi experiment; 1M/maxseq16 is the preferred explicit deep-session experiment; 1M/maxseq4 is a passing lower-admission alternative; 1M/maxseq1 is rejected for agentic use; all remain `no-promotion`

## What succeeded

Moving Windows display output from the RTX PRO cards to the AMD iGPU changed
the practical ceiling. The same pinned model, image, tensor-parallel topology,
and 0.975 vLLM memory ceiling that previously missed maxseq16 startup by a
narrow margin now ran the upstream 128K/maxseq16 shape, then extended to a
GPU-only 650K window and finally a GPU-only 1M window. No CPU KV offload was
needed for any of these runs.

The final 1M/maxseq16 profile did more than reach health. It recovered the
correct value from an approximately 985K-token prompt, passed coding, JSON,
three overlapping typed tool calls, streaming tools, tool-result continuation,
and the Responses API, then answered another coding request after the near-limit
probe. At ordinary 32K Pi context it delivered 129.0 tok/s median decode. This
is a successful local translation of the community envelope to dual SM120
Max-Q cards under WSL2, with a reproducible managed recipe and retained failure
evidence from the configurations that did not work.

## Question and immutable identity

The experiment tested the community claim that the r16 B12X/DSpark recipe can
move from 128K and 8,192 batched tokens to approximately 650K/4,096 and
1M/2,048. The source explicitly presents those larger windows as community
envelopes rather than certified gates. Local qualification therefore required
real near-limit retrieval and Pi protocol tests.

All profiles used `deepseek-ai/DeepSeek-V4-Flash-0731` revision
`9e165c30e2704aec5d9d593cce3eebd58bbef1cb`, image digest
`sha256:48518e91cf87dd0c0483c76ff86e81dfc0f46de7e364b46f7a82c481ce08188f`,
B12X W4A8 NVFP4 MoE/FP8 dense kernels, FP8 MLA KV, InstantTensor, TP=2,
DCP=1, DSpark fixed-depth K5, `GPU_MEMORY_UTILIZATION=0.975`, and no CPU KV
offload. Windows display output was on the AMD iGPU.

| Profile | Context | Batched tokens | Max sequences | Purpose |
|---|---:|---:|---:|---|
| Upstream-default retry | 131,072 | 8,192 | 16 | Confirm that moving display output off the PRO cards removes the prior startup shortfall |
| Pi default candidate | 650,000 | 4,096 | 16 | Preserve responsiveness and small agent/tool bursts while extending GPU-only context |
| 1M single-sequence probe | 1,000,000 | 2,048 | 1 | Test maximum GPU-only retrieval with minimal admission |
| 1M Pi retry | 1,000,000 | 2,048 | 4 | Preallocate a safer B12X workspace and allow a small agent/tool burst |
| 1M full-admission retry | 1,000,000 | 2,048 | 16 | Test the upstream admission envelope after maxseq4 passed |

The exact local recipes are
[`maxseq16-128k`](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq16-128k-recipe.toml),
[`maxseq16-650k`](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq16-650k-recipe.toml),
[`maxseq1-1m`](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq1-1m-recipe.toml),
[`maxseq4-1m`](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq4-1m-recipe.toml),
and [`maxseq16-1m`](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq16-1m-recipe.toml).

## Results

### Context and Pi workflow gates

| Profile | Near-limit retrieval | Pi protocol result |
|---|---|---|
| 128K/maxseq16 | Functional preflight passed; 16-request/c16 4K run completed 16/16 | Smoke, JSON, typed tools, streaming tools, tool-result continuation, and Responses passed |
| 650K/maxseq16 | ~640K needle passed in **120.6 s** | All Pi checks passed, including a three-request shared-prefix tool burst |
| 1M/maxseq1 | ~985K needle passed in **242.3 s** | **Failed**: only 1/3 tool calls completed before the engine died; streaming tools, tool-result continuation, and Responses then returned HTTP 500 |
| 1M/maxseq4 | ~985K needle passed in **235.7 s** | All Pi checks passed, including the same three-tool crash reproducer |
| 1M/maxseq16 | ~985K needle passed in **237.7 s**; a post-probe coding request also passed | All Pi checks passed, including the same three-tool crash reproducer |

The maxseq1 root cause was a fatal B12X workspace mismatch: the workspace was
locked at 514.25 MB, then compressed MLA required 873.62 MB and could not grow
after locking. Raising admission to four changed the graph/workspace envelope
and eliminated the failure in the matched gate.

### Matched 32K single-user performance

Each row used three requests, concurrency one, 32,768 planned context tokens,
1,024 maximum output tokens, low reasoning, and the same endpoint harness.

| Profile | Completed | TTFO p50 | TTFT p50 | E2E p50 | Prefill p50 | Decode p50 | Aggregate output |
|---|---:|---:|---:|---:|---:|---:|---:|
| 650K/maxseq16 | 3/3 in 16.6 s | 2.66 s | 5.46 s | 5.59 s | 8,793 tok/s | **141.6 tok/s** | 67 tok/s |
| 1M/maxseq1 | 3/3 in 51.1 s | 3.25 s | 10.52 s | 13.20 s | 7,195 tok/s | **13.0 tok/s** | 10 tok/s |
| 1M/maxseq4 | 3/3 in 15.4 s | 2.96 s | 5.00 s | 5.13 s | 7,901 tok/s | **119.9 tok/s** | 50 tok/s |
| 1M/maxseq16 | 3/3 in 14.9 s | 2.92 s | 4.58 s | 4.81 s | 8,012 tok/s | **129.0 tok/s** | 52 tok/s |

The maxseq1 profile is both functionally unsafe for agent bursts and an order
of magnitude slower in median decode than the 650K profile. The maxseq4 retry
restores normal short-context responsiveness. Maxseq16 also passes, improves
median decode from 119.9 to 129.0 tok/s, and preserves 1,715,610 KV tokens, so
it becomes the preferred 1M experimental profile. The 650K profile remains
about 10% faster in median decode.

## Memory and reserve caveat

The engine reported 996,983 KV tokens for 650K/maxseq16, 1,907,945 for
1M/maxseq1, 1,849,667 for 1M/maxseq4, and 1,715,610 for 1M/maxseq16. Moving the
Windows display to the AMD iGPU gave each PRO card 93.54/95.59 GiB free at
engine startup, enough for the previously failing maxseq16 graph envelope.

This qualification intentionally waived the separate 3 GiB reported-free
VRAM acceptance gate; it did not remove vLLM's 0.975 runtime ceiling. The
measured free memory after the 650K workload was 797/805 MiB. After the 985K
maxseq4 probe it was 207/209 MiB; maxseq16 retained 339/335 MiB after its 985K
probe and Windows had 50.55 GiB physical RAM free. These are experimental
capacity results, not evidence for production promotion or for raising
utilization further.

## Decision

- **Promotion recommendation:** promote **1M/maxseq16** to the dedicated Pi
  experimental profile. It gives one coding user the full window, retains
  small agent/tool bursts, and gives up only about 9% median decode versus the
  650K profile in the matched 32K run.
- Keep **650K/maxseq16** as the managed rollback and faster everyday option.
  Its near-limit retrieval takes about two minutes rather than four and its
  matched median decode is 141.6 tok/s.
- Retain **1M/maxseq4** as the passing lower-admission comparison and rollback
  for the experimental 1M lane.
- Reject **1M/maxseq1** for Pi: a single user still creates overlapping agent
  and tool work, and this profile crashed under that realistic burst.
- This recommendation is for a Pi-specific experimental route, not replacement
  of the current production Primary. The route change remains a separate,
  human-gated operator action and explicitly accepts the sub-1-GiB reported-free
  VRAM observation. Broader repeated coding/session quality and sustained
  multi-turn recovery remain follow-up evidence, not claims of this run.

## Evidence

- [128K maxseq16 functional gate](2026-08-02-deepseek-r16-maxseq16-igpu-evidence/preflight-128k-maxseq16-low.json)
- [128K maxseq16 c16 capacity](2026-08-02-deepseek-r16-maxseq16-igpu-evidence/capacity-4k-c16-r16-low.json)
- [650K near-limit retrieval](2026-08-02-deepseek-r16-maxseq16-igpu-evidence/preflight-650k-maxseq16-needle640k-low.json)
- [650K Pi protocol gate](2026-08-02-deepseek-r16-maxseq16-igpu-evidence/preflight-650k-maxseq16-pi-low.json)
- [650K matched 32K capacity](2026-08-02-deepseek-r16-maxseq16-igpu-evidence/capacity-32k-c1-r3-650k-low.json)
- [1M/maxseq1 near-limit retrieval](2026-08-02-deepseek-r16-maxseq16-igpu-evidence/preflight-1m-maxseq1-needle985k-low.json)
- [1M/maxseq1 failed Pi gate](2026-08-02-deepseek-r16-maxseq16-igpu-evidence/preflight-1m-maxseq1-pi-low.json)
- [1M/maxseq1 matched 32K capacity](2026-08-02-deepseek-r16-maxseq16-igpu-evidence/capacity-32k-c1-r3-1m-low.json)
- [1M/maxseq4 near-limit retrieval](2026-08-02-deepseek-r16-maxseq16-igpu-evidence/preflight-1m-maxseq4-needle985k-low.json)
- [1M/maxseq4 Pi protocol gate](2026-08-02-deepseek-r16-maxseq16-igpu-evidence/preflight-1m-maxseq4-pi-low.json)
- [1M/maxseq4 matched 32K capacity](2026-08-02-deepseek-r16-maxseq16-igpu-evidence/capacity-32k-c1-r3-1m-maxseq4-low.json)
- [1M/maxseq16 near-limit retrieval](2026-08-02-deepseek-r16-maxseq16-igpu-evidence/preflight-1m-maxseq16-needle985k-low.json)
- [1M/maxseq16 Pi protocol gate](2026-08-02-deepseek-r16-maxseq16-igpu-evidence/preflight-1m-maxseq16-pi-low.json)
- [1M/maxseq16 matched 32K capacity](2026-08-02-deepseek-r16-maxseq16-igpu-evidence/capacity-32k-c1-r3-1m-maxseq16-low.json)
- [1M/maxseq16 post-985K coding smoke](2026-08-02-deepseek-r16-maxseq16-igpu-evidence/preflight-1m-maxseq16-post985k-smoke-low.json)
- [Runtime and restoration observations](2026-08-02-deepseek-r16-maxseq16-igpu-evidence/runtime-observations.json)
- [Community recipe prior](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/ds4dspark-v20.md)

All recipe containers were removed after the run. Both GPUs returned to 0 MiB
reported use, and the managed shared-memory inspector reported zero files and
zero reclaimable bytes.
