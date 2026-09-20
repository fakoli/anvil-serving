# Qwen3.8 context-envelope investigation

**Date:** 2026-09-19

**Status:** the 160K/C1 profile is qualified on the measured RTX 5090. This report recommends a measured reference configuration; deployment state is retained privately.

<!-- benchmark-result-card/v1 -->
## Result card

| Setup | Result |
|---|---|
| 64K baseline | Earlier qualification retained for comparison |
| 128K direct-I/O | Preflight, repeated quality, unique long capacity and vision passed |
| Final 160K baked profile | Preflight 8/8, quality three repetitions, vision 18/18, retrieval 9/9, short capacity 12/12 and long capacity 3/3 |
| Larger estimates | 192K and 204800 are policy-infeasible under the recorded 4 GiB free floor; not unconditional physical impossibilities |
| Reference recommendation | 163,840 total context tokens, one active request, 8,192 output allowance |

Evidence: [bundle README](2026-09-19-qwen38-context-envelope-evidence/README.md), [canonical manifest](2026-09-19-qwen38-context-envelope-evidence/artifact-manifest.json), and [publication summary](2026-09-19-qwen38-context-envelope-evidence/publication-summary.md).

The 128K default host-cache profile passed capability checks but exposed startup cache pressure. A 2 GiB host-cache variant loaded but failed the memory-reserve gate. The subsequent direct-I/O verifier avoided the observed cache pressure: the 128K direct-I/O gate, three-repetition quality, unique approximately 117K capacity, and 12-image vision evidence passed. These are local bounded observations, not a causal throughput claim.

The final baked 160K profile uses the retained NInfer source revision, image digest, and binary hash in the sanitized recipe reconstruction. Direct preflight passed 8/8, including a 144,425-token needle and a 147,941-token tool call. The final long-depth suite passed exact retrieval 9/9: three repetitions each at 150,058, 150,144, and 150,124 prompt tokens; the maximum retained input is 150,144 with an 8,192-token output allowance and approximately 4.5 GiB GPU free at startup. Each first request was cold or partially cached; later requests were warm, so these nine attempts are not a nine-sample cold-latency or throughput population. The request used an 8,192-token visible-output cap; it does not establish an 8,192-token generated output. Startup minima were 4,606 MiB GPU free and 14,165.406 MiB Windows available. During long-capacity qualification, the minima were 4,598 MiB and 13,669.93 MiB respectively. Final quality passed three repetitions, vision passed 18/18, and the short unique-canary capacity cell passed 12/12 at 4K with exact 32-word outputs.

Native Pi, OpenClaw and Hermes tool compatibility passed bounded probes. One macOS shell-marker failure remains unexplained; a separate read-tool probe passed. See [client compatibility](2026-09-19-qwen38-context-envelope-evidence/client-compatibility.json).
