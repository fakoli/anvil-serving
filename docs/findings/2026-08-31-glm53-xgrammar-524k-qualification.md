# GLM-5.3-Flash 524K xgrammar fix-forward qualification

**Date:** 2026-08-31

**Measured hardware:** 2x NVIDIA RTX PRO 6000 Blackwell Max-Q, 96 GB each

**Topology:** exclusive TP=2/DCP=2 over PCIe under Windows 11, Docker Desktop,
and WSL2; no NVLink

**Decision:** qualify and forward-restore the 524,288-token DFlash2 K5 profile;
retain the former 1,048,576-token image and configuration as the first rollback

**License boundary:** the independent DFlash2 draft is CC-BY-NC-ND-4.0, so
this combined recipe remains evaluation/noncommercial without separate permission

## Outcome

The corrected profile satisfies the requested replacement envelope:

- 524,288 configured context, inside the required 250K-600K range;
- 2,493,817 reported KV-cache tokens, or 4.76 complete configured windows;
- two concurrent nominal 250K requests completed 2/2 with an 8,192-token API
  completion allowance;
- DFlash2 fixed K5 speculation is enabled and was compared with an otherwise
  matched no-speculation control;
- median decode improved from 42.61 to 83.08 tok/s at 4K (+95.0%) and from
  43.63 to a pooled 69.99 tok/s at 240K (+60.4%);
- the new 83.08 tok/s 4K result is 1.2% above the former live profile's
  82.1 tok/s, while the pooled 69.99 tok/s 240K result is 3.1% above its
  67.9 tok/s;
- strict JSON, long-context retrieval, tools 20/20, a tool after a 100K+
  prompt, streaming tools, tool-result continuation, Responses, image, OCR,
  bounded intelligence, session recall, and real routed clients passed; and
- an exact rollback to the retained 1M service, its direct gate, all router
  capabilities, and a real Pi tool turn passed before the 524K profile was
  restored and gated again.

The route advertises concurrency 16 for short-request scheduling, but that is
not presented as sixteen full-window requests. The measured long-context
claim is C2 at a nominal 250K target.

## Immutable target and runtime

| Component | Exact identity |
|---|---|
| Target | `wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1@319d66a8b53092b491f698440ecea781e4ddd4e4` |
| Draft | `incoai/GLM-5.3-Flash-DFlash2@dc77ff1c99eeb2df044ee3d4f0094eb033fee410` |
| Corrected runtime image | `anvil-glm53-xgrammar@sha256:4909e318ba1348a179824e210f90c268d6fc68e8b4e514af4782e26e6a1e5939` |
| Runtime source base | `vllm-project/vllm@487ecf187` |
| Served identity | `glm53-flash-exl3-k3-dflash2-k5-fp8-tp2-524k-vision-xgfix` |
| Selected recipe | [`glm53-flash-purtell-k3-dflash2-k5-fp8-524k-vision-xgrammar-sm120-tp2-wsl2-recipe.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-k3-dflash2-k5-fp8-524k-vision-xgrammar-sm120-tp2-wsl2-recipe.toml) |
| Matched control | [`glm53-flash-purtell-k3-nospec-fp8-524k-vision-xgrammar-sm120-tp2-wsl2-recipe.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-k3-nospec-fp8-524k-vision-xgrammar-sm120-tp2-wsl2-recipe.toml) |

Both arms keep the same target revision, derived runtime, TP/DCP layout,
524,288 context, FP8 DS-MLA target KV, maxseq16, 2,048-token scheduler batch,
vision limits, parser selection, memory policy, and WSL2 transport settings.
Only the served identity and DFlash2 K5 speculative configuration differ.

## Why a corrected runtime was required

The initial DFlash2 profile produced repeated structured-output failures after
the model's reasoning channel ended. The earliest actionable runtime error was
`Failed to advance FSM`, not a router or client failure. The fix-forward image
applies the official xgrammar reasoning-termination/reset correction and the
post-reasoning speculative-validation correction to the pinned runtime. The
source and hash gate are retained under
[`configs/runtime-patches/vllm/487ecf187-xgrammar-spec-reasoning-end/`](../../configs/runtime-patches/vllm/487ecf187-xgrammar-spec-reasoning-end/).

The corrected image then passed repeated JSON and tool generation without the
FSM error. This is a local qualification of the exact derived image; it is not
a claim that every vLLM build or GLM quant contains the fix.

## Feasibility and capacity

The hardware-first feasibility screen classified both arms as paper-feasible
benchmark survivors rather than assuming that aggregate 192 GB VRAM was one
unified pool. The engine subsequently reported 19.18 GiB of available KV
memory per rank and 2,493,817 aggregate KV tokens. That is enough for 4.76
complete 524,288-token windows before scheduling and output reserve effects.

The C2 gate sent two concurrent requests with 206,630 actual prompt tokens
each from a nominal 250K calibration target and an 8,192-token completion cap.
Both the no-speculation and DFlash2 arms completed 2/2. Output lengths varied,
so the C2 artifact is used as concurrency/capacity evidence, not as a clean
throughput comparison.

## Matched speculative-decoding A/B

