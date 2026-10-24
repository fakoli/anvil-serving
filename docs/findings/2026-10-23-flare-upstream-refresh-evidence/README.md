# Flare upstream refresh evidence

This closed diagnostic baseline completed 391 adapter requests plus one smoke
across six fresh incarnations, with no canonical request failures. The exact
incumbent was restored. This is current upstream plus a minimal GLM legacy-template
compatibility patch, not pristine upstream or a 25%/75% comparison.

Start with [summary](summary.json), [reconstruction](reconstruction.md),
[friction](friction-log.md), [coverage](coverage-and-gaps.md),
[source registry](source-registry.json) and the [manifest](artifact-manifest.json).
The [repetition chart](graphs/replay.svg) derives from exact
[graph data](graphs/replay-data.json) and [decision summary](summary.json).

Raw archives preserve full native responses, metrics, commands, failures and
restoration. `archives/canonical-00-fixtures.tar.gz` and the five numbered lane
archives contain the complete canonical phase populations; `archives/canonical-root.tar.gz`
contains root-level and restoration receipts. All failed predecessor windows
and driver recovery remain in separately named archives. Accepted and rejected
large summaries are retained in `archives/accepted-summary.tar.gz` and
`archives/rejected-analysis.tar.gz`. Full file/member paths and hashes are in
[provenance](provenance.json). Original private bytes remain retained separately;
public hashes bind sanitized payloads, not their private originals.

The native full speculative-work fields remain unknown. One-token matching
does not clear the earlier 128-token sampled-output equality failure. Driver 615.71.10 and
current upstream are different from historical observations; no historical
95–96% improvement is attributed to this baseline. No promotion, default change
or upstream submission occurred. All per-request timings remain descriptive.

## Proposed contributions

Read the [compatibility PR draft](compatibility-pr-draft.md), its [exact patch](legacy-glm-template-compat.diff), and the separate [cache issue draft](issue-draft.md). These are reviewable proposals, not submitted upstream messages. The compatibility fix is separate from an unrun allocation comparison.
