# Benchmark publication contract

## Update matrix

| Trigger | Finding + index | Run catalog | Dossier | Hardware page | Archive | Methodology |
|---|---|---|---|---|---|---|
| Configured/served or failed load | yes, when meaningful | yes | yes | yes | only if reader comparison changes | only if contract changes |
| Functional/capacity/quality benchmark | yes | yes | yes | yes | only if reader comparison changes | only if contract changes |
| Qualification without promotion | yes | yes | yes; `no-promotion` | yes | only if recommendation changes | no |
| Promotion/rollback/current decision | yes | yes | yes | yes | yes | no |

## Dossier template

Use `templates/dossier.md` for new dossiers and format-only migrations. The
template carries the `benchmark-dossier/v2` marker and this stable navigation
contract:

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

Inside that contract:

- lead with a scannable `Decision snapshot` covering product role,
  best-qualified configuration, measured hardware, evidence, decision and
  promotion boundary, important limitation, evidence cutoff, and dossier
  review date;
- preserve the detailed review narrative, divided by date or outcome when it
  spans multiple decisions;
- group immutable identity, recipes, and evidence by materially different
  configuration family instead of blending lanes;
- label each evidence subsection with `Status`, `Measured`, `Limits`, and
  `Evidence`, followed by the narrative needed to interpret the result;
- separate retained/selected configurations from rejected, superseded, or
  incomplete configurations;
- group long failure lists by cause and use bold lead labels; and
- keep every decision-relevant dated finding reachable.

Link the exact public recipe when one is tracked and safe to publish. When the
managed recipe belongs to the private operator repository or embeds private
topology, link a sanitized public reconstruction page instead. Never expose a
private repository path, endpoint, host identity, or credential to satisfy the
dossier layout.

When retained evidence lacks a field, publish **Not recorded in retained
evidence**, **Not retained**, or **Unknown** as appropriate. Do not infer the
value, erase the gap, or run live hardware solely to make the layout
symmetrical. Optional subsections may be omitted when there is no supporting
evidence, but the eight canonical `##` headings remain required.

Use `not-qualified` in prose for failed/incomplete configurations, with the
canonical evidence label `compatibility-only` or `historical-invalid` and the
decision label `rejected` or `no-promotion`.

## Publication-ready finding v1

Every local `functional`, `capacity`, or `quality` finding has two compact,
derivative views in addition to its complete narrative and raw evidence:

1. A result card near the top of the finding, marked with
   `<!-- benchmark-result-card/v1 -->` and based on `templates/finding.md`.
2. A companion publication summary, marked with
   `<!-- benchmark-publication-summary/v1 -->` and based on
   `templates/publication-summary.md`.

The result card contains one bounded local outcome, the exact setup, three to
six headline measurements with their conditions, why the result matters, the
most important caveat or failure, and links to the evidence manifest and
publication summary. The exact setup includes the managed recipe or reproduction path and the
measurement layer: direct or routed online endpoint, offline engine, or kernel
microbenchmark, plus warm/cold state when relevant. The detailed narrative
follows it; the card does not replace any required identity, method, failure,
or decision detail.

The publication summary contains canonical facts, managed recipe, measurement path,
copy-ready X/short-post text, a Reddit title and body, screenshot alt text, and
a claim ledger. Every metric or capability claim in the copy maps to a finding
section or raw artifact. State that results are local. Do not use `best`,
`fastest`, `strongest`, or equivalent comparison language without naming and
supporting the exact comparison set and calculation.

Project formatting limits are at most 260 literal characters for the preferred
X/short-post variant, with 280 as the hard validation limit, and at most 120
characters for the preferred Reddit title. Subreddit rules can be stricter and
must be checked at posting time. Include the canonical finding URL inside the
short-post character count. Platform copy and screenshots never become evidence,
qualification, or promotion authority.

Research-only, release/readiness-only, and failed-load findings may use the
card, but their publication summary is optional unless a compact public
communication is required.

### Repository-only format migration

A format-only migration does not require a live rerun when retained public
artifacts already provide:

- immutable model, runtime, recipe, hardware, and topology identity;
- context, output reserve, concurrency, sampling/reasoning, and modality
  configuration;
- measurement path, warm/cold state, workload/corpus identity, sample counts,
  statistics, and metric definitions;
- request-level or aggregate machine-readable results, including failures;
  and
- the decision, promotion boundary, and raw artifact links.

Generate the card and publication summary from those artifacts and add a consistency
test for the published headline values. If a required value is absent or only
exists as an unsupported prose claim, disclose the gap instead of inferring it
or running a live benchmark without separate authorization.

## Detailed finding template

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
