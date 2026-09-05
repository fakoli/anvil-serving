# Session-efficiency review

## Scope and accounting boundary

A temporary inventory captured seven campaign-related agent sessions at one
point during the work. Some sessions were still active, so these are snapshot
counts rather than final totals:

| Measure | Inventory value |
|---|---:|
| Accounted tokens | 175,034,893 |
| Input tokens | 174,465,524 |
| Input tokens marked cached | 169,816,320 (97.3%) |
| Output tokens | 569,369 |
| Reasoning output tokens | 168,172 |
| Tool calls | 1,226 |
| Recorded tool failures | 12 |

These are API accounting fields, not unique semantic-token counts. Cached input
tokens are repeated context served through a cache; they must not be described
as unique text, model training data, cost, or a measured optimization saving.
This review did not run a controlled before/after efficiency experiment and
makes no savings claim.

## Evidence-backed patterns

- The largest session accounted for 117,404,470 tokens and 798 tool calls. Its
  input was dominated by cached context replay, consistent with a long campaign
  remaining in one growing conversation while many bounded commands ran.
- Three parallel campaign branches together accounted for 47,428,602 tokens
  and 283 calls; 96.4% of their input tokens were marked cached. Full-history
  delegation is therefore a material context-replay surface even though the
  cache changes its runtime economics.
- A sampled managed recipe-log read returned many repeated health lines plus a
  large log body while the actionable runtime/checksum signal appeared near the
  end. Tail-only polling makes concise diagnosis difficult.
- A sampled compound verification block continued after an artifact-finalizer
  failure and emitted a broad worktree inventory. PowerShell did not make the
  failed native executable terminating merely because error preference was set.

The review used the aggregate inventory plus bounded slices from three logs. It
did not ingest or publish raw transcripts, prompts, responses, credentials,
private paths, or reachable endpoints.

## Fix-forward applied to the workflow

- `campaign-state.json` records only stage state, verified launcher, bounded
  assignments, completed cells, failures, evidence paths, and next actions.
- `dispatch-packet.md` supports one bounded assignment without replaying a full
  parent transcript when a small/no-history handoff is sufficient.
- `coverage-and-gaps.md` keeps every requested outcome tied to a direct evidence
  gate, including incomplete and rejected requests.
- Scout, finalist, quality, restoration, and publication stages now have
  separate advance criteria.
- Verification guidance runs one native command per step, stops on nonzero,
  scopes status output to owned paths, and requires an explicit durable
  disposition plus independent verification for every friction entry.

## Deferred product work

Capacity remains a synchronous one-cell surface even though durable cursor-based
benchmark jobs already exist for other suites. Managed recipe logs accept a
bounded tail but not a cursor/error-only continuation. Extending those existing
surfaces is tracked in the
[durable capacity campaign ticket](https://github.com/fakoli/anvil-serving/blob/main/.tickets/2026-09-05-capacity-campaign-jobs-and-cursor-logs.md).
This documentation change does not claim that orchestration or cursor logging
has shipped.
