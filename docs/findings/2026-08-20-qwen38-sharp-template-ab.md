# Qwen3.8 27B Sharp v22.1 chat-template A/B on RTX 5090

**Date:** 2026-08-20

**Public worktree:** `codex/qwen38-nvfp4-5090-qualification` at
`b5ac1f297a396abde7dab68eed0ce6656f26c4ba` (clean at campaign start)

**Evidence:** `functional`, bounded diagnostic `quality`

**Decision:** `rejected`; `no-promotion`

**Measured hardware:** one NVIDIA GeForce RTX 5090, 32,607 MiB, sm_120

**PRO relationship:** `unrelated`

## Outcome

Do not adopt Sharp v22.1 for the pinned RadixArk Qwen3.8 27B NVFP4 RTX 5090
profile from this evidence. The template loaded correctly and passed the complete
thinking-disabled functional gate, but it did not improve the bounded
thinking-enabled MMLU-Pro diagnostic. It preserved the same 24/30 attempt result
and the same two reasoning-budget failures while increasing completion tokens
10.8% and mean latency 10.7%.

A separate thinking-disabled behavior diagnostic showed a smaller, favorable
efficiency signal: Sharp used 5.1% fewer completion tokens and was 5.0% faster.
That lane did not clear its deterministic contract, however. Stock passed 18/18;
Sharp passed 15/18 because its ambiguous-request response requested four specific
details without posing the literal question required by the check. The response
was cautious and usable, so this is a narrow behavioral mismatch rather than an
unsafe action, but the hard gate remains failed.

No direct alias, router configuration, serve promotion, or production workload
changed. The exact stock 128K qualification container was restored healthy at
the end.

## Immutable identity and recipe

- Model: `RadixArk/Qwen3.8-27B-NVFP4` at
  `554ebba9b5f1b79dc11246341960360e6ef05ef4`.
- Template: `peculiar-ragdoll/Qwen-Sharp-Chat-Templates` at
  `3dc34df52c63dd22ada21f96435e069deaa8d7da`; root template described by the
  publisher as Sharp v22.1.
- Runtime: `lmsysorg/sglang:qwen38-27b` at
  `sha256:506525a5907ea22c9d445afb7c03603959b912de034d86915cf17da814f1a124`;
  image-label engine revision `c4271c3fe1262fc2adbd162c33b25de5255251c5`.
- Weights/quantization: ModelOpt NVFP4 W4A4 with FP8 projections, BF16
  vision/MTP tensors, and FP8 E4M3 KV.
- Shape: TP=1, 131,072 served tokens, one running request, 2,048-token chunks,
  FlashInfer attention, disabled radix cache, one Mamba/GDN state slot, CPU
  multimodal feature transport, no MTP.
- Sharp changed only the served identity and explicit SGLang `--chat-template`
  path. The template was pulled at the exact revision through
  `anvil-serving models pull` into the same named Hugging Face cache.
- Managed recipe: [sharp-recipe.toml](2026-08-20-qwen38-sharp-template-ab-evidence/sharp-recipe.toml).

Managed startup logs reported the exact template path, detected an OpenAI-format
user-specified Jinja template, and auto-detected the `enable_thinking` toggle,
Qwen3 reasoning parser, and Qwen3 Coder tool parser. The bounded startup record is
[sharp-startup-proof.json](2026-08-20-qwen38-sharp-template-ab-evidence/sharp-startup-proof.json).

## Workloads and results

### Thinking-disabled functional gate

Sharp passed coding smoke, structured JSON, shared-prefix tools 20/20,
streaming tools, tool-result continuation, and the supported Responses subset.
Every check used a 256-token visible-answer budget, zero reasoning headroom, and
required no reasoning leakage. Raw artifact:
[sharp-preflight-thinking-disabled.json](2026-08-20-qwen38-sharp-template-ab-evidence/sharp-preflight-thinking-disabled.json).

### Thinking-enabled MMLU-Pro diagnostic

The repository's hash-pinned 10-item MMLU-Pro fixture ran three repetitions per
item. Each request received 256 visible-answer tokens plus 1,792 reasoning
headroom tokens. The harness sends that as one 2,048-token completion cap; it
does not hard-partition visible and hidden channels.

| Metric | Stock | Sharp v22.1 | Sharp delta |
|---|---:|---:|---:|
| Passed attempts | 24/30 | 24/30 | no change |
| Completion tokens | 24,138 | 26,736 | +10.8% |
| Reasoning characters | 69,798 | 66,570 | -4.6% |
| Visible characters | 8,868 | 17,424 | +96.5% |
| Mean attempt latency | 10,586.3 ms | 11,716.6 ms | +10.7% |

Both templates exhausted the full completion budget on all three repetitions of
the computer-science and engineering items, emitted no visible answer, and were
classified `reasoning_budget_exhausted`. The publisher's token-efficiency claim
therefore did not reproduce on this bounded profile.

