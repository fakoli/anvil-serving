# Evidence index — 2026-11-21 GLM derivative survey

This directory is the public-safe, local evidence projection for the dated
finding. It contains derived score records and sanitized endpoint/restoration
receipts; it does not contain prompts, completions, reasoning traces, endpoint
addresses, local paths, or credentials.

Every copied native JSON projection has an adjacent `.binding.json` with its
source and public SHA-256. Derived summaries identify their source class in the
source registry; publisher grading, native SWE, Aider, context, agentic,
preflight, C4, and QAD capability receipts remain findable from this index.

The [decision summary](summary.json), [artifact manifest](artifact-manifest.json),
and [restoration record](restoration.json) retain the completed measurement
campaign. Serving is restored; the pre-existing monitoring-registration gap is
retained separately and no promotion follows.

| Artifact | Purpose | Boundary |
|---|---|---|
| `raw/mia-60-publisher-grades.json` | Literal-parser per-item grade metadata and totals for Mia | Derived score evidence; no response text |
| `raw/az327-60-publisher-grades.json` | Final matching grade metadata and totals for AZ327-Focus | Derived score evidence; no response text |
| `raw/mia-vs-az327-60.json` | Final paired score, summed sequential C1 request-time, token, and discordance calculation | Fixed 60-case comparison only |
| `raw/az327-vision.json` | Final AZ327 image-case aggregate and per-attempt metadata | Outputs, prompts, reasoning, and media paths removed |
| `raw/az327-natural-c4.json` | AZ327 matched C4 natural-output receipt | Eight-request eligible population; no raw journal or serving flags |
| `raw/mia-natural-c4.json` | Mia matched C4 natural-output receipt | Eight-request eligible population; no raw journal or serving flags |
| `graphs/paired-c4-matrix.svg` and [`graph data`](graphs/paired-c4-matrix-data.json) | Rendered matched-C4 observation matrix | Separate n=8 pairs; not pooled and not a broad speed claim |
| `az327-mia-configuration-comparison.json` | Public-safe matched-configuration summary | Omits command paths, ports, and environment values |
| `configuration-reconstruction.json` | Exact public model/image pins, runtime, quantization, and serving controls | Explicitly marks template or llama.cpp revisions that were not retained |
| `az327-preflight-summary.json` | Public-safe functional summary | Omits endpoint, prompts, completions, reasoning, and tool arguments |
| `az327-swe-scout5-summary.json` | Official-image SWE-Bench Verified five-instance summary | IDs and independently graded outcomes only; not a paired comparison |
| `raw/paired-swe-five.json` | Paired five-image SWE-Bench Verified projection | Per-case official-grader decisions and digest bindings; not a full-suite score |
| `native-scores/oracle-outcomes.json` | Per-case native score provenance | Sanitized source hashes for the retained AZ, QAD, Tev, and Swift claims |
| `capability-scouts-summary.json` | AZ327 and Swift-light capability aggregates | Minimal public-safe aggregates; no prompts, responses, or traces |
| `raw/swift-clean-c4-seed482204.json` | Swift clean C4 receipt | 8/8 eligible, seed 482204; matched Mia receipt retained |
| `raw/mia-clean-c4-seed482204.json` | Matched Mia clean C4 receipt | 8/8 eligible, seed 482204 |
| `raw/qad-r2-c4.json` | QAD R2 C4 repeat receipt | 0/8 eligible; no performance metrics |
| `az-reexecution-digests.json` | Per-case AZ64/AZ327 reexecution proof | Hashes only; prompts and answers excluded |
| `qad-r3-capability-summary.json` | QAD R3 functional aggregate | Does not create a timing or performance claim |
| `qad-publisher12-summary.json` | QAD one-card publisher12 pilot | Partial cross-runtime comparison only |
| `swift-clean-c4-summary.json` | Compact derived C4 metrics | Separate matched Mia seed-482204 cohort retained; n=8 is not a broad speed claim |
| `tev-compact-scout-summary.json` | Tev compact functional aggregate | One historical Mia oracle image is not a control |
| `frozen-plan.json` | Frozen request/scoring plan | Controls and source corpus identity |
| `raw/qad-r1-c1.json` | One-card Qwen functional receipt | 0/8 performance eligible; no timing result |
| `raw/final-service-continuity.json` | Final authenticated service-continuity receipt | Mia serving healthy; two traffic guards retained; monitoring-registration gap predates campaign |
| `endpoint-receipt-*.json` | Sanitized identity/configuration receipts | Configured values are not capacity results |
| `restoration-router-glm-after-qad-r1.json` | Sanitized restoration receipt | Restoration evidence, not promotion |
| `restoration.json` | Canonical restoration record | Authenticated Mia serving healthy; pre-existing monitoring-registration gap retained |
| `*.binding.json` | Source-to-projection SHA-256 binding | Verifies the staged projection source |

The earlier `az-60` files are retained as preliminary AZ64 evidence only. The
final headline is the independently re-executed 327K profile: all 60 visible
answers are byte-identical to AZ64 while all request and raw-event digests are
different, so it is not a reused response trace.

The source registry, coverage gaps, decision summary, restoration record, and redaction record are
maintained in this directory. The artifact set is sealed locally; no external
publication transaction was performed.
