# Failures and fix-forward dispositions

| Observed problem | Disposition and evidence | Status |
| --- | --- | --- |
| TLS record corruption and SSH integrity errors during artifact transfer | Network root cause remains open. Approved receive-checksum test was negative; settings restored. Exact final artifact hashes verified. Further network diagnosis is outside this model comparison. | deferred host-network issue |
| Managed staging could not use a verified local artifact | Native-stage/import repair in `anvil_serving/models.py`, with strict revision/SHA, path and capacity checks; independent review and eight focused tests passed. Broader author model tests also passed. | repaired for tested import path |
| Active download helper not visible through normal status | Narrow read-only diagnosis recorded in the [existing product-gap ticket](https://github.com/fakoli/anvil-serving/blob/main/.tickets/2026-08-14-model-pull-active-writer-status.md); returned to managed inventory/lifecycle. | tracked product gap |
| Vision-enabled 262K startup: `bf16 linear: unsupported shape` | Profile unloaded. Text-only cleared that error; no unproven GDN cause asserted. Runtime compatibility watch item; no vision qualification. | deferred unsupported profile |
| Text-only 262K missed reservation floor | Headroom policy preserved; both final arms measured at text32K. Not labelled a mathematical impossibility for every engine/configuration. | excluded profile |
| First fresh Swift32K startup blocked by host-memory reserve | No quality score assigned. Managed reclaim and later ready endpoint passed fresh preflight; final job replaced the infrastructure-failed attempt. | recovered with readiness invariant |
| QUASAR omitted requested final status in 2/18 scenarios | Correct calls retained; independent acceptance checks expose the result-incorporation failure. No promotion. | retained model behavior |
| Frozen topological-sort pair representation ambiguous | Original list grade preserved; separately labelled tuple sensitivity changes only pair reconstruction, with identical cases/oracles/responses. Future fixture work must state the representation explicitly. | bounded interpretation gap |
| Swift15 varint behavioral failure | Full source and failing independent cases retained. A small fixture does not establish a general ranking. | retained model behavior |
| Background/cache activity not fully controlled | No speed win or capacity claim; no chart pack because comparable performance populations were not qualified. | explicit measurement limit |
| Native Windows SWE unsupported | Pure-function fixture reported separately; no SWE score. A supported worker is a future qualification gate. | deferred qualification gate |
| Full suite / documentation tooling | Full retry stopped at unrelated Pi process smoke after 7,932 passes/416 skips; earlier HTTP timeout retained. Documentation-skill tests 19/19 passed. Ruff/MkDocs unavailable. | incomplete software gates |
| Shared Hindsight recall/retain unavailable | Transport/tool unavailable; continued from verified live state and retained local evidence. No credentials or raw conversation stored. | external service unavailable |

Original model service, ownership and all five checksum settings were restored;
test containers were removed. Hash-bound raw artifacts retain original line
endings. Source copies and their transformations are independently checked in
[redaction-lineage.json](redaction-lineage.json).
