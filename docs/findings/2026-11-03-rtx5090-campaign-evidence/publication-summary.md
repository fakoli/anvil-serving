# Publication summary: RTX 5090 candidate campaign

<!-- benchmark-publication-summary/v1 -->

Derivative copy only. [Finding](../2026-11-03-rtx5090-campaign.md),
[native manifest](artifact-manifest.json) and [evidence index](README.md)
are the canonical public record.

## Canonical facts

- One RTX 5090, Windows/Docker Linux VM; direct online requests.
- [Managed configuration description](configuration.md): Ignis 0.8.0 and separate
  NInfer profiles; exact private model/asset identity withheld.
- Seven coding task types, three repeats; 21 solutions and 123 behavioral checks.
- Fresh 128K text candidate:123/123, median task 0.886s; restored 160K vision
  reference:114/123,1.399s. Different loaded capabilities; no general ranking.
- Matched Ignis 32K budget follow-up:111/123→123/123 at 99.145→305.500 summed
  task seconds. Post-hoc tuning, not held-out validation.
- Repeated prompts/prefix reuse; not cold-only. No promotion or full replacement.
- Evidence cutoff 2026-11-03. Public identity/text redactions limit replay.
- Canonical URL: https://fakoli.github.io/anvil-serving/findings/2026-11-03-rtx5090-campaign/

## X / short post

Local RTX 5090 coding fixture: three candidates reached 123/123 in selected configs. Ignis needed a larger reasoning budget; no replacement qualified. https://fakoli.github.io/anvil-serving/findings/2026-11-03-rtx5090-campaign/

## Reddit

**Title:** RTX 5090 candidate tests: coding passes, reasoning-budget tradeoffs, and why no model was promoted

On one RTX 5090 I tested three candidate families using direct endpoints.
Each coding profile used seven task types repeated three times.
Selected configurations passed 123/123 checks; the restored reference passed 114/123.
The reference and candidates differ in context, reasoning and vision capability.

The matched Ignis 32K follow-up improved 111/123 to 123/123 by raising the thinking
budget from 1,024 to 4,096, while summed task latency increased 99.145 to 305.500s.
That is a local fixture result, not a general model ranking or an optimal budget.

Repository tasks, routed clients and full-context concurrency remain unqualified.
Private identities/output text are withheld; numeric native evidence and failures
are retained. No promotion occurred.

[Methods, failures and evidence](../2026-11-03-rtx5090-campaign.md).
This copy is prepared, not posted to an external community.

## Screenshot alt text

RTX 5090 local result card: fresh 128K text candidate passes 123 of 123 coding checks,
restored 160K vision reference passes 114. Median task latencies 0.886 and 1.399 seconds.
Different capabilities, seven task types repeated three times, no promotion.

## Claim ledger

| Claim | Conditions | Evidence |
|---|---|---|
| Three families each reached 123/123 | Selected distinct configurations;21 solutions each | [All coding rows](coding-results.md), [summary](summary.json) |
| Reference 114/123 and candidate 123/123 | Fresh 160K vision versus 128K text; not equivalent capabilities | [Reference grade](raw/reference-restored-coding-grade-final.json), [candidate grade](raw/coding-candidate-128k-recheck-coding-grade-final.json) |
| Ignis budget recovery and latency | Matched 32K controls except budget; post-hoc selection | [1,024 raw](raw/ignis-budget1024-coding.json), [4,096 raw](raw/ignis-budget4096-coding.json) |
| No full replacement or promotion | Missing real-workload/capability parity | [Finding](../2026-11-03-rtx5090-campaign.md#failures-and-remaining-work), [restoration](restoration.json) |
