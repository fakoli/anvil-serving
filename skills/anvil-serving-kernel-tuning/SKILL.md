---
name: anvil-serving-kernel-tuning
description: Decide whether an Anvil Serving model needs hardware-specific kernel tuning, run an isolated engine tuner, qualify the result against an untuned baseline, and recommend, store, or reject the tune with reproducible evidence. Use for missing MoE/GEMM kernel-config warnings, suspected kernel bottlenecks, engine or GPU upgrades that invalidate an existing tune, and default-versus-tuned throughput or latency comparisons.
---

# Anvil Serving Kernel Tuning

Use the product CLI for lifecycle and benchmark operations. Do not add
skill-local tuner scripts, change a live route, or treat a generated
microbenchmark winner as production proof.

Read `references/tune-contract.md` before tuning or selecting an artifact.

## Decide

1. Capture the exact warning, model revision, runtime image and digest, engine
   revision, GPU identity, driver, CUDA, Triton, dtype, tensor-parallel size,
   kernel family, model geometry, and current recipe.
2. Check the pinned runtime and `configs/kernel-tunes/` for an exact compatible
   tune. A filename for another GPU, runtime, dtype, TP size, or model geometry
   is not compatible.
3. Tune only when an exact config is absent and either the runtime reports a
   fallback or profiling identifies the kernel as a material bottleneck.
4. Estimate the run before starting. Record every planned batch size, the
   configurations per batch, completed-batch timings from any pilot, total
   expected work, disk need, and a range rather than a single completion time.
5. Skip expensive tuning for a short-lived candidate unless the campaign needs
   the result to make a decision.

## Establish the baseline

1. Work on an isolated candidate serve and preserve the starting serve/router
   state. Never tune against or mutate the live Primary route.
2. Retain the fallback warning and verify that the expected tuned config is
   actually absent.
3. Run the campaign's functional hard gates and representative end-to-end
   capacity lanes with the default kernel selection. Use at least three warmed
   repetitions for the performance comparison.
4. Capture throughput, TTFT, effective client-observed prefill, generation
   duration, decode rate, mean inter-token latency, p50/p95 E2E latency, exact
   prompt/output token counts, GPU allocation, errors, and workload identity.
   Label effective prefill as queueing/scheduling-inclusive. Kernel
   microbenchmarks are supplementary evidence.

## Tune

1. Use the pinned engine's official tuner inside an isolated managed workload
   on the target GPU. Pin any tuner-only dependencies and record them.
2. Keep the complete batch-size surface unless a bounded pilot was explicitly
   chosen. Progress is completed batches divided by planned batches; do not
   confuse per-batch configuration count with total configurations.
3. Preserve the first actionable failure. Before retrying a timed-out client,
   inspect whether the tuner container is still running so a second writer is
   not started.
4. Let an authorized long tuner finish. Monitor it through bounded status/log
   reads and do not restart it merely because the controlling shell detached.
5. Validate that the output is parseable, covers every planned batch size, and
   records the expected kernel family, shape, dtype, and tuner/runtime version.

If the product lacks the required bounded lifecycle or evidence surface, record
the gap in `.tickets/` and add it to the Anvil Serving CLI/MCP rather than
making a skill-local operational script.

## Qualify and recommend

1. Recreate the same candidate with only the tune activation changed.
2. Prove from startup logs that the exact tune was loaded and the fallback
   warning disappeared. A set environment variable is not proof of use.
3. Repeat the same functional and end-to-end performance lanes. Do not use the
   model to validate its own output.
4. Compare paired default and tuned evidence. Use campaign-specific thresholds
   when declared. Otherwise recommend adoption only when the primary lane
   improves by at least 5 percent, no protected lane regresses by more than
   3 percent, all hard gates still pass, and the improvement repeats across
   three warmed runs.
5. Label a smaller stable improvement as inconclusive, not adopted. Reject a
   tune that regresses, fails to load, changes functional behavior, or only
   improves the kernel microbenchmark without an end-to-end benefit.

## Store, activate, and publish

1. Store accepted and rejected artifacts under the canonical path in
   `references/tune-contract.md`. Use a short portable repository filename and
   record the engine-required config filename separately; preserve the latter
   byte-for-byte when building or mounting the runtime artifact.
2. Add `kernel-tune-manifest/v1` identity, hashes, tuner duration, activation
   contract, baseline/tuned evidence links, and the decision.
3. Make activation explicit in a managed recipe. Supply the config through a
   read-only mount or a pinned derived image layer and set the engine's
   supported config-folder control. Storage alone must not activate a tune.
4. Re-run startup, functional, capacity, and restoration checks using the exact
   managed recipe. Promotion remains separately human-gated.
5. Publish the warning, tuning cost, default/tuned results, applicability
   boundary, recommendation, and raw artifact links through
   `skills/anvil-serving-benchmark-docs/SKILL.md`. Apply its
   `references/artifact-set-contract.md` and
   `anvil-serving.benchmark-artifact-set/v1` template around the
   `kernel-tune-manifest/v1`, default/tuned native evidence, startup proof,
   friction, and restoration record. Its publication-ready result card and
   publication summary must name the matched baseline, measurement path,
   repetitions, delta calculation, and retained regression boundary. A
   format-only refresh uses the paired retained artifacts and does not rerun
   the tuner or model.
6. Restore and verify the exact starting serve/router state.
