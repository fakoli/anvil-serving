# RTX 5090 private candidate families and Ignis runtime trials

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** bounded coding and synthetic capability research.
    - **Best-tested settings:** private candidate/Ignis 0.8.0 with 4,096 thinking budget;
      QAT/NInfer no-speculation; coding-focused/NInfer 128K text profile.
    - **Measured hardware:** one RTX 5090, Windows/Docker Linux VM.
    - **Evidence:** quality, functional and short-output capacity diagnostics;
      selected profiles each passed 123/123 coding checks.
    - **Decision:** challenger evidence, no-promotion.
    - **Important limitation:** private identities/text withheld; seven task
      types repeated three times, no real repository or equivalent-main qualification.
    - **Review dates:** evidence and dossier reviewed 2026-11-03.

[Campaign finding](../../findings/2026-11-03-rtx5090-campaign.md) ·
[Ignis improvement note](../../findings/2026-11-03-ignis-improvements.md) ·
[Configuration description](../../findings/2026-11-03-rtx5090-campaign-evidence/configuration.md).

The coding candidate passed the fixture with lower observed task latency than
the restored reference, but its 128K text profile is not the 160K vision profile.
Ignis's budget follow-up recovered correctness at a substantial latency cost.
QAT's text survivor did not qualify its failed vision sibling.
**Outcome:** retain configuration-specific research leads; no replacement decision.

## Immutable identity

Model/variant, weight/draft/projector, served identity and image fingerprints
are withheld by the private-model boundary. Ignis 0.8.0 is a public runtime
identity, not a model identity. Original reference capture was incomplete.
Sanitized native artifacts do not grant public immutable-identity qualification
or independent reproduction. See [redactions](../../findings/2026-11-03-rtx5090-campaign-evidence/redaction-ledger.json).

## Tested hardware and topology

One RTX 5090, 32 GB class; one active candidate at a time; direct online endpoints.
No other GPU was measured. No routed-client or deployment claim follows.

## Engine, quantization, KV, context, and concurrency recipe

The [sanitized configuration](../../findings/2026-11-03-rtx5090-campaign-evidence/configuration.md)
records distinct 32K/128K/160K/256K profiles, quantization and available KV
descriptions, host caps, thinking policy and configured lanes. It is not an
executable recipe: exact identity is intentionally private. Four Ignis lanes
are not four measured full-context requests.

## Evidence by measurement class

**Status:** completed bounded quality/functional/short-output diagnostics;
full serving comparison incomplete.

**Measured:** selected candidate profiles 123/123 coding; restored reference 114/123.
Ignis large-profile retrieval 45/45 through 245,646 input tokens, tools 30/30,
required image facts 18/18. QAT text retrieval 45/45 and tools 30/30.
The coding candidate's earlier raw retrieval 225/225 remains inside a failed
outer job, separate from its fresh coding recheck.

**Limits:** cross-profile policies and capabilities differ. Retrieval is synthetic;
image markers do not score incidental hallucinations; tools are short-message
fixtures; capacity uses 16-word C1 outputs. No broad ranking or real repository score.

**Evidence:** [complete coding rows](../../findings/2026-11-03-rtx5090-campaign-evidence/coding-results.md),
[summary](../../findings/2026-11-03-rtx5090-campaign-evidence/summary.json),
[native index](../../findings/2026-11-03-rtx5090-campaign-evidence/README.md).

## Decision and promotion state

Selected settings are challenger research leads, not current deployment assignments.
The larger reasoning budget warrants held-out validation; QAT speculative failures
and vision load failure remain rejected configurations.
No serving route or model assignment changed through this publication.

## Failures and gotchas

- **Reasoning:** smaller budget broke state invariants; larger budget is not a universal optimum.
- **Vision:** Ignis captions added unsupported interpretations; metadata reported text.
- **Memory:** Ignis reached its 16GiB host cap without OOM; four lanes do not establish full-window capacity.
- **Evidence:** failed outer jobs stay failed; historical identity gaps and public redactions remain visible.
- **Missing gates:** repository tasks, routed clients, video, endurance and full-context concurrency.

## Dated run history

- 2026-11-03 — [RTX 5090 campaign](../../findings/2026-11-03-rtx5090-campaign.md)
  and [Ignis use/improvements](../../findings/2026-11-03-ignis-improvements.md).