Raw artifacts:
[stock](2026-08-20-qwen38-sharp-template-ab-evidence/stock-mmlu-pro-r3-thinking-enabled.json) ·
[Sharp](2026-08-20-qwen38-sharp-template-ab-evidence/sharp-mmlu-pro-r3-thinking-enabled.json).

### Thinking-disabled behavior diagnostic

Six cases covered concise factual prose, an availability caveat, code, system
prompt preservation, ambiguous-request handling, and multi-turn memory. Each ran
three times with a 256-token visible budget and no reasoning headroom. The suite
is [published here](2026-08-20-qwen38-sharp-template-ab-evidence/sharp-behavior-diagnostic.suite.json).

| Metric | Stock | Sharp v22.1 | Sharp delta |
|---|---:|---:|---:|
| Passed attempts | 18/18 | 15/18 | -3 attempts |
| Completion tokens | 1,596 | 1,515 | -5.1% |
| Visible characters | 7,017 | 6,723 | -4.2% |
| Mean attempt latency | 1,267.6 ms | 1,204.7 ms | -5.0% |

Sharp preserved the caller's system marker, returned the exact multi-turn
codename, and matched stock's code size. It was materially shorter on the system
prompt and ambiguous-request cases, but longer on the immutable-artifact answer.
Its ambiguous response correctly declined to guess and requested missing fields;
it nevertheless failed the declared literal-question-mark check.

Raw artifacts:
[stock](2026-08-20-qwen38-sharp-template-ab-evidence/stock-behavior-r3-thinking-disabled.json) ·
[Sharp](2026-08-20-qwen38-sharp-template-ab-evidence/sharp-behavior-r3-thinking-disabled.json).

The compact cross-lane record is
[comparison-summary.json](2026-08-20-qwen38-sharp-template-ab-evidence/comparison-summary.json).

## External source registry

| Source | Observed | Age | Evidence type | Relevance and decision impact |
|---|---|---|---|---|
| [Sharp template repository](https://huggingface.co/peculiar-ragdoll/Qwen-Sharp-Chat-Templates/commit/3dc34df52c63dd22ada21f96435e069deaa8d7da) | 2026-08-20 | current | community artifact / external prior | Exact tested template revision; publisher claims lower token use and improved bounded scores, but local evidence controls the decision. |
| [Sharp v22.1 template change](https://huggingface.co/peculiar-ragdoll/Qwen-Sharp-Chat-Templates/commit/435b6b8974eb6c16df0fd6e14c23054ba9eedfb5) | 2026-08-20 | current | community artifact | Identifies the v22.1 rebase and medium-default behavior; used to define the candidate lane. |
| [SGLang custom-template support](https://github.com/sgl-project/sglang/issues/6418) | 2026-08-20 | stale | official project issue | Confirmed the `--chat-template` operating seam; local managed startup logs proved application in the pinned runtime. |

The external benchmark plates are advisory-only. This campaign did not reproduce
Claw-Eval, SWE-bench-Live, or the publisher's complete MMLU-Pro procedure.

## Failures, friction, and caveats

- The generic benchmark inspector reports validation warnings for unrelated
  built-in suites recorded as `not_run` and missing aggregate chat timing fields.
  The complete external-suite attempts, finish reasons, content, usage, and
  per-attempt latency remain intact, but the artifacts are not promotion-grade.
- Thinking control is recorded as `requested_unverified` in both quality lanes.
  The separate preflight independently proved thinking-disabled behavior with no
  leaked reasoning; the thinking-enabled lane retains the weaker label.
- Both MMLU-Pro arms failed the same two items through reasoning-budget
  exhaustion. This is not a clean quality pass for either template.
- The machine-wide `anvil-serving` shim did not honor `recipe.serve.model_path`
  in its load preview. The verified qualification worktree's
  `python -m anvil_serving.cli` path did, so no incorrect container was started.
  The exact stock recipe was also restored through that verified checkout.
- The runtime repeated the known FP8-KV warning: absent scaling factors default
  to 1.0. This campaign does not prove equivalence to unquantized KV.
- The Sharp artifact remains cached and the recipe remains recorded for
  reproducibility. Neither state grants route or promotion authority.

## Decision and restoration

Sharp v22.1 is `rejected` for adoption on this exact Qwen3.8 27B NVFP4 RTX 5090
profile. The small thinking-disabled efficiency improvement does not outweigh a
failed behavior check plus worse thinking-enabled token and latency results.
Further work, if desired, should first verify reasoning-effort steering through
`chat_template_kwargs` and use a larger independently scored task suite.

Campaign close restored the exact starting direct candidate:
`RadixArk/Qwen3.8-27B-NVFP4-RTX5090-128K`, original served identity, original
container name, same pinned runtime/model revisions, healthy on the same
loopback port. Its post-restoration smoke, JSON, tools 20/20, streaming tools,
tool-result continuation, and Responses gate all passed with reasoning
forbidden; the artifact is
[stock-restoration-preflight.json](2026-08-20-qwen38-sharp-template-ab-evidence/stock-restoration-preflight.json).
Router and production aliases were never in scope. Promotion remains separately
human-gated.
