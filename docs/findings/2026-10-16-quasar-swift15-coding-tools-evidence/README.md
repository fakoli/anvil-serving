# QUASAR versus Swift15: evidence index

Completed 2026-10-16 functional comparison on one RTX 5090 (32,607 MiB).
Keep Swift15 as default for now; QUASAR is not a clear upgrade. No promotion occurred.

Both final profiles used the same pinned NInfer image, text-only 32K context,
configured C4 and actual request C1. Coding used seven tasks repeated three
times, with 4,096 visible allocation + 8,192 reasoning headroom and a combined
12,288-token cap; the endpoint does not partition channels. Medium reasoning,
temperature 0.6 and top-p 0.95 were matched. Seeds and cache warmth were not fixed.

| Result | QUASAR | Swift15 | Retained evidence |
| --- | ---: | ---: | --- |
| Agentic scenarios | 16/18 | 18/18 | [QUASAR](native/quasar-text32k-agentic-raw.json), [Swift15](native/swift15-text32k-agentic-raw.json) |
| Frozen coding cases / solutions | 119/123; 20/21 | 120/123; 20/21 | [QUASAR](native/quasar-text32k-coding-grade.json), [Swift15](native/swift15-text32k-coding-grade.json) |
| Tuple-pair sensitivity | 123/123; 21/21 | 120/123; 20/21 | [QUASAR](native/quasar-text32k-coding-grade-tuple-edges.json), [Swift15](native/swift15-text32k-coding-grade-tuple-edges.json) |

The tuple analysis is post hoc, changes only pair reconstruction, and preserves
the original grades. The ambiguous prompt prevents a general coding ranking.
Both QUASAR tool misses omitted the requested final status after correct calls.
Swift15's coding miss was an unambiguous varint implementation error.

## Reproduction and provenance

- [Native raw-output index](raw-evidence-index.md), including full coding responses and reasoning.
- [Frozen fixture and grader](fixture/README.md), [manual source review](source-review-notes.md).
- [Configuration and recipes](configuration-and-identity.md), [run plan](run-plan.json).
- [Pinned identity sources](source-registry.json); no external performance prior informed the decision.
- [Redaction and source/public hash lineage](redaction-lineage.json).
- [Ten-role manifest](artifact-manifest.json), [decision summary](summary.json), [publication summary](publication-summary.md).

The installed collection harness was 1.5.0/Python 3.14.8 with a retained source
fingerprint; its source revision was unavailable. The dirty helper/publication
worktree declared 1.2.1 and is not claimed as the collection source. All ten
artifact roles retain files. Native schemas, prompts, outputs, reasoning and
case results are preserved; only declared operator metadata was redacted.
Original pre-redaction grade input hashes map to public copies through lineage.

## Failures, restoration and limits

The failed 262K vision/text profiles are historical phases, not scored answers.
QUASAR's measured 32K container and the fresh Swift15 test container were unloaded;
the original Swift15 262K profile was restored. [Restoration record](restoration.json),
[public restored preflight](native/final-restored-preflight.json), and
[independent verification](native/final-restored-verification.json) are retained.
Detailed host-state assertions are reported by that verification record; underlying
operator identity/status sources remain private.

[The friction log](friction-log.md) preserves startup, transport and tooling gaps.
The checksum-offload experiment was negative and restored every setting. The
cache-transfer host performed no inference and is not measured hardware. Native
Windows SWE, long-context/vision qualification, and a controlled performance
comparison were not completed. Timing remains descriptive. Recipe reconstructions
preserve the as-run prequalification labels; they are not promotion records.
