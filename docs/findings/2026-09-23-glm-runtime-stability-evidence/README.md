# GLM runtime investigation evidence

This bundle supports the [dated finding](../2026-09-23-glm-runtime-stability.md).
The [summary](summary.json) is the authoritative source for displayed results;
[coverage](coverage-and-gaps.md) lists the unrun gates. Three matched DCP1
175K/32K overlaps passed after the DCP2 parent crashed. This is bounded
mitigation evidence, not kernel attribution, a reliability rate, performance
ranking or replacement qualification. The exact baseline is restored.

[Artifact manifest](artifact-manifest.json) binds every retained byte;
[manifest source](artifact-manifest-source.json) reproduces it. Native stability
artifacts retain failures, usage, response IDs and bounded outputs as originally
collected. The initial parent and 512-cap runs predate the result-retention fixes;
see [friction](friction-log.md) and [correlation](crash-175k-correlation.json).

[Sanitization receipts](sanitization-receipts.json) bind private source hashes to
public copies. Generic paths, ports and GPU identities are reconstruction
placeholders. Container IDs are consistently redacted with one-way tokens;
the evidence inspector marks their provenance as sanitized. Recipe/registry digests inside native evidence identify the
original private configuration; recalculate them when loading a local copy.
The endpoint placeholders must not be used as proof that a recipe is running.

No graph or social-copy package is required: these are diagnostics without
comparable performance populations. All files are text; raw evidence stays
below 1 MiB per file and 5 MiB for this bundle.

## Source registry

- [source-registry.json](source-registry.json)

## Workload manifest

- [workload-manifest.json](workload-manifest.json)

## Run plan

- [campaign-plan.md](campaign-plan.md)
- [notebook.md](notebook.md)
- [campaign-state.json](campaign-state.json)

## Configuration and identity

- [baseline-recipe.toml](baseline-recipe.toml)
- [candidate-dcp1-recipe.toml](candidate-dcp1-recipe.toml)
- [baseline-recipe-status.json](baseline-recipe-status.json)
- [baseline-gpus.csv](baseline-gpus.csv)
- [dcp1-start-identity.json](dcp1-start-identity.json)
- [dcp1-start-status.json](dcp1-start-status.json)
- [dcp1-admission.json](dcp1-admission.json)
- [dcp1-startup.log](dcp1-startup.log)
- [development-source.json](development-source.json)
- [sanitization-receipts.json](sanitization-receipts.json)
- [dcp1-small-scenario-comparison.json](dcp1-small-scenario-comparison.json)
- [dcp1-175k-scenario-comparison.json](dcp1-175k-scenario-comparison.json)

## Raw run evidence

- [baseline-small.json](baseline-small.json)
- [baseline-175k-serial.json](baseline-175k-serial.json)
- [baseline-175k-overlap.json](baseline-175k-overlap.json)
- [baseline-preflight.json](baseline-preflight.json)
- [baseline-preflight-thinking-enabled.json](baseline-preflight-thinking-enabled.json)
- [baseline-json-thinking-enabled.json](baseline-json-thinking-enabled.json)
- [json-policy-disabled-2.json](json-policy-disabled-2.json)
- [json-policy-disabled-3.json](json-policy-disabled-3.json)
- [json-policy-enabled-2.json](json-policy-enabled-2.json)
- [json-policy-enabled-3.json](json-policy-enabled-3.json)
- [dcp1-preflight.json](dcp1-preflight.json)
- [dcp1-small.json](dcp1-small.json)
- [dcp1-small-matched.json](dcp1-small-matched.json)
- [dcp1-175k-overlap.json](dcp1-175k-overlap.json)
- [dcp1-175k-repeat2.json](dcp1-175k-repeat2.json)
- [dcp1-final-metrics.json](dcp1-final-metrics.json)
- [dcp1-final-owner.log](dcp1-final-owner.log)
- [dcp1-final-status.json](dcp1-final-status.json)
- [dcp1-final-kernel.log](dcp1-final-kernel.log)

## Failures and friction

- [friction-log.md](friction-log.md)
- [crash-175k-correlation.json](crash-175k-correlation.json)
- [crash-175k-overlap-model-combined.log](crash-175k-overlap-model-combined.log)
- [crash-175k-overlap-kernel.log](crash-175k-overlap-kernel.log)
- [crash-175k-overlap-status.json](crash-175k-overlap-status.json)
- [dcp1-small-kernel.log](dcp1-small-kernel.log)
- [dcp1-small-owner.log](dcp1-small-owner.log)
- [dcp1-201k-time-admission.json](dcp1-201k-time-admission.json)
- [skill-review-report.txt](skill-review-report.txt)
- [skill-review-operator-workflow-v1.json](skill-review-operator-workflow-v1.json)
- [skill-delta-review.json](skill-delta-review.json)
- [skill-scenario-comparability-review.json](skill-scenario-comparability-review.json)
- [runner-review-findings.json](runner-review-findings.json)
- [runner-review-operator-workflow.json](runner-review-operator-workflow.json)
- [dcp1-followup-findings.json](dcp1-followup-findings.json)
- [dcp1-followup-operator-workflow.json](dcp1-followup-operator-workflow.json)
- [dcp1-result-retention-followup.json](dcp1-result-retention-followup.json)
- [dcp1-result-retention-operator-workflow.json](dcp1-result-retention-operator-workflow.json)
- [dcp1-continuation-review.json](dcp1-continuation-review.json)
- [dcp1-continuation-operator-workflow.json](dcp1-continuation-operator-workflow.json)

## Restoration

- [restoration.json](restoration.json)
- [restoration-status.json](restoration-status.json)
- [restoration-startup.log](restoration-startup.log)
- [restoration-direct-preflight.json](restoration-direct-preflight.json)
- [restoration-routed-preflight.json](restoration-routed-preflight.json)
- [restoration-router-sha256.json](restoration-router-sha256.json)
- [restoration-kernel.log](restoration-kernel.log)
- [restoration-gpus.csv](restoration-gpus.csv)
- [recovery1-preflight.json](recovery1-preflight.json)
- [recovery1-routed-preflight.json](recovery1-routed-preflight.json)

## Decision summary

- [summary.json](summary.json)

Independent final [source and publication review](publication-review.json) accepted
the reviewed bytes for exact PR-head CI. Its manifest hash deliberately records
the bundle before insertion of this review packet.
