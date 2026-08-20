# DeepSeek V4 Flash 0731 r33 393K Primary promotion

**Date:** 2026-08-11

**Evidence:** local `functional`, `capacity`, and bounded `quality`

**Decision:** human-approved `current` for `llm.primary`; subsequent live
ownership remains private operator state

## Outcome

The digest-pinned r33 DeepSeek 0731 recipe became the routed text Primary at a
393,216-token window after a managed profile application proved exact model
identity, readiness, and exclusive ownership of both RTX PRO 6000 cards. The
profile keeps the quality-first choices: FP8 DS-MLA KV, DSpark K5, 4,096
batched tokens, 16 admitted sequences, 0.975 GPU utilization, and no CPU or
filesystem KV offload. The engine reported 725,543 GPU KV tokens, or about
1.845 full configured windows.

OpenClaw's existing `anvil/llm.primary` model entry was updated from a 262,144
to a 393,216 context window and its generic 8,192-token output limit was raised
to the quality-oriented 32,768-token ceiling. Config validation passed, the
gateway completed safe restarts, and isolated
post-restart basic-agent and real tool-loop requests both used provider
`anvil`, model `llm.primary`, without fallback. The tool action was recorded as
started and succeeded.

## Exact promoted configuration

- Model: `deepseek-ai/DeepSeek-V4-Flash-0731`, revision `9e165c30e2704aec5d9d593cce3eebd58bbef1cb`
- Served name: `deepseek-v4-flash-0731-r33-b12x-dspark5-maxseq16-batch4096-tp2-393k`
- Image: `sha256:fdde59fed7f9fc12f9fd5ef1b3b3ea8d5097bf10ebad54b348497102c3a83f82`
- Runtime: r33 B12X W4A8 mixed NVFP4-MoE/FP8, FP8 DS-MLA KV, DSpark fixed probabilistic K5
- Hardware: 2x RTX PRO 6000 Blackwell Max-Q, exclusive TP=2 over PCIe without NVLink
- Envelope: 393,216 context, `max_num_seqs=16`, `max_num_batched_tokens=4096`, utilization 0.975
- Capacity tiers: GPU only; no KV offload and no reserve gate

The portable managed configuration is the
[393K recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r33-b12x-dspark5-maxseq16-batch4096-393k-recipe.toml).

## Capacity and functional evidence

The calibrated direct context ladder passed at 238,507, 339,310, and 359,900
actual API prompt tokens. The largest request measured 65.2-second TTFT,
5,599 effective prefill tok/s, and returned 158 output tokens. A post-probe
functional gate passed.

The routed preflight passed smoke, JSON, 3/3 typed tools, streaming tools,
tool-result continuation, and the Responses API. Its legacy nominal-320K
needle failed closed with HTTP 413 because the generated prompt was 1,800,115
bytes and the router's conservative bytes/4 estimate was 450,028 tokens—above
the 393,216-token route limit. This is a benchmark-harness calibration mismatch,
not a successful routed long-context result. The direct 359,900-token request
proves model capacity above 300K; a calibrated routed context job is still
required before claiming the same through the router.

The preflight's reasoning-evidence-required check also failed on trivial
prompts that completed without a reasoning channel. The earlier repeated
high-reasoning quality suite remains the quality evidence; this result is not
silently converted to a pass.

## OpenClaw verification

OpenClaw version `2026.7.1-2` retained `agents.defaults.model.primary =
anvil/llm.primary`. The provider model now declares 393,216 context tokens and
32,768 maximum output tokens. After safe gateway restarts:

- Run `2a0968e4-27b6-4652-a32e-dd466f3b6887` returned the exact readiness
  marker, reported a 393,216-token context budget, and used no fallback.
- Run `df38095f-1029-4d62-9ac3-ca7368e6d6d4` completed an actual workspace
  tool call, returned the expected marker, and used no fallback.
- Session `agent:main:config-393k-32k-smoke-20260811` returned the exact
  `OPENCLAW_393K_32K_READY` marker at high thinking after the output-cap
  correction; gateway config audit and RPC health passed.

These are functional client-path checks, not an OpenClaw 300K context test.

## Hermes verification

Hermes Agent `0.20.0` already selected custom model `llm.primary` but retained
a 262,144-token provider metadata entry, no explicit output cap, and persistent
reasoning effort `none`. Its active default-model configuration now pins
`model.context_length=393216`, `model.max_tokens=32768`, and
`agent.reasoning_effort=high`. Hermes' own configuration command performed the
write; the launchd messaging gateway restarted with zero active sessions.

An isolated one-shot request returned the exact
`HERMES_393K_32K_HIGH_READY` marker through provider `custom`, model
`llm.primary`, in one API call. This proves the corrected Hermes client path,
not a Hermes request above 300K.

The operator then restarted Hermes for an upgrade. Version `0.20.0` retained
all three settings, but the upgrade left the current launchd definition
unloaded. `hermes gateway start` reloaded the matching service definition and
restored launchd supervision with auto-start/auto-restart. A post-upgrade
one-shot returned the exact `HERMES_POST_UPGRADE_393K_32K_HIGH_READY` marker in
one API call.

## Promotion and benchmark failures retained

The first lower-level promotion attempt exposed two product defects: the
transaction supplied mutually exclusive reasoning controls to preflight, and
its automatic recovery could not transition directly between exclusive serve
owners. The stopped candidate was removed through managed lifecycle commands,
then the declared `dual-gpu-exclusive` profile applied successfully and
readmitted the route only after the expected and observed model identities
matched. The defects are recorded in
[the promotion recovery ticket](https://github.com/fakoli/anvil-serving/blob/main/.tickets/2026-08-11-exclusive-promotion-preflight-and-recovery.md).

The requested remote-worker SWE smoke did not submit a benchmark job. Controller
0.33.1 accepted the typed dispatch but returned `profile_unavailable` because
the installed wheel lacks the repository-level benchmark-profile JSON files.
No SWE score exists for this promotion. The packaging gap is recorded in
[the benchmark profile ticket](https://github.com/fakoli/anvil-serving/blob/main/.tickets/2026-08-11-benchmark-profiles-missing-from-installed-wheel.md).

## Scope and restoration boundary

This promotion proves exact managed routing, direct capacity through 359,900
actual prompt tokens, corrected OpenClaw and Hermes client budgets, and
post-restart client paths. It does not prove routed, OpenClaw, or Hermes
context above 300K, broad concurrency at 393K, or a SWE score. During the
approved qualification, exclusive TP=2 kept other GPU inference offline. The
post-session owner and operating mode remain private operator state rather
than a public deployment claim.

The sanitized machine-readable record is
[promotion-summary.json](2026-08-11-deepseek-v4-flash-0731-r33-393k-promotion-evidence/promotion-summary.json).
