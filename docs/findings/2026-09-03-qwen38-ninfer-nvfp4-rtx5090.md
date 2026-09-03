# Qwen3.8 27B NInfer NVFP4 qualification on RTX 5090

**Date:** 2026-09-03

**Decision:** preferred measured RTX 5090 text/tools performance challenger;
`no-promotion`

**Measured hardware:** one NVIDIA GeForce RTX 5090, 32,607 MiB, Blackwell
`sm_120`

**Topology:** isolated direct managed candidate lane; no router alias, client
catalog, or persistent serving assignment changed

## Outcome

The current NInfer-published NVFP4 artifact with its integrated MTP3 proposal
head is the fastest locally measured Qwen3.8 27B text/tools configuration on
this RTX 5090 while retaining a large context envelope. In a matched five-
request 4K/C1 comparison, MTP3 kept median TTFT near-flat at 0.430 versus
0.421 seconds, increased median decode from 75.3 to 165.9 tok/s, and reduced
median E2E from 1.085 to 0.720 seconds.

The selected arm also returned the exact marker from a nominal 244,480-token
fixture containing 201,746 API-reported prompt tokens while accepting an
8,192-token completion cap. The request completed in 70.4 seconds. Smoke,
structured JSON, C1 tools, streaming tools, tool-result continuation, and the
bounded repeated coding/triage/tool suite passed.

This is not promotion evidence. Both arms completed only 17/20 requests in a
simultaneous shared-prefix tool burst; the other three received explicit
`429 server_overloaded` responses under the C1 scheduler contract. MTP3 also
left 2,354 MiB free by `nvidia-smi`, above this isolated campaign's declared
1 GiB model-only floor but below the ordinary 3 GiB reserve. Routed/client,
multimodal, thinking-enabled, sustained thermal, and broad agentic/SWE gates
remain open. The exact incumbent was restored and passed a fresh smoke check.

## Result card

| Field | Measured result |
|---|---|
| Model | `neroued/Qwen3.8-27B-nvfp4-NInfer@204e3d92` |
| Runtime | NInfer `e3aeaf8c`, source-built on pinned CUDA 13.1.2 base |
| Profile | NVFP4/row-scaled FP8 target, INT8 KV, MTP3, 252,928 context, C1, thinking disabled |
| 4K/C1 MTP3 | TTFT p50/p95 0.430/0.648 s; E2E p50/p95 0.720/0.919 s; decode p50/p95 165.9/184.3 tok/s |
| Matched no-spec | TTFT p50 0.421 s; E2E p50 1.085 s; decode p50 75.3 tok/s |
| Context | 201,746 actual prompt tokens from nominal 244,480 fixture; 8,192 completion cap; marker pass in 70.4 s |
| Behavior | smoke, JSON, C1/streaming/continuation tools, coding 3/3, triage 3/3, repeated tools 3/3 pass |
| Failed gate | shared-prefix tool burst 17/20 on each arm; three explicit C1 queue-overload responses |
| Memory | no spec 28,542 MiB used / 3,646 free; MTP3 29,834 used / 2,354 free |
| Decision | preferred direct text/tools performance challenger; no promotion |

## Immutable identity

| Component | Exact identity |
|---|---|
| Base model | `Qwen/Qwen3.8-27B@1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0` |
| Quant source | `unsloth/Qwen3.8-27B-NVFP4@60e813d4dbbdc5d64cf3f5a8caf2897bedf03679` |
| NInfer artifact | `neroued/Qwen3.8-27B-nvfp4-NInfer@204e3d92c30d9d05f3300d2f52e443ad1edf6ddf`, `qwen3_8_27b_nvfp4.ninfer`, 21,492,695,040 bytes, SHA-256 `bb3360522a06e136e0367f5703414d26272b7285c8a6ab6194135c17dbd81b32` |
| Runtime source | `Neroued/ninfer@e3aeaf8c0b6f83ae8f051780f0ad0d995d5a7bef` |
| CUDA base | `nvidia/cuda:13.1.2-devel-ubuntu24.04@sha256:b9f64abf7226fdb3463ca202bc99878ec847171e6c5f77bd34c8d1403fbf1eca` |

The [managed recipe](../../configs/qwen38-27b-ninfer-nvfp4-rtx5090-252k-recipes.toml)
checks the exact artifact SHA before launch and verifies the runtime checkout.
The first run source-builds NInfer into a revision-specific named volume. Its
Ubuntu package resolution is not digest-pinned, so this is an intake recipe,
not a promotion-grade baked runtime image.

## Matched performance

| Arm | Run state | TTFT p50 | E2E p50 | Decode p50 | Effective prefill p50 | Result |
|---|---|---:|---:|---:|---:|---|
| no spec | first measured | 0.421 s | 1.085 s | 75.3 tok/s | 8,580 tok/s | 5/5 |
| MTP3 | first measured | 0.430 s | 0.720 s | 165.9 tok/s | 8,410 tok/s | 5/5 |
| no spec | immediate repeat | 0.436 s | 1.034 s | 75.3 tok/s | 8,288 tok/s | 5/5 |
| MTP3 | immediate repeat | 0.444 s | 0.730 s | 184.2 tok/s | 8,137 tok/s | 5/5 |

