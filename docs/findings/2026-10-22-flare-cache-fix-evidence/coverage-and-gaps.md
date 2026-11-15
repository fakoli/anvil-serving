# FLARE-FIX-001 coverage and gaps

Closed campaign; all six formal arms and the three-pair matrix are retained.
The earlier frozen plans and request hashes remain the evidence for prospective
choices. The independent causal review accepts diagnostic support only for the
fixed-work replay; it does not clear the false numerical, oracle, or promotion gates.

| Requested outcome | Evidence condition | Current state |
| --- | --- | --- |
| Configurable continuation-row budget | Default preserved; validated startup knob through native and Ignition paths | Implemented locally and independently reviewed; 1–99%, omitted 25%; exact v2 source/build pins retained |
| Same 32 GiB budget | Native geometry and allocation for both arms | Verified: A 52 rows / 7,314 pages; B 158 / 2,434; both physical pools 34,359,738,368 bytes |
| Protected workload capacity | Exact native tokenization plus page demand | Verified by `audit-b-capacity.json`; 420 protected plus 105 warmup pages fit B's 2,434 pages |
| Transfer integrity | Actual model outbound/inbound page and row bytes on both ranks | A/B audits passed, zero mismatches; see `audit-*-transfer-audit.json` and independent reviews |
| Semantic and numerical correctness | Independent oracles and uncached/restored state equivalence | Strict arithmetic/JSON/tool checks pass in A/B. Exact 128-token sampled-output gate failed. Teacher-forced/logit and Restore/re-Snapshot state oracles remain unavailable |
| Lifetime under cancellation | Real concurrent restore cancellation/pressure plus safe drain | CPU ownership/error tests and the composed shared-fetch waiter-cancellation test pass (success/failure completion under pressure); see `lifetime-test-execution/r2/execution.json` and independent review. This later test delta is outside the frozen v2 image. Live model cancellation-under-pressure is not demonstrated. Existing telemetry cannot prove overlap/ownership; see `review/live-cancellation-feasibility.md` |
| Repeated performance comparison | All nine lanes, three paired repetitions, frozen order, all attempts | All six formal arms are complete: 2,550 formal requests (1,152 prime, 1,398 measured) plus 162 warmups across 54 fresh formal incarnations, with no formal request failures. Normalized, strict, and descriptive outputs exist. Independent review accepts fixed-work causal support only as a diagnostic; no performance result is accepted while numerical/fidelity/oracle gates remain false and protected/C4 limits persist. A failed r2-B pretraffic window (8.94875 minutes) sent zero formal warmups or targets and remains retained as a failed window |
| Monitoring follows model swaps | Exact new generation, healthy scrapes before traffic, raw exports | Audit incarnations/restoration verified. Formal r1-A actual pretraffic and restoration state verified retrospectively: three healthy scrapes in45s and fresh collection; original loop did not explicitly enforce actual-scrape count. r1-B strict monitoring is 1/9 because six first polls were empty and two retained post-traffic padding zeros; historical actual-pretraffic reads are 9/9. r2-B functional and monitoring proofs are 9/9, and r2-A/r3-A/r3-B each have 9/9 monitoring proofs. This does not establish arbitrary fleet-caller coverage or browser rendering |
| Power held constant | Same GPU power limits with telemetry | 300 W limits preserved; formal windows retain readings. Power is not assumed to explain the result |
| Incumbent recovery | Exact retained identity, direct/routed checks and monitoring | Pilot, audit, and all six formal windows restored. r1-A parent verification passed at 55.71 minutes; r1-B restoration observation was 53.0147 minutes; r2-B closeout was 49.736 minutes; r2-A restored at 50.8709167 minutes; r3-A restored at 51.0379667 minutes; r3-B restored at 49.7004166683 minutes with direct/routed seven-group checks including 20-tool batches and two accepted monitoring observations 45 seconds apart. r2-A/r3-A/r3-B restoration proofs are true. r2-B's original restoration proof remains false from an empty discovery receipt; `formal/r2-b/restoration-registration-refresh.json` separately captures a matching live generation accepted at 54.9174 minutes. These are historical verified states, not continuous or future health. Each formal window has a separate restoration gate |
| Reviewable contribution | Incremental diff, dependency statement, source tests and evidence | Packaging in `contribution/`; no upstream submission, commit or promotion |
| Report and hosted access | One derived summary, complete raw evidence links and verified served URL | Private final report and decision summary are complete; authorized public-safe inventory, privacy, size-policy review, packaging, and served-URL verification remain pending |
| Notebook and semantic lookup | Stable IDs, evidence links and verified shared retention | Notebook updated with FLARE-CONFIG-001, FLARE-TRANSFER-001, FLARE-COUNTERS-001 and FLARE-LIFETIME-001. Shared audit-result retention waits until benchmark traffic is finished; native memory unconfirmed |
| Repeatable process | Existing managed commands, frozen harness, failure checks and lookup guide | Reused existing product/skills. Negative readiness fixture corrected; token/selector conventions recorded in notebook AGENTS.md. No second operational plugin created |
| Kernel optimization or engine parity | Matched work, numerical oracle and actual critical-path evidence | Not established; later work, as specified by the original plan |

The protected fixtures cover 8,036, 16,036 and 29,036 prompt tokens under the
frozen 32,768-token workload ceiling. They do not qualify the package's full
1,048,576-token native context or simultaneous full-window C4 capacity.

Byte-audit timing is excluded from the performance matrix. A clean transfer
does not clear the failed numerical gate. The diagnostic amendment remains
binding: `diagnostic_only=true`, `performance_eligible=false`, `promoted=false`.
