# Agents-A1 FP8 Primary promotion

**Observed:** 2026-07-29

**Host:** Fakoli Dark, one NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation
Edition (96 GB, sm_120)

**Decision:** promote Agents-A1 official FP8 to Primary with thinking disabled;
retain Qwen3.5 122B NVFP4 as the immediate managed rollback.

## Why the decision changed

The earlier [262K head-to-head](2026-07-29-agents-a1-qwen-262k-head-to-head.md)
established that Agents-A1 used 51.78% less model memory than Qwen, retained
3.75 times as much KV memory, cut measured 240K TTFT by 52.15%, decoded 2.59
times faster at that lane, and was the only exact runtime that delivered the
video corpus. That campaign deliberately stopped short of promotion because
Agents-A1 had not repeated the complete protocol-v3 quality suite at 262K.

The missing suite was rerun three times with thinking disabled and passed every
hard serving-contract assertion. The operator then supplied the separate human
promotion authorization. This is an operational Primary decision, not a claim
of general intelligence superiority.

## Exact promoted profile

| Field | Value |
|---|---|
| Model | `InternScience/Agents-A1-FP8` |
| Revision | `4d7d59380f327b76e73bc71f40e0c589ad0ca1d5` |
| Served name | `agents-a1-fp8-mm-262k` |
| Engine | vLLM `0.23.1rc1.dev1327+gf25953cc5` |
| Image | `vllm/vllm-openai:nightly-f25953cc59f9b4ba9b04b16228d2b86dcfbcbdb1` |
| Image digest | `sha256:212a1bd7b4267c604408d17dc0048ef152101bc67fbe6ba8567899fc1f227bcd` |
| Quantization / KV | compressed-tensors FP8 / FP8 |
| Context / admission | 262,144 tokens / one sequence |
| Media limits | four images and one video |
| Thinking contract | disabled and not caller-overridable at the router |

The generated hardware-specific MoE tune remains rejected and inert. Its
matched three-run A/B regressed primary-lane throughput by 1.399%, so the
promoted recipe keeps vLLM's default selection.

## Qualification gate

The 240K functional preflight passed smoke, deterministic JSON, retrieval, and
20/20 tool calls. The full protocol-v3 run then passed at a required 100% rate:

| Gate | Result |
|---|---:|
| 32K, 128K, and 240K context retrieval | 3/3 lanes |
| Automatic tool calling | 3/3 |
| Session recall | 3/3 |
| Unified diff | 3/3 |
| Timeout triage | 3/3 |
| Visible answers / allowed finish state | all pass |
| Reasoning leakage while disabled | none |

The repeated run used 256 visible-answer tokens, zero reasoning headroom,
three repetitions, and the exact thinking-disabled control artifact.
Client-observed timing was:

| Context lane | TTFT | End to end |
|---|---:|---:|
| 32K | 1.571 s | 1.918 s |
| 128K | 12.839 s | 13.212 s |
| 240K | 35.209 s | 35.615 s |

Across the suite, derived decode throughput was 169.19 tok/s p50 and 181.44
tok/s p95; effective prefill was 10,601 tok/s p50. These are client-observed
request metrics, not isolated kernel measurements.

## Multimodal caveat

The retained strict corpus result remains 28/30 for both BF16 and official
FP8: 12/12 images, 4/4 mixed-media attempts, and 12/14 video attempts. Both
failed event-localization assertions named the exact time interval but omitted
the required word `alert`; matched BF16 output means this was not an observed
FP8 quantization regression. The promotion accepts that documented prompt-
contract caveat. Direct and isolated routed `video_url` handling, malformed
media, media admission, tools with video, and streaming video were already
verified.

## Managed transition and rollback

The qualification container was removed after an exact identity check.
`serves promote agents-a1-fp8-primary` then quiesced and drained the live
Primary tier, started the exact promoted recipe, reran its thinking-disabled
functional gate, installed the pinned router configuration, checked exact
model identity, and readmitted the tier. Qwen3.5 remains available through the
`primary-qwen35-rollback` managed service and matching rollback router config.
The post-promotion `llm.primary` route then passed smoke, deterministic JSON,
and 20/20 tool calls with no reasoning leakage.

No raw Docker lifecycle command was used. Promotion evidence and the terminal
serve/router state are retained with the raw benchmark artifacts.

## Evidence

- [Evidence manifest](2026-07-29-agents-a1-primary-promotion-evidence/README.md)
- [240K functional preflight](2026-07-29-agents-a1-primary-promotion-evidence/preflight-240k-thinking-disabled.json)
- [Repeated protocol-v3 quality](2026-07-29-agents-a1-primary-promotion-evidence/quality-protocol-v3-262k-thinking-disabled.json)
- [Candidate startup log](2026-07-29-agents-a1-primary-promotion-evidence/candidate-startup.log)
- [Promotion transaction](2026-07-29-agents-a1-primary-promotion-evidence/promotion-transaction.stdout.log)
- [Routed Primary preflight](2026-07-29-agents-a1-primary-promotion-evidence/routed-primary-preflight.json)
