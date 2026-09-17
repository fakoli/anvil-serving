# Mixed 3.5-bpw GLM: repaired loader and C4 evaluation

The repaired model ran at C1 and C4. Functional checks and repeated bounded
quality passed; long retrieval passed at 216,307 actual input tokens. Strict
capacity was inconsistent. The baseline was restored; no promotion occurred.

- [Identity](identity.json), [C4 recipe](candidate-recipe.toml), [workloads](workload-manifest.json), and [comparison plan](comparison-plan.json)
- [Loader patch](loader-r7-stream.patch), [CPU fixture](test_stream_loader.py), [pinned build](build-reconstruction.md), and [containment](containment.json)
- C1: [preflight](preflight.json), [one-repetition scout](candidate-intelligence.json), [restoration](restoration.json)
- C4: [preflight](candidate-c4-preflight.json), [repeated quality](candidate-c4-quality.json), [valid capacity cell](candidate-c4-capacity-1.json), [failed repeat](candidate-c4-capacity-2.json), and [long needle](candidate-needle.json)
- Baseline: [repeated quality](baseline-quality.json) and [failed capacity cell](baseline-capacity-1.json)
- Final recovery: [C4 restoration receipt](restoration-c4.json), [direct check](baseline-restored-direct.json), and [routed check](baseline-restored-preflight.json)
- [Decision](summary.json), [failures](friction-log.md), [sources](source-registry.json), and [artifact manifest](artifact-manifest.json)

Primary Node ran the managed trials; Mini was not involved. Public artifacts
redact operator paths, endpoint identities, container IDs, and GPU UUIDs.
Native benchmark artifacts retain their full schemas, visible outputs,
per-request validation, timings, and failures. Only endpoint and operator
identities are redacted.
The recipe remaps its endpoint port to a synthetic example value while
preserving the load-bearing model, runtime, memory, and scheduling settings.
