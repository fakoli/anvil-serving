# r1-B independent closeout review

Decision: **accept r1-B as a completed diagnostic window with verified retained-incumbent restoration and explicit evidence limitations**. This does not qualify performance, numerical correctness, promotion, or every monitoring lane. Read-only review; no model/Prometheus requests, lifecycle, source changes, builds, or performance analysis. Only this note was written.

## Native completion and runner exception

Independently totaled all 27 native phase summary/attempt files: **425 target requests and 27 warmups**, expected = attempted = completed, no reported missing/error, matching attempt counts. Nine distinct retained registration generations and nine lane-complete receipts exist. Every wrapper summary reports ok=true, child_returncode=0, error=null and audit=false. Each lane has a successful managed unload receipt. These support native completion separately from the unavailable outer runner exit status.

`decode-c4/lane-complete.json` explicitly records root completion after the runner PID disappeared and its PTY status was unavailable; root performed managed unload after the native 4/4 request completion. The captured complete runner source/provenance and the earlier incomplete fragment are both retained. Do not convert this into a clean outer-runner exit claim or infer that the outer process succeeded. Closeout and parent-review correctly retain this exception. No request replay is needed to resolve that missing process receipt.

## Restoration

Raw `restore.json` records successful managed exact retained-ID restoration. `final-incumbent.json` reports the expected container ID `container-33`, image `sha256:745feb916ee816f5409fd58386114be4f22fbede7f9c0e0a7b8c4fe8dac0fb29`, running=true, restart_count=0, no OOM-kill and zero memory OOM events. `final-readmit.json` reports applied/readmitted=true and the declared primary tier ready/admitting with its expected model identity.

Both retained preflight files independently contain seven passing result groups, 27 passing observations, and all twenty unique tool indices 0..19. Direct uses the retained incumbent served alias; routed uses llm.primary. The final engine target generation matches `final-incumbent-generation.txt` and both accepted monitor observations.

At epochs **1792716149.864** and **1792716194.883** (45.019 seconds apart), retained monitoring reports up=1, actual scrape_count=3, scrape_min=1, GPU collection freshness=1, and sample ages 12.697 and 12.716 seconds. Historical restoration proof evaluates the second epoch with the same generation and five raw query responses. The second observation is **53.0147167 minutes** after the frozen window start, within the 90-minute deadline. The initial empty restoration poll remains separately preserved with its unknown original evaluation time recorded; it is not counted as a scrape. These raw receipts support the parent's restoration checks and the reporter's restoration=true proof. They establish the recorded closeout state, not a new live observation during r2.

## Reporter proofs and remaining gates

Read all nine final lane proof manifests/decisions and verified their referenced proof-file SHA-256 values. Functional=true for all nine is consistent with the previously reviewed bounded scope: arm-specific strict-three pilot plus native formal output/accounting contracts; it is not an independent semantic rescore of formal targets. Restoration=true is supported above. Independent-oracles, restore-fidelity and causal-chain remain false for every lane; sampled-output numerical acceptance remains failed. Diagnostic/performance-eligible=false/promoted=false labels are retained.

Monitoring is **1/9 true**, for fixed-work only. Preserve all eight false receipts:

- original-reverse, original-forward, small12-original, cold, decode-c1 and decode-c4 retain an empty up-1 response, so no two-sample monitoring JSONL was fabricated. Historical queries at warmup show three actual successful scrapes, but the current strict proof still lacks its required second retained observation. Historical reconstruction does not retrospectively prove that the original runner enforced readiness.
- small12 and pageheavy have two nonempty successful retained observations but fail the strict full-export predicate on up=0 after traffic. In the raw identity ranges, small12's zero is epoch1792714789, after its last wrapper completion1792714780.272; pageheavy's zero is1792715024, after completion1792715021.071. These are post-traffic padding samples. Retain the failed strict proof; do not label these two samples an observed in-traffic outage. No predicate change or new analysis framework is requested.

`analysis/r1-b-scoped-monitoring-collection.json` records the direct lane-only collection, first-attempt preserved history/ranges, actual-only sample reconstruction, and separately collected restoration history. This matches the reviewed narrow collection scope; whole-collector HOLD remains. Closeout's “collection underway” wording is an earlier snapshot: final proof outcomes above now exist and should be linked in the closure narrative without erasing that history.

No operational restoration blocker was found in the retained receipts. The unresolved monitoring/process receipts and failed numerical gate must remain visible in campaign reporting and denominators.
