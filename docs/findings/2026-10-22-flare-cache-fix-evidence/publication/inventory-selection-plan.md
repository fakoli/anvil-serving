# FLARE-FIX-001 final inventory selection plan

This is a selection plan for the existing `publish-evidence.py` publisher. It
is not an inventory, a hash list, a performance analysis, or publication
approval. Create the explicit `flare-cache-fix-publication-inventory/v1` only
after all six arms are closed, the final derived outputs exist, producers have
stopped, and the selected files pass the separate identity/secret and size
reviews. Enumerate every selected regular file explicitly at that time; the
publisher does not accept directory globs.

## Publisher roles

| Role | Planned retained selection |
| --- | --- |
| `evidence-index` | Final `README.md`, publisher-generated `provenance.json`, final report/evidence map, and final inventory review record. |
| `source-registry` | The dated `source-registry.json`, with `candidate-source-v2.patch`, baseline-source material, `contribution/` review units and manifest, source/test receipts, and frozen harness/analysis source as supplemental source provenance. |
| `workload-manifest` | `harness/*bodies*.json`, `harness/decode-bodies/`, contracts, oracle fixtures, prospective configuration, and the final normalized matrix. |
| `run-plan` | `formal/execution-plan.json`, `preformal-freeze.json`, `runtime-pins.json`, candidate freeze/geometry, managed recipes/registries, monitoring query contract, and this selection plan. |
| `configuration-and-identity` | Candidate/build/image/config receipts, managed status/running proofs, phase plans, identity responses, generation records, and frozen input digests. |
| `raw-run-evidence` | Every regular file under each closed formal arm; all pilot and audit request/response evidence; monitoring ranges/samples/readiness history; and final strict/descriptive/causal derived outputs. |
| `failures-and-friction` | `failed-windows/r2-b-pretraffic-1/`, failed/partial pilot and audit receipts, all retained stderr/stdout/error records selected with their corresponding run, `friction-log.md`, and reviews that explain limitations. |
| `restoration` | Campaign `restoration.json`, every arm's managed restore/closeout/parent-review/final-monitor evidence, historical restoration readiness, discovery receipts (including r2-B's original false receipt and supplemental receipt), and audit/pilot restoration evidence. |
| `decision-summary` | `summary.json`, `report.md`, `coverage-and-gaps.md`, `report-evidence-map.json`, diagnostic amendment, final strict/descriptive outputs, final causal assessment, and gate/limitation reviews. |
| `publication-summary` | `not-applicable`: the requested public deliverable is the complete report and evidence set, not a separate social or announcement copy. |

## Archive groups

Use directory-preserving `.tar.gz` archives with the publisher's canonical
evidence-relative member names. Each group below expands to all selected
regular files under the named roots; no request, response, failure, empty
poll, or retained raw body may be dropped to reduce size.

| Public archive path | Selected roots after closure | Primary roles |
| --- | --- | --- |
| `raw/formal-r1-a.tar.gz` through `raw/formal-r3-b.tar.gz` | `formal/r1-a/` through `formal/r3-b/`, one archive per arm | raw-run-evidence, configuration-and-identity, restoration |
| `raw/failed-windows.tar.gz` | `failed-windows/r2-b-pretraffic-1/` | failures-and-friction, restoration |
| `raw/pilots-and-audits.tar.gz` | `pilot-*/`, `audit-*/`, `audit-window/`, and top-level pilot/audit receipts | raw-run-evidence, failures-and-friction, restoration |
| `raw/monitoring-and-readiness.tar.gz` | `monitoring/`, `analysis/readiness-history/`, and arm-local `query-ranges/`/`monitoring-samples.jsonl` files already included in formal archives | raw-run-evidence, configuration-and-identity |
| `raw/lifetime-and-transfer-fixtures.tar.gz` | `lifetime-test-execution/`, `lifetime-test-proposal/`, `offload-audit-current-tests.json`, transfer audit receipts, and independent fixture reviews | source-registry, raw-run-evidence, failures-and-friction |
| `source/frozen-source-and-harness.tar.gz` | `baseline-source/`, `contribution/`, frozen patch/trace files, `harness/`, and frozen analysis scripts | source-registry, workload-manifest, run-plan |
| `review/reviews-and-limitations.tar.gz` | `review/` current reviews, retained runner-source/provenance records, and `analysis/` method/limitation/assembly receipts | failures-and-friction, decision-summary |

The final explicit inventory must prevent duplicate selected sources across
archives. Where an arm-local monitoring file is already in its formal archive,
the monitoring archive must not select it again. `provenance.json` will retain
the private-source hash and the sanitized payload/archive-member hash mapping.

## Direct public artifacts

Select these as individually named artifacts rather than duplicate them inside
an archive: final `README.md`, dated `source-registry.json`, `summary.json`,
campaign `restoration.json`, `report.md`, `coverage-and-gaps.md`,
`friction-log.md`, `report-evidence-map.json`,
`publication/native-evidence-schemas.json`, the final
`analysis/normalized-matrix.json`, `analysis/diagnostic-analysis.json`,
`analysis/descriptive.json`, the final hash-linked causal assessment, and the
reviewed inventory itself if it is public-safe. Their final paths, roles, and
whether they are gzip-compressed are an inventory-review decision.

## Required closure additions

Before inventory review, add the r3-B closed-arm readiness/restoration receipts
and proof assembly output; then create the final normalized matrix, strict
analysis, descriptive output, and diagnostic causal assessment. Include all
six formal arms, all historical readiness records, and all five restoration
query responses per completed arm. Retain the numerical failure, false
independent-oracle/restore-fidelity/causal receipts, r1-B monitoring limits,
r2-B original restoration-proof failure, its separately labeled supplemental
receipt, and every failed startup/pretraffic record.

## Excluded files and reasons

| Exclusion | Reason |
| --- | --- |
| `**/__pycache__/` and `*.pyc` | Interpreter cache; reproducible from selected Python source and not evidence. |
| Publisher staging output, generated `provenance.json`, `artifact-manifest-source.json`, and finalized `artifact-manifest.json` before the closure run | Generated only by the reviewed publisher/finalizer; include the final generated outputs through their defined artifact roles, never as inputs. |
| `publication/review-history/` superseded publisher/registry snapshots | Private repair provenance, not campaign measurement evidence; retain privately while selecting the current reviewed publisher, registry, review notes, and source hashes. |
| Live mid-campaign status such as `progress.json` after a final closeout supersedes it | Retained privately as chronology; final arm closeouts, report, friction ledger, and failure archives are the public record. This exclusion does not remove a failed run or receipt. |
| Unreviewed drafts, temporary synthetic-check fixtures, and any non-UTF-8/binary payload | Do not silently omit a required artifact. Either retain it through a reviewed safe representation or record the role/item as missing with a reason; the existing publisher accepts UTF-8 text only. |

Public packaging proceeds under the user's existing authorization after the
inventory's explicit paths pass publisher preflight, an independent
identity/secret review, the size-policy review, and finalizer repeatability.
Upstream submission and serve promotion remain separate human-gated actions.
