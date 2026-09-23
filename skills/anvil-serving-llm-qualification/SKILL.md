---
name: anvil-serving-llm-qualification
description: Qualify a pinned local LLM or VLM on Anvil Serving with reproducible text, image, video, capacity, router, and storage evidence. Use for model bakeoffs, failure diagnosis and researched configuration trials, quant comparisons, context/concurrency qualification, multimodal enablement, or production-role recommendations that must remain human-gated.
---

# Anvil Serving LLM Qualification

Use the product CLI for lifecycle, cache, preflight, benchmark, and router work.
Do not create skill-local operational scripts or promote a route.

The maintained layout is `PRODUCT_ROOT/skills/anvil-serving-llm-qualification`.
For global discovery, symlink that directory rather than copying it. Resolve
its real path and verify the product root contains `pyproject.toml` and
`anvil_serving/` before using repository-relative paths. For a copied install,
use the available workspace-resolution skill to locate the product checkout
and read its canonical skill; never infer the checkout from the caller's cwd.
Read `references/evidence-contract.md` and `references/configuration-search.md`
before starting. The latter owns failure investigation and candidate stopping
rules; preserve failed configurations while testing supported successors.
For runtime crashes, configuration improvement, or hardware-limit exploration,
read [runtime-investigation.md](references/runtime-investigation.md) before live
requests. Validate its incident scenario offline first; finish the incident
branch separately from a replacement-model comparison.
Apply the shared `session-improvement-loop:research-synthesis` policy through its Anvil
bindings without requiring the
user to ask for fusion: use one initial researcher and shared source registry,
then independent answers only for a named unresolved material decision.
Budget one synthesis and keep local-only questions on retained local evidence.

## Workflow

1. Record the repository revision, dirty state, live serve/router state, exact
   model/runtime revisions, GPU identity, and cache inventory.
2. Work in an isolated `codex/` worktree. Preserve unrelated and untracked
   files.
   Before downloading or loading a candidate, validate the offline harness:

   ```text
   python scripts/run_tests.py tests/test_agentic_benchmark.py tests/test_benchmark_suite_runner.py -q
   ```

   Stop on a failed fixture/oracle check. Strict tool protocol, fixture state,
   final-answer formatting and independent coding quality remain separate
   evidence. Record the oracle revision; never rewrite historical scores as
   if they had used a newer scorer.
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
7. Diagnose startup and request failures down-stack: caller, product status/logs,
   container exit/health, then engine/model-download error. Retain the earliest
   actionable error, research it, and test a versioned configuration remedy
   under `references/configuration-search.md` before rejecting a candidate.
8. For a hardware-gated upstream kernel patch, pin the upstream commit, verify
   the exact source and result hashes, and fail startup unless the required
   symbol imports or the engine logs prove the intended path loaded. Treat the
   patch as an exact recipe artifact, not generic runtime guidance.
9. For missing hardware-specific MoE/GEMM config warnings or measured kernel
   bottlenecks, use `skills/anvil-serving-kernel-tuning/SKILL.md`. Do not
   recommend a generated tune without the identical untuned-versus-tuned A/B.
10. Declare and qualify the model-native reasoning and sampling policy intended
    for the role. Use thinking-disabled probes only when supported and relevant;
    they do not qualify a deployment that will use a different reasoning policy.
11. Run capacity at declared context/concurrency points. Keep at least the
   campaign’s output/reasoning headroom.
12. For images/video, run direct endpoint preflight and
    `eval benchmark multimodal` before router work. If direct `video_url` fails,
    retain the failure and pause routed video qualification while investigating
    the direct configuration. Resume only after its direct gates pass.
13. If direct video passes, verify same-dialect preservation, fail-closed
    unsupported translation, admission/count/token limits, streaming, tools,
    malformed media, and an isolated router configuration.
14. A profile passes only with exact identity, all deterministic assertions,
    visible answers, allowed finish reasons, matching media hashes, valid
    tools, and no OOM/parser corruption.
15. Explicitly classify the result as either isolated-only or client-facing.
    If no public alias, router metadata, or selected backing identity changed,
    prove that fact and do not rewrite client catalogs. If an authorized
    promotion, rollback, or route switch changes any client-facing alias's
    backing identity, `context_limit_tokens`, `max_output_tokens`, reasoning,
    or modality contract, client propagation is a required qualification
    closure gate even when the request did not mention clients.
16. Every approved promotion must also pass the all-host provisioning and
    rebuild-snapshot gate in `skills/anvil-serving-release-readiness/SKILL.md`,
    even when advertised limits are unchanged. Record source bindings,
    inventory coverage and repeat convergence; missing coverage is
    `harness_convergence_pending`, not completed promotion.
    For client-facing changes on Companion Node, use
    `converge-mini-vision-clients` when installed; otherwise enforce the same
    closure directly. Reconcile Hermes, Pi, and OpenClaw from one authenticated
    router snapshot. Enumerate
    every retained Anvil-backed Hermes profile, preserve provider/auth and
    compaction settings, validate real client behavior, and require an
    idempotent final dry-run. If live client mutation/restart authority is not
    present, report the qualification as client-convergence-pending rather than
    complete or deployed.
17. Restore the exact starting serve/router state. Freeze the final candidate
    source, run focused and full repository gates once, then delegate publication to
    `skills/anvil-serving-benchmark-docs/SKILL.md`. Apply its
    `references/artifact-set-contract.md` and
    `anvil-serving.benchmark-artifact-set/v1` template around the native raw
    artifacts, dated finding, precision/modality decision table, and required
    publication-ready result card. Add a publication summary only when compact
    public communication is requested; otherwise mark that role
    `not-applicable` with a reason. For a format-only
    refresh backed by complete retained artifacts, reconcile the published
    values without restarting the serve or rerunning the benchmark.

## Improve the workflow from evidence

At campaign closure or after a material process failure, read
`references/improvement-loop.md`. Capture scoped lessons in existing evidence,
then evaluate authorized skill changes independently. Preserve the running
qualification baseline and the budget for the requested model outcome.

## Decisions

- Separate official/community claims from locally measured results.
- Compare quantized profiles only after every hard gate passes for the exact
  frozen profile. A failed profile returns to configuration search; it does
  not automatically reject the checkpoint or engine.
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
