# Mixed 3.5-bpw GLM startup evidence

One failed managed startup on September 17, 2026. No candidate inference request completed or was submitted. The retained kernel records identify host RAM exhaustion and desktop-session shutdown; this is not a throughput or quality result.

- [Boot continuity](boot-continuity.json), [configuration hashes](configuration-hashes.json), and before/after managed status in the manifest.
- [Identity](identity.json) and [predeclared plan](run-plan.json).
- [Kernel and desktop excerpts](failure-excerpts.log), [startup excerpts](startup-excerpts.log).
- [Baseline before-test preflight](baseline-before-preflight.json).
- [Restored direct preflight](baseline-restored-direct-preflight.json) and [authenticated routed preflight](baseline-restored-routed-preflight.json).
- [Restoration](restoration.json), [decision](summary.json), [sources](source-registry.json), and [friction](friction-log.md).
- [Artifact manifest](artifact-manifest.json).

Publication redactions: host labels are synthetic; private endpoint ports, container IDs, GPU UUIDs, operator paths, and unrelated process inventories are omitted. Log excerpts preserve timestamps, process names, numeric memory fields, and terminal errors. Raw evidence remains separately retained by the operator.
