# Private-evidence publication audit — 2026-07-22

- **Issue:** [#175](https://github.com/fakoli/anvil-serving/issues/175)
- **Public repository base:** `85b0e40053c56f004fda960a00a00b72ee4fd1f8`
- **Companion repository inspected:** `fakoli/anvil-serving-notes` at
  `7b46ceb6ae62252f8f808f6c065706a24e7970bb`, after fetching `origin/main`
- **Evidence type:** repository inventory, sanitized publication, and offline deterministic rerun
- **Outcome:** public grounding restored; one unavailable raw-log set recorded as an explicit gap

## What was published

Three load-bearing narratives were present in the companion repository and are now public:

- [Anvil integration audit](2026-06-28-anvil-integration-audit.md)
- [planning-capability evaluation](2026-06-28-planning-capability-eval.md)
- [harness intent-routing research](2026-06-29-harness-intent-routing.md)

They also remain recoverable without private access. The June 28 narratives were introduced at
`21f9a81f9be98dab3be15b07395ab34749d852b6`, and the harness research at
`b0a68c64482774a719da76a62a745e095effda1e`. The strongest single public snapshot is
`31d95adaf68157b81318325356516cef9569b10f`: immediately before relocation, its complete bundle
tree `6e26ebc80b853e151075e8807c0bba084480f823` exactly matches the fetched notes mirror. The notes
revision is an inventory input, not the public chain's sole provenance.

The planning evaluation's complete bounded bundle is public under
[`eval-data/2026-06-28-planning-capability/`](eval-data/2026-06-28-planning-capability/PUBLICATION.md): 21 canonical
source files totaling 159,272 canonical Git bytes before three enumerated publication-safety edit
categories. It includes both
rendered prompts, all six model outputs, generation metrics, deterministic structural scores, four
blind-judge records, the anonymization map, aggregates, and the historical harness. No file exceeds
1 MiB and the bundle is below ADR-0027's 5 MiB limit.

The pre-publication bundle matched no user-home paths, emails, credentials, authorization values,
session identifiers, or secret-like values. The integration narrative's one machine-local checkout
path was replaced by its public repository and pinned commit. Source SHA-256 digests and every
publication edit are recorded alongside the bundle.

## What ran

From the source snapshot and again from the public copy, `python grade_struct.py` followed by
`python aggregate.py` reproduced the tracked structural scoreboard and judge aggregates. The rerun
left the generated JSON/CSV unchanged. This proves the offline calculation path over the published
inputs.

The 2026-06-28 local/frontier generations and blind judge calls were not rerun. Their models and
topology are historical; the publication does not relabel them as a current model qualification.

## Mirror claims checked

PR #174 removed these three public files while saying their home was the private notes repository:

- `docs/PLAN-advise-and-defer.md`
- `docs/REVIEW-2026-07-02-architecture-and-models.md`
- `docs/OPENCLAW-LIVE-VALIDATION.md`

None is present under its exact path or as a byte-for-byte mirror in any fetched
companion-repository ref. Related descendants do exist: the advise-and-defer PRD, the v0.4-to-v0.7.2
reflection, and `docs/findings/2026-06-30-openclaw-live-validation.md`, which records results from the
removed validation runbook. The exact removed files remain recoverable from the public product repository at
[`cf09b06bd63d6d4013a67b59d581a8689a804cf9`](https://github.com/fakoli/anvil-serving/tree/cf09b06bd63d6d4013a67b59d581a8689a804cf9/docs).
They are planning/review records, not the load-bearing evidence published above. Current behavior is
covered by public reference docs, ADRs, and findings, so no public claim depends on pretending those
files were mirrored.

## Unavailable evidence

ADR-0008's speculative-decoding raw benchmark logs were never committed to the product repository.
A search of the fetched companion history for its exact throughput, cache-hit, model-class, and
flag signatures found no matching record. The
[evidence-gap finding](2026-07-22-adr-0008-evidence-gap.md) makes that absence public and limits how
the historical ADR may be used.

## Current-doc impact

The quality-gated router reference, Fakoli Dark runbook, OpenClaw plugin docs, relevant ADRs, and
changelog now link public evidence or explicitly classify private material as non-load-bearing.
Private design history remains supplementary under ADR-0027.
