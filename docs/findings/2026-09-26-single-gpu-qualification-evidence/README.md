# Evidence index: 2026-09-26 single-GPU qualification

This closed bundle records a no-promotion decision. Native suite artifacts are
retained under `raw-run-evidence/`; their schemas and result counts are not
flattened. The two sanitized SWE files retain their native schema but replace
operator paths and transient container identifiers. Recipe reconstructions use
`${MODEL_CACHE}` and `${ENGINE_CACHE}` placeholders.

- [Decision summary](summary.json), [source registry](source-registry.json),
  [workload and gate contract](workload-manifest.json), and [coverage](coverage-and-gaps.md).
- [Restoration](restoration.json), [friction log](friction-log.md), and
  [redaction ledger](redaction-ledger.json).
- [Closed artifact ledger](artifact-manifest.json). The publication-summary
  role is not applicable because no compact public communication was requested.

ThinkingCap coding raw evidence is missing after managed cancellation. The
retained [review excerpts](thinkingcap-r1-cancellation-review-excerpts.json)
preserve no complete trajectory, patch, grader output, or completed score.

## Native gate reports

| Configuration | Agentic / context | Images | Coding |
|---|---|---|---|
| Flash IQ3 r3 | [18/18](raw-run-evidence/single-gpu-swift-iq3-r3-agentic-0-agentic.json) / [12/12](raw-run-evidence/single-gpu-swift-iq3-r3-context-0-context.json) | [12 image](raw-run-evidence/swift-iq3-r3-images.json), [4 eight-image](raw-run-evidence/swift-iq3-r3-eight-images.json) | [3/5, all graded](raw-run-evidence/single-gpu-swift-iq3-r3-swe-2-swe.json) |
| Swift27 r2 | [18/18](raw-run-evidence/single-gpu-swift27-r2-agentic-0-agentic.json) / [12/12](raw-run-evidence/single-gpu-swift27-r2-context-0-context.json) | [12 image](raw-run-evidence/swift27-r2-images.json), [4 eight-image](raw-run-evidence/swift27-r2-eight-images.json) | [2/5 attempted, 4 graded](raw-run-evidence/single-gpu-swift27-r2-swe-2-swe.json) |
| ThinkingCap r1 | [18/18](raw-run-evidence/single-gpu-thinkingcap-r1-agentic-0-agentic.json) / [12/12](raw-run-evidence/single-gpu-thinkingcap-r1-context-0-context.json) | [12 image](raw-run-evidence/thinkingcap-r1-images.json), [4 eight-image](raw-run-evidence/thinkingcap-r1-eight-images.json) | incomplete; [observed excerpts](thinkingcap-r1-cancellation-review-excerpts.json) |

The [Flash preflight](raw-run-evidence/swift-iq3-r3-preflight.json) includes
the 20-request tool gate. [GLM reuse verification](baseline-swe-reuse-verification.json)
binds the [retained 4/5 reference](../2026-09-23-glm-dcp1-qualification-evidence/swe-native.json).

Capacity diagnostics retain [C1 unique](raw-run-evidence/swift-iq3-r3-final-c1-unique.json),
[C4 shared](raw-run-evidence/swift-iq3-r3-final-c4-shared.json), and the
[invalid C4 unique timing](raw-run-evidence/swift-iq3-r3-final-c4-unique.json)
with its [cache-history correction](raw-run-evidence/capacity-cache-control-correction.json).
The correction takes precedence over the native artifact's eligibility flag.
These populations do not support a qualified model-speed ranking.

Restoration checks are retained as [direct](raw-run-evidence/glm-r11-restored-direct-preflight.json)
and [authenticated routed](raw-run-evidence/glm-r11-restored-routed-preflight.json)
native reports, alongside the exact identity in [the restoration receipt](restoration.json).
