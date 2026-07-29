# Benchmark publication contract

## Update matrix

| Trigger | Finding + index | Run catalog | Dossier | Hardware page | Archive | Methodology |
|---|---|---|---|---|---|---|
| Configured/served or failed load | yes, when meaningful | yes | yes | yes | only if reader comparison changes | only if contract changes |
| Functional/capacity/quality benchmark | yes | yes | yes | yes | only if reader comparison changes | only if contract changes |
| Qualification without promotion | yes | yes | yes; `no-promotion` | yes | only if recommendation changes | no |
| Promotion/rollback/current decision | yes | yes | yes | yes | yes | no |

## Dossier template

```markdown
# Model

## Current status and review date
## Immutable identity
## Tested hardware and topology
## Engine, quantization, KV, context, and concurrency recipe
## Evidence by measurement class
## Decision and promotion state
## Failures and gotchas
## Dated run history
```

Use `not-qualified` in prose for failed/incomplete configurations, with the
canonical evidence label `compatibility-only` or `historical-invalid` and the
decision label `rejected` or `no-promotion`.

## Finding template

State date, repository revision, host/topology, measured hardware, protected
hardware, exact model/image/engine identity, recipe, workload, gate outcomes,
metrics, failure details, decision, promotion boundary, raw artifact links, and
current-doc impact. Link raw JSON rather than copying it.

## Run-catalog row template

```markdown
| YYYY-MM-DD | Capability | Exact model/configuration | Measured GPU | Evidence labels | Decision labels | [Dossier](models/model.md) · [Finding](../findings/YYYY-MM-DD-model.md) |
```

For RTX 5090 rows, insert the `PRO relationship` column before evidence. Every
row links both a dossier and a finding. Split unrelated models into separate
rows unless one campaign finding is the only retained evidence; in that case,
link the applicable hardware subsection of the dossier index.

## Non-promotion boundary

A configuration, cache pull, load, health check, preflight, benchmark,
qualification, or documentation update is never permission to mutate a serve
or route. Promotion and rollback remain separately human-gated operations.
