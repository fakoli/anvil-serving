# SGLang, Qwen, and Nemotron comparison evidence

This public-safe bundle is the evidence index for the 2026-10-16 bounded scout campaign. It preserves native schemas and terminal failures without publishing raw prompts, model output, trajectories, tool arguments, commands, endpoints, private paths, host identity, GPU UUIDs, or container identities.

## Decision

`no-promotion`. The original GLM baseline and Nemotron both have C1 0/4 performance-eligible requests; SGLang GLM and Qwen FP4 miss the frozen coding floor, while Nemotron also misses the campaign exact Oracle gate. No throughput ranking or graph exists.

## Retained evidence

- `baseline-public-configuration.json`, `sglang-scout-public-configuration.json`, `qwen-scout-public-configuration.json`, and `nemotron-scout-public-configuration.json` bind public-safe identities and scope.
- `native/` preserves preflight, Oracle, context, vision, capacity, SWE, and failure projections in their native schemas.
- `source-registry.json`, `run-plan.json`, and `workload-manifest.json` preserve inputs and the late common-packet initialization.
- `summary.json`, `friction-log.md`, and `restoration.json` are campaign-level decision, process, and restoration records, including the pre-trial router-custody checkpoint and its bounded caveats.

The [dated finding](../2026-10-16-sglang-qwen-comparison.md) is the authoritative narrative. [Artifact manifest](artifact-manifest.json) is the closed inventory generated from `artifact-manifest-source.json` after final restoration.
