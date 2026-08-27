---
name: anvil-serving-llm-qualification
description: Qualify a pinned local LLM or VLM on Anvil Serving with reproducible text, image, video, capacity, router, and storage evidence. Use for model bakeoffs, quant comparisons, context/concurrency qualification, multimodal enablement, or production-role recommendations that must remain human-gated.
---

# Anvil Serving LLM Qualification

Use the product CLI for lifecycle, cache, preflight, benchmark, and router work.
Do not create skill-local operational scripts or promote a route.

Read `references/evidence-contract.md` before starting.

## Workflow

1. Record the repository revision, dirty state, live serve/router state, exact
   model/runtime revisions, GPU identity, and cache inventory.
2. Work in an isolated `codex/` worktree. Preserve unrelated and untracked
   files.
3. Gate storage with `models cache inventory`. For any removal, use an exact
   revision dry-run and explicit confirmation. Never broad-prune Docker or
   delete volumes.
4. Record every source with observed date, evidence class, hardware/runtime
   relevance, and decision impact. Treat model cards and discussions as priors,
   not local proof.
5. Pull exact revisions with `models pull`; a shell/client timeout does not
   prove failure. Before retrying, inspect whether the one downloader container
   remains active. Never start competing writers against the same cache.
6. Load one isolated candidate at a time with a pinned recipe and discovered
   GPU UUID. For a Windows/WSL GPU lane, credential handoff, unhealthy loaded
   recipe, managed switch, or restore transaction, use
   `.agents/skills/anvil-serving-candidate-operations/SKILL.md`. Do not change
   a live alias.
7. Diagnose startup failures down-stack: caller, product status/logs, container
   exit/health, then engine/model-download error. Retain the earliest actionable
   error.
8. For a hardware-gated upstream kernel patch, pin the upstream commit, verify
   the exact source and result hashes, and fail startup unless the required
   symbol imports or the engine logs prove the intended path loaded. Treat the
   patch as an exact recipe artifact, not generic runtime guidance.
9. For missing hardware-specific MoE/GEMM config warnings or measured kernel
   bottlenecks, use `skills/anvil-serving-kernel-tuning/SKILL.md`. Do not
   recommend a generated tune without the identical untuned-versus-tuned A/B.
10. Run thinking-disabled functional gates first. Use default thinking only as a
   bounded diagnostic lane.
11. Run capacity at declared context/concurrency points. Keep at least the
   campaign’s output/reasoning headroom.
12. For images/video, run direct endpoint preflight and
    `eval benchmark multimodal` before router work. If direct `video_url` fails,
    publish the failure and stop routed video qualification.
13. If direct video passes, verify same-dialect preservation, fail-closed
    unsupported translation, admission/count/token limits, streaming, tools,
    malformed media, and an isolated router configuration.
14. A profile passes only with exact identity, all deterministic assertions,
    visible answers, allowed finish reasons, matching media hashes, valid
    tools, and no OOM/parser corruption.
15. Restore the exact starting serve/router state. Re-run focused and full
    repository gates, then publish raw artifacts, a dated finding, and a
    precision/modality decision table.

## Decisions

- Separate official/community claims from locally measured results.
- Compare quantized profiles only after every hard gate passes.
- Compare speculation to an otherwise identical no-speculation control. Keep
  model, revision, image, patch, TP, context, concurrency, KV dtype, memory
  fraction, backends, batching, graph capture, transport, parsers, and offload
  policy fixed; do not inherit a KV dtype already known to fail that hardware
  path.
- Prefer a quant only when it meets the campaign’s declared memory or
  throughput improvement threshold relative to the next higher precision.
- Limit a known-crashing vision quant to a text-only role; do not infer future
  support from shipped vision weights.
- Keep promotion separately human-gated.
