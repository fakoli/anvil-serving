# GLM-5.3-Flash NVIDIA FP4 primary promotion follow-up

**Date:** 2026-10-17

**Decision:** `current`, human-approved primary promotion

**Evidence class:** local functional and bounded capacity acceptance; the controlled A/B remains the authoritative comparison record.

<!-- benchmark-result-card/v1 -->

## Result card

The human-approved NVIDIA GLM-5.3-Flash FP4 configuration is live behind
`llm.primary`. It is the exact accepted candidate from the earlier
[quality/context finding](2026-10-17-glm53-nvidia-fp4-ab.md) and the
[controlled serving comparison](2026-10-17-glm53-serving-performance.md), not
a newly inferred variant. The retained EXL3 r11 control is stopped and retained
as the exact rollback target.

| Setup | Bounded local outcome |
| --- | --- |
| Model identity | `nvidia/GLM-5.3-Flash-FP4-Local@76348ddb2be2e35e39ecfc00e75dc07703f4f3d5` |
| Served configuration | `glm53-nvidia-fp4-local-tp2-327k-c4-mtp3-r2`; TP2, 327,680 configured tokens, C4, MTP3 |
| Direct acceptance | 9 protocol checks passed; eight-image corpus 18/18; largest retained near-limit request measured 316,249 prompt tokens and stopped normally |
| Routed acceptance | authenticated preflight 8/8, eight-image corpus 18/18, and `debug-loop-v2` oracle revision 9 passed 1/1 across five requests |
| Fresh capacity acceptance | client C1/C2/C4 at approximately 31.6K actual prompt tokens: 13 completed requests, one excluded warmup, 12 scored; no observed OOM, preemption, or refusal |

The prior serving comparison retains the full numeric tables and its failed and
blocked cells. This follow-up does not duplicate those tables or convert its
bounded results into a universal quality, SWE, p99, endurance, MTP-off, or
full-window simultaneous-capacity claim.

## Promotion evidence and boundary

The promoted artifact is pinned to revision
`76348ddb2be2e35e39ecfc00e75dc07703f4f3d5`. The same accepted container image
used by the comparison remains selected. Startup configuration was staged and
enabled for that retained owner; the repeat check was idempotent. No reboot or
fresh rebuild was tested.

The direct acceptance receipt retained nine passing protocols, including
tools, Responses, image handling, and the large context probe. The routed
receipt retained authenticated protocol acceptance, 20 tool checks, image
coverage, and the five-request coding/tool continuation. Both receipts preserve
their pre-dispatch launch corrections as non-inference events.

Fresh single-arm capacity evidence covered all 17 required metric families at
client C1, C2, and C4. It contains 13 valid completed requests, with one warmup
excluded and 12 scored. It records zero OOMs, preemptions, and refusals in
those cells. At client C4, sampled maxima were engine active 2, queued 3, and
occupancy 4; these are independent time maxima and must not be summed. C4 does
not show four simultaneously decoding sequences. This is a fresh acceptance
check at approximately 31.6K actual prompt tokens, not a replacement for the
earlier A/B or a long-duration test.

## Rollback, propagation, and publication status

The former EXL3 r11 service is stopped and retained as the exact managed
rollback target; no blind recreation of an old unit is implied. The primary
alias now selects the approved FP4 configuration. Fleet propagation, real
client catalog convergence, and public Grafana, Workbench, or web publication
have separate authority and evidence gates. The authoritative NativeWorkflows
owner check is `recovery_required` with failed workflow progress, 13 targets,
and `all_targets_verified=false`; no propagation retry or settlement was run.
Grafana readback confirmed the comparison dashboard's 11 panels. Prometheus and its observer
each retained 792 numeric campaign observations across 17 metric families, 396
per comparison arm. Workbench returned 92 configured cards, including 24 exact
campaign cards (12 per arm) with pinned revision and engine fields. Canary and
primary apply/repeat checks passed. Native Prometheus discovery still exposes
its historical model label, and its target and engine-capability checks fail
under the existing NativeWorkflows recovery state; the comparison metrics and
evidence cards were verified independently. No public Pages deployment is
authorized or recorded.

One fresh Primary Node Pi tool smoke has passed with the exact alias, provider, nonce,
one tool call, normal completion, and no session. Subsequent real OpenClaw
Gateway smokes passed on Companion Node and Mac with the selected primary alias,
327,680-token context, one successful tool call, exact nonce, normal completion,
and no fallback. The first Mac attempt encountered its headless execution
approval policy; the bounded retry passed using the permitted read tool without
changing that policy. The failure remains retained.

Earlier owner-local evaluator attempts failed before dispatch because their
credential environment was unset. Those setup failures remain separate from
the later Gateway results. The native Gateway counters did not expose usable
token accounting or engine timing; client wall-clock durations are not TTFT or
decode throughput. The existing 17-family capacity evidence remains the
performance record.

Five additional Pi process combinations passed fresh no-session read-tool
checks across Primary Node, Companion Node, and Mac. Each retained the exact
nonce, selected provider and primary alias, normal stop, and `agent_settled`.
Post-run executable hashes identify the tested combinations; two use an
explicit Node interpreter instead of the launcher shebang. Two earlier lost
captures remain excluded: seven client turns produced five accepted captures,
not seven accepted results or an inferred HTTP request count. Mac retained a
stale model-pattern warning even though its explicit primary invocation passed.

Both Companion Node Hermes profiles selecting the primary alias passed fresh
CLI checks with one terminal read, the exact nonce, and normal completion.
Exact-session exports bind both to the approved router. Profile metadata names
the provider `anvil`; runtime exports name it `custom`. Their context, fallback,
and effective reasoning fields remain unavailable. The two secondary profiles
are outside this primary-model check. These results do not validate existing
conversations, Pi Web, or the running Hermes Gateway. Full catalog and session
propagation remains blocked by the native workflow's custody gap.

A fresh owner and pinned-source audit confirmed that the older failed workflow
has no terminal custody receipt. Its missing fixture records held random native
session identities that cannot be reconstructed from the job identifier. Empty
fixture directories do not prove those sessions settled. Recovery still needs
an authenticated owner barrier bound to the original job and targets.

Open WebUI catalog reconciliation passed for all six approved aliases and three
active accounts. Its repaired infrastructure helper verifies the complete
approved catalog using the owner's credential, while WebUI retains its limited
credential. Managed preview, apply, scheduled execution, and repeat checks
passed; repeat apply reported zero changes. The model, router, and WebUI
container identities and start times were unchanged. This closes the separate
WebUI catalog defect without settling the older fleet workflow.

Private operator receipts hold raw request captures, configuration identities,
and full hashes. This finding links the sanitized local
[promotion evidence summary](2026-10-17-glm53-nvidia-fp4-promotion-evidence/summary.json)
and the existing public [authoritative comparison JSON](2026-10-17-glm53-serving-performance-evidence/analysis/final-closed/summary-table.json).

## Evidence and limits

The source receipts were reviewed from the private promotion evidence bundle:
direct acceptance summary, routed acceptance summary and hash addendum, fresh
capacity summary, and startup apply/repeat-check receipts. The public summary
intentionally omits endpoint addresses, host identities, container identifiers,
paths, and credentials.

This decision establishes the live primary mapping after the stated human gate.
It does not establish fleet-wide client success, a public-site readback,
external publication, a general quality ranking, or a safe maximum capacity.
