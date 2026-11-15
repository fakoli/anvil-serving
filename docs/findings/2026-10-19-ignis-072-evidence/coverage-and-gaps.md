# Request-to-evidence coverage

| Requested outcome | Retained evidence | Status | Claim allowed | Remaining gap |
|---|---|---|---|---|
| C1 protocol | `raw/preflight-c1.json` | covered | Five named C1 preflight checks passed. | This is not a capacity or client-acceptance claim. |
| C8 tool admission | `raw/preflight-c8.json` | covered | One explicit shared-prefix tool batch completed 8/8. | No general concurrent-capacity or throughput claim. |
| Long context | `raw/preflight-long-context*.json` | partial | r2 retained two passing probes at 100,739 and 103,256 actual prompt tokens. | Original failure remains; no 131,072-token full-window claim. |
| Long response / timeout transport | `raw/contract-long-answer.json` | partial | A response ran 112.120 s and ended at the 16,384-token cap. | It did not cross the former 120-second default or exercise deadline expiry. |
| Cancellation | `raw/contract-disconnect.json` | partial | Client cancellation and subsequent recovered requests are retained. | Interpret server-side lane correlation only with the paired logs; not a quality score. |
| SWE fixed five | `raw/swe-stage-initial.json` | partial | Initial attempt failed before any model request because its harness client dependencies were incompatible. Corrected run attempted five cases: one official grade is unresolved; four empty submissions are ungraded. | Incomplete native evidence; do not state a fully graded 0/5 score. |
| Exact restoration | `restoration.json` | covered | Candidate removed; incumbent direct and routed five-check preflights passed. | No candidate promotion follows. |
| Serving performance | none | not collected | None. | No matched controlled-output workload or eligible comparison population. |
