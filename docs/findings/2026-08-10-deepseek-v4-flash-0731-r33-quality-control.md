# DeepSeek V4 Flash 0731 r33 quality-first control

**Date:** 2026-08-10

**Evidence:** local `functional`, `capacity`, bounded `quality`; one retained harness caveat

**Decision:** priority `challenger`, `no-promotion`; 393K FP8-KV recipe translated but not loaded

## Outcome

The digest-pinned r33 B12X runtime served the exact released DeepSeek V4 Flash
0731 checkpoint on two equal RTX PRO 6000 Blackwell Max-Q cards in exclusive
TP=2. The target-only 131,072-token control passed the complete functional
preflight, repeated high-reasoning coding/session/tool checks, prompt-contract
fingerprints, and an API-reported 119,503-prompt-token capacity request. The
largest request measured 17.445 seconds TTFT, 7,537 effective prefill tok/s,
and 73.86 decode tok/s. A short coding smoke passed after every context probe.

This is a local qualification result for the 131K control. It does not prove
that the prepared 393,216-token configuration loads or that a request above
300,000 prompt tokens succeeds.

## Immutable identity and configuration

- Checkpoint: `deepseek-ai/DeepSeek-V4-Flash-0731` revision
  `9e165c30e2704aec5d9d593cce3eebd58bbef1cb`.
- Image:
  `voipmonitor/vllm@sha256:fdde59fed7f9fc12f9fd5ef1b3b3ea8d5097bf10ebad54b348497102c3a83f82`.
- Runtime:
  `0.11.2.dev280+gilded.gnosis.v20.vllmfa13d33.b12x06db0f4.fi1ac6942.cu132.20260809.r33`.
- Hardware: 2× RTX PRO 6000 Blackwell Max-Q, 96 GB each, PCIe without NVLink.
  The 192 GB aggregate is TP-sharded capacity, not unified memory.
- Control: target-only `MODE=dspark-mtp0`, no speculative decoding, B12X W4A8
  with NVFP4 MoE weights and FP8 activations/dense path, FP8 DS-MLA KV, TP=2,
  DCP=1, `max_model_len=131072`, `max_num_seqs=1`,
  `max_num_batched_tokens=8192`, and `gpu_memory_utilization=0.975`.
- Memory policy: exclusive AI-only. There was no separate video-workload
  reserve or hard reserve gate. The vLLM utilization fraction remains runtime
  allocation space rather than a video reservation.

The portable control is
[the 131K recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r33-b12x-nospec-maxseq1-131k-recipe.toml).

## Gates and measurements

| Gate | Result |
|---|---|
| Cold managed start | Passed health in 323.5 seconds |
| Reasoning prompt contract | Low/high/max fingerprints passed at 6/85/98 prompt tokens |
| Functional preflight | 6/6: smoke, JSON, 20/20 tool calls, streaming tool, tool-result continuation, Responses API |
| Repeated high-reasoning quality | Intelligence 2 checks × 3/3; session recall 3/3; tools 3/3; 32K context pass |
| Near-limit capacity | 119,503 actual prompt tokens; 17.445 s TTFT; 17.886 s E2E; 7,537 effective prefill tok/s; 73.86 decode tok/s |
| GPU KV allocation | 15.27 GiB, 283,917 tokens, reported 2.17× capacity at the configured 131,072-token envelope |
| Native-offload cleanup prerequisite | Managed absent-container path reclaimed exactly one unmapped 4,096-byte fixture through the guarded two-scan path; clean postcondition |
| Route and promotion | No route or promotion changed |

The engine reported approximately 75.64 GiB of model weights per TP rank.
Both ranks loaded and remained healthy after the qualification requests.

## Retained failure and harness defect

The first 120,000-target context attempt returned HTTP 400. The endpoint
remained healthy, and later requests passed at 118,830 and 119,503 actual
prompt tokens. A replay labeled as a 120,000 target also passed, but the API
reported only 88,939 prompt tokens. Other requested targets were likewise
non-monotonic: 117,500 produced 87,207 actual tokens while 118,500 produced
118,830.

The HTTP 400 remains a real failed attempt, but the later evidence does not
support classifying it as persistent model instability. More importantly, the
harness target label is not a capacity measurement. Public context claims in
this finding use API-reported `usage.prompt_tokens`. The calibration defect is
tracked in
[the context-target ticket](https://github.com/fakoli/anvil-serving/blob/main/.tickets/2026-08-10-quality-context-target-calibration.md).

## Why the next arm retains FP8 KV

The quality-first order separates weight precision from cache precision. The
r33 control already exercises a mixed B12X path whose MoE weights use NVFP4,
while its DS-MLA KV cache remains FP8. Moving the cache to NVFP4 would add a
second lossy variable before an exact-checkpoint, matched-prompt quality A/B
exists. Community speed or fit reports cannot establish cache-quality
equivalence.

The measured GPU KV pool holds 283,917 tokens, below both the requested
300,000-token floor and the 393,216-token target. Removing a video reserve
cannot bridge that gap because no such gate was active. The translated
[393K recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r33-b12x-nospec-maxseq1-393k-recipe.toml)
therefore retains FP8 DS-MLA KV and adds a 16 GiB native host tier. It disables
filesystem L2, admits one sequence, and leaves speculative decoding off. That
recipe is a candidate, not a measured result, and requires a later explicitly
reviewed lifecycle transaction.

## Evidence and decision boundary

The sanitized machine-readable status, exact metrics, caveats, and SHA-256
identifiers for privately retained raw artifacts are in
[qualification-status.json](2026-08-10-deepseek-v4-flash-0731-community-config-refresh-evidence/qualification-status.json).
The broader 11-candidate, 28-source community campaign is in the
[configuration refresh](2026-08-10-deepseek-v4-flash-0731-community-config-refresh.md)
and its
[machine-readable candidate ledger](2026-08-10-deepseek-v4-flash-0731-community-config-refresh-evidence/candidates.json).

Raw operational artifacts remain in the private operator evidence store
because they contain live topology details. The public JSON restates the
bounded claims and content hashes without those identifiers. No route or
promotion changed. A pre-request managed mode-leave attempt failed during
final router readmission after about 543 seconds; inspection found the control
healthy and the prior split owners absent, so no second teardown was attempted
during this campaign.
