# MODEL benchmark or qualification

**Date:** YYYY-MM-DD

**Scope:** LOCAL-HARDWARE, TOPOLOGY, ENGINE, CONTEXT, CONCURRENCY

**Decision:** DECISION-LABEL and the exact promotion boundary

<!-- benchmark-result-card/v1 -->
## Result card

> One sentence stating the bounded local outcome. Name the model, local
> hardware, configuration, and evidence boundary.

| Setup | Qualified value |
|---|---|
| Model | `REPOSITORY@REVISION` and served name |
| Hardware | measured GPU, topology, and interconnect |
| Runtime | engine/image revision, quantization, KV format, speculation |
| Recipe | retained managed recipe or reproduction link |
| Measurement path | direct/routed online, offline engine, or kernel; warm/cold state |
| Contract | context, output reserve, concurrency, modality, reasoning mode |
| Evidence | evidence labels and completion state |
| Decision | decision labels and whether any live state changed |

| Headline measurement | Local result | Conditions |
|---|---:|---|
| METRIC | VALUE | context, concurrency, sample count, statistic |
| METRIC | VALUE | context, concurrency, sample count, statistic |
| NEGATIVE OR BOUNDARY | VALUE | retained failure or untested boundary |

**Why it matters:** Explain the user-visible consequence without expanding the
comparison universe.

**Important caveat:** Lead with the most decision-relevant failure, limitation,
or missing comparison.

Evidence manifest: `RELATIVE-EVIDENCE-README` · Publication summary:
`RELATIVE-PUBLICATION-SUMMARY` (convert both placeholders to links in the finding)

## Outcome and decision

State what passed, failed, changed, and did not change.

## Exact configuration

Record the complete immutable identity and reproducible recipe.

## Method

Define the workload, sample counts, statistics, and measurement terms.

## Results

Present auditable measurements with links to retained raw artifacts.

## Failures and caveats

Preserve negative, incomplete, and non-comparable cases.

## What to test next

Separate proposed experiments from qualified recommendations.

## Evidence boundary

State what the evidence proves, what it does not prove, and the promotion
boundary.
