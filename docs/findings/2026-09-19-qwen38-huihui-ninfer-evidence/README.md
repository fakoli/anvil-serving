# Qwen3.8 Huihui NInfer qualification evidence â€” 2026-09-19

The exact no-spec8K/C1 profile is **rejected for promotion** after repeated strict tool failures. The campaign reached a hard-gate decision and restored media; full performance, long-context, MTP and endurance qualification did not run.

- [Dated finding](../2026-09-19-qwen38-huihui-ninfer.md), [decision](summary.json), [coverage](coverage-and-gaps.md), [publication](publication-summary.md).
- [Identity and run binding](configuration.json), [workloads](workload-manifest.json), [run plan](run-plan.json), [feasibility input](feasibility-input.json) and [result](feasibility-result.json).
- [Source registry](source-registry.json), [research](research-notes.md), [producer manifest](upstream-build-manifest.json).
- [Initial text recipe](recipe-initial-text.toml), [vision reload recipe](recipe-vision-qualification.toml), [owning log excerpts](runtime-log-excerpts.txt).
- [Friction](friction-log.md), [independent intake review](independent-recipe-review.md), [gate review](independent-gate-review.md), [restoration](restoration.json).
- [Repository verification](verification.json), [infrastructure disposition](infrastructure-disposition.md).
- [Campaign ledger](campaign-state.json), [bounded assignments](dispatch-packet.md), [hash manifest](artifact-manifest.json).

## Native evidence

| Artifact | What it proves |
|---|---|
| [Initial smoke](preflight-nospec-smoke.json) | Two HTTP400 failures from unsupported request field. |
| [Server-default smoke](preflight-nospec-server-control.json) | Smoke and JSON2/2. |
| [Protocol preflight](preflight-nospec-protocol.json) | Tools, streaming tools, continuation, Responses,4096-token needle5/5. |
| [Quality off](quality-nospec-8k.json) | Diff0/3, triage3/3, string ZIP tools0/3. |
| [Quality requested low reasoning](quality-nospec-reasoning-low.json) | Diff3/3, triage3/3, string ZIP tools0/3. |
| [Initial images](multimodal-nospec-8k.json) | Configuration disabled vision;0/12. |
| [Vision reload](multimodal-nospec-8k-vision.json) | Image/OCR/chart/UI/spatial/multi-image12/12. |
| [Incumbent preflight](preflight-incumbent.json) | Smoke and JSON2/2. |
| [Incumbent quality](quality-incumbent-control.json) | Diff, triage and string ZIP tools3/3 each. |

All probes use direct loopback on the measured Secondary Node RTX5090. Native latency fields are incidental mixed warm/cold samples, not controlled performance evidence. Launcher1.0.0 comes from repository commit `2b99e3353eb2e5a2813195af85050b1da0754332` plus this campaign's recipe/docs additions. Model grading uses deterministic assertions, not self-evaluation.

Absolute operator corpus paths were redacted in native image JSON; public text line endings were normalized to LF. Private originals remain. Runtime binaries/linked dependencies are not baked into an immutable candidate image; no deployment recreation claim is made.