On the first matched runs, MTP3 raised median decode 120.4% and reduced median
E2E 33.7%, while median TTFT rose 2.0% and effective prefill fell 2.0%. The
immediate-repeat pair retained the conclusion: 144.6% higher median decode and
29.4% lower median E2E. The harness reported zero cached prompt tokens in all
four capacity artifacts, so this comparison does not depend on a warm-prefix
shortcut. Each row contains only five requests; p95 is descriptive, not a
population estimate.

## Functional, context, and quality gates

| Gate | No spec | MTP3 | Interpretation |
|---|---:|---:|---|
| thinking-disabled coding smoke | pass | pass | exact short visible answer; no reasoning channel |
| structured JSON | pass | pass | parsed declared keys |
| C1 tool call | 1/1 | 1/1 | exact function and argument |
| streaming tool call | pass | pass | valid SSE completion and tool arguments |
| tool-result continuation | pass | pass | exact tool result retained |
| shared-prefix tool burst | 17/20 | 17/20 | three explicit `429 server_overloaded` admissions per arm |
| nominal 244,480 retrieval, 64-token cap | pass, 70.1 s | pass, 70.2 s | 201,746 API-reported prompt tokens |
| nominal 244,480 retrieval, 8,192-token cap | not rerun | pass, 70.4 s | selected-arm full-reserve admission proof |
| repeated coding edit | 3/3 | 3/3 | deterministic diff checks |
| repeated timeout triage | 3/3 | 3/3 | deterministic diagnosis/fix checks |
| repeated tool call | 3/3 | 3/3 | exact function and arguments |

NInfer reported a 252,928-token INT8 KV pool at startup. The deepest retained
request contains 201,746 API-reported prompt tokens; the fixture's nominal
244,480 estimate is therefore not relabeled as actual tokens.

## Feasibility and runtime safety

The preregistered interval screen classified the candidate as a
`benchmark-survivor`. It deliberately reduced the ordinary 3 GiB campaign
reserve to a 1 GiB model-only floor for this isolated experiment. No-spec used
28,542 MiB and left 3,646 MiB free. MTP3 used 29,834 MiB and left 2,354 MiB
free both after startup and after the longest request. NInfer's own startup log
reported 1.55 GiB runtime-free memory for MTP3. No OOM, CUDA error, crash,
restart, or request loss occurred outside the explicit 429 admissions.

## Decision

- Retain NInfer NVFP4 MTP3 as the preferred **direct text/tools performance
  challenger** on one RTX 5090. It materially improves decode and end-to-end
  latency over its exact no-spec control and the previously measured GGUF
  Q4_0/MTP3 104.1 tok/s result.
- Keep the Unsloth GGUF Q4_K_XL/MTP3 incumbent and its prior broad image/OCR,
  agentic, endurance, routed-short-path, and deeper actual-prompt evidence.
  This campaign did not replace those capability claims.
- Do not promote or reroute NInfer until a digest-pinned baked runtime replaces
  floating apt resolution, the 20-request admission gate is resolved or the
  contract explicitly stays C1, the normal reserve policy is adjudicated, and
  routed/client plus broader agentic/SWE gates pass.
- Do not claim multimodal support from the artifact alone. The evaluated
  profile was text/tools only.

## Restoration

Both NInfer containers were removed through the managed recipe surface. The
starting `unsloth/Qwen3.8-27B-UD-Q4_K_XL-Native-MTP3-262K` revision and exact
llama.cpp image digest were reloaded. Managed status returned healthy,
`/v1/models` returned the 262,144-token incumbent identity, and a fresh
thinking-disabled smoke passed. Candidate artifact and revision-specific build
caches were retained; no route or client configuration changed.

## Evidence

- [Evidence bundle and role ledger](2026-09-03-qwen38-ninfer-nvfp4-rtx5090-evidence/README.md)
- [Machine-readable decision summary](2026-09-03-qwen38-ninfer-nvfp4-rtx5090-evidence/summary.json)
- [Matched no-spec capacity](2026-09-03-qwen38-ninfer-nvfp4-rtx5090-evidence/capacity-nospec-4k-c1.json)
- [Matched MTP3 capacity](2026-09-03-qwen38-ninfer-nvfp4-rtx5090-evidence/capacity-mtp3-4k-c1.json)
- [Full-reserve context gate](2026-09-03-qwen38-ninfer-nvfp4-rtx5090-evidence/preflight-mtp3-needle-244k-output-reserve-8192.json)
- [MTP3 quality artifact](2026-09-03-qwen38-ninfer-nvfp4-rtx5090-evidence/quality-mtp3.json)
- [Feasibility result](2026-09-03-qwen38-ninfer-nvfp4-rtx5090-feasibility.md)
- [NInfer-published artifact](https://huggingface.co/neroued/Qwen3.8-27B-nvfp4-NInfer/tree/204e3d92c30d9d05f3300d2f52e443ad1edf6ddf)
- [Exact NInfer runtime source](https://github.com/Neroued/ninfer/tree/e3aeaf8c0b6f83ae8f051780f0ad0d995d5a7bef)
