# Shadow Jev evidence pilot

Run from the directory containing a reviewed `jev-pilot.json`:

```text
anvil-serving eval benchmark jev --allow-export
anvil-serving eval benchmark jev --no-jev
```

Each run requires a new output directory. The command retains one receipt per
packet immediately, including the full-context baseline, stable word-overlap
baseline and Jev proposal. It never executes diagnostics, benchmarks, grades,
route changes, cancellation or promotion. It is outside benchmark timing and
has no automatic-consumption mode.

Install Anvil separately. Use its protected `TYPESAFE_API_KEY` mechanism;
credentials never belong in this config. `jev` uses the existing Serving policy
contract, including an absolute trusted `anvil_binary`, pinned `jev-1.13.0`,
`enabled`, `allow_api`, `allow_export`, and capabilities `context_ranking` and
`incident_triage`. API and export permission plus `--allow-export` are all
required. `--no-jev` makes no provider calls.

```json
{
  "schema": "anvil-serving.jev-pilot/v1",
  "packets": ["selected-case.json"],
  "output": "shadow-run-001",
  "jev": {
    "enabled": false,
    "capabilities": ["context_ranking", "incident_triage"],
    "allow_api": false,
    "allow_export": false,
    "anvil_binary": "/opt/anvil/bin/anvil",
    "model": "jev-1.13.0"
  }
}
```

Paths resolve relative to the config. Select 1–64 unique packets. Packets have
exact fields `schema: anvil-serving.jev-decision-packet/v1`, `id`, `intent`,
`observation`, `optional_limit` (1–24), and `evidence`. Each evidence row has
`id`, selected sanitized `text`, `kind`, `artifact` and `sha256`. Artifact
references are never opened or exported by the command. The owner must verify
them and retain access to every original; the digest binds the referenced
artifact, not the truth of its contents. A `missing_capture` row alone may
have a null digest. Missing originals must remain explicit notices.

Owner-classified `mandatory`, `contradiction`, `failure`, and `missing_capture`
rows are always pinned. Only `optional` rows may be ranked. Incorrect owner
classification remains a risk: independent labels and review must check it.
There are at most 24 optional rows; overlarge input fails instead of truncating.
The stable deterministic baseline ranks by distinct word overlap with intent.
Jev scores reorder optional IDs; tied scores at the cutoff keep all evidence
and flag ambiguity. Known unambiguous error signatures avoid incident calls.
Unknown or conflicting signatures use the closed incident vocabulary and
reviewed diagnostic templates from the existing Anvil bridge. No confidence
threshold is treated as calibrated proof.

The command binds packet and policy digests and checks both around calls.
Revoked or changed inputs invalidate recommendations. Provider failures retain
the baseline and escalation path; every attempt and fallback remains in the
receipt. Anvil validates typed responses; Serving independently validates
capability IDs and provenance. Source instructions never become tool authority.

## Compare before enabling anything

Freeze independently labeled tuning and held-out cases before calls. Replay
all three retained prompts with the same downstream model, settings, tools,
context budget and outcome oracle; retain actual provider usage and failures.
Count preprocessing, all Jev calls, downstream calls and fallbacks. The command
reports selection latency, not overall decision latency or token savings.
Missing cost/usage is unknown, never zero. A valid failed-call usage receipt
counts even when its answer was rejected. Report total tokens, cost where
known, median/p95 decision latency, escalation, critical recall, selection and
category errors and downstream correctness. Do not infer benefit from Jev's
latency alone or from shorter character counts.

Issue #242's initial target is 20% lower median downstream input tokens versus
the stronger baseline, with no total cost or median latency regression and no
held-out critical miss. Unknown billing prevents cost acceptance. Keep shadow
mode and the simpler baseline unless all measured criteria pass. A future
automatic consumer needs its own reviewed restore path and sampled audits.