| Requested context | No spec p50 decode | DFlash2 K5 p50 / pooled decode | Change | DFlash2 p50 TTFT |
|---:|---:|---:|---:|---:|
| 4K, c1, five requests | 42.61 tok/s | **83.08 tok/s** | **+95.0%** | 0.974 s |
| 240K, c1, five requests | 43.63 tok/s | 64.51 tok/s | +47.9% | 59.80 s |
| 240K repeat, c1, five requests | same control | 70.50 tok/s | repeat | 59.67 s |
| 240K, ten DFlash2 requests pooled | 43.63 tok/s | **69.99 tok/s** | **+60.4%** | run medians 59.67-59.80 s |

Decode uses API usage completion tokens after the first streamed token divided
by client-observed generation time and may include reasoning tokens. It is not
a server-side per-token trace. The retained two-run DFlash2 spread is reported
instead of selecting only the faster run.

## Functional, quality, and client gates

- Both matched arms passed 28/28 direct preflight observations: short coding,
  strict JSON, 206,296-actual-token retrieval from the nominal 250K target,
  tools 20/20, a structured tool after the long prompt, streaming tools,
  tool-result continuation, and Responses.
- The selected DFlash2 arm passed image understanding and verbatim OCR; video
  remains disabled.
- The selected arm passed three high-control context cases at 4K, 131K, and
  240K, intelligence 6/6, tool use 3/3, and single-request session recall 3/3
  with no retained failures. The request control was accepted but not
  independently proven by token-level reasoning telemetry, so the evidence
  says `requested_unverified` rather than overstating it.
- The restored route exposed the exact served identity, 524,288 context,
  8,192 maximum output, and the recorded public configuration fingerprint.
  All six LLM/vision aliases plus the two audio capabilities were reachable.
- Real OpenClaw and Hermes shell-tool continuations passed with the exact
  routed identity and no fallback. Pi 0.84.2 passed its normal extension-loaded
  PTY path with exactly one `read` tool call, exact recovery of an unseen
  marker, and zero error events.

Raw client evidence contains private fleet endpoints and remains in the
private operator evidence store. The public result above records only the
sanitized gate outcome.

## Rollback and fix-forward record

The rollback drill quiesced and drained the 524K tier, unloaded it through the
managed recipe surface, loaded the retained digest-pinned 1M profile, and
installed the retained router configuration atomically. The old profile's
first generic 256-token probe exhausted its visible-answer allowance in
reasoning; rerunning its already-qualified 512-visible plus 4,096-reasoning
budget passed smoke, JSON, tools 20/20, streaming tools, tool-result
continuation, and Responses. All eight capabilities and a real Pi tool turn
then passed. The old tier was quiesced, drained, and unloaded before the new
524K profile was loaded and admitted again.

The live campaign also exposed product defects that were fixed in the managed
surfaces rather than worked around silently:

- recipe-owned GPU UUIDs were not represented in mode ownership;
- routed evaluation could not run on the client-owning host or resolve its
  credential through the normal envfile chain;
- OpenClaw's canonical and generated service environments could drift during
  credential rotation and a restart failure was not durably retryable;
- router-runtime fleet status recursively attempted Docker dispatch from
  inside the router container; and
- an ambient credential could override the explicitly selected Compose
  envfile during router recreation.

Each correction has focused regression coverage. The final router image and
Mini package were rebuilt from the corrected source before the forward client
gate.

## Current external priors

External sources were treated as recipe leads, not local proof:

- the exact target and draft revisions are retained at their
  [Hugging Face target](https://huggingface.co/wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1/tree/319d66a8b53092b491f698440ecea781e4ddd4e4)
  and [draft](https://huggingface.co/incoai/GLM-5.3-Flash-DFlash2/tree/dc77ff1c99eeb2df044ee3d4f0094eb033fee410)
  repositories;
- a 2026-08-27
  [four-PRO Reddit report](https://www.reddit.com/r/LocalLLaMA/comments/1vzw57i/glm53flash_fp8_on_4_x_rtx6000_pro/)
  described 160-230 tok/s generation and 4.65 mean speculative acceptance,
  but its TP=4 FP8 setup is not hardware- or recipe-matched;
- a 2026-08-28
  [dual-PRO discussion](https://www.reddit.com/r/LocalLLaMA/comments/1w0oolk/anyone_running_glm53_flash_on_2x_rtx_pro_6000_96gb/)
  highlighted fit, SM120, and runtime uncertainty, reinforcing the need for
  immutable local qualification; and
- a 2026-08-29
  [local-runtime thread](https://www.reddit.com/r/LocalLLaMA/comments/1w1qp10/is_anyone_successfully_running_glm_53_flash/)
  contained mixed engines, quants, and hardware and therefore remains
  practitioner context only.

Searches of X did not yield an inspectable, attributable recipe source during
this run. No X claim is used in the decision.

## Evidence

The public raw artifacts and measurement notes are indexed in the
[evidence README](2026-08-31-glm53-xgrammar-524k-qualification-evidence/README.md).
Private rollback, router, and real-client artifacts remain in the private
operator repository or operator evidence root.
