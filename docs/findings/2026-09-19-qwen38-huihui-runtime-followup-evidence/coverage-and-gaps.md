# Request coverage

| Request | Retained evidence | Disposition |
|---|---|---|
| Research exact model and try settings | Sources; old/new runtime; off/low; instruction variant; NoSpec/MTP;8K/32K | Original failure recovered without changing weights or assertions. |
| Correctness | Repeated core quality, protocol, image corpus | Core and images pass. Exact CRLF and128-word counting fail for both candidate and GGUF control. |
| Speed versus previous Qwen | Three strict32-word8K/C1 cells with12 requests each | Candidate MTP is faster in this narrow workload; no broad application claim. |
| Better footprint | Post-workload GPU snapshots at equal8K allocation | Not met: candidate MTP21852MiB versus GGUF19022MiB. |
| Larger context |32K managed recipe; nominal24K retrieval; actual22-23K capacity | Bounded success, not full-window endurance or established262K replacement. |
| Promotion | No route switch | Withheld: footprint criterion not met and production qualification incomplete. |
| Repeatable deployment and observability | SHA-verified managed recipes; prior infrastructure inspection | Cached runtime reload works. Immutable build, NInfer telemetry, client qualification and private deployment convergence remain unperformed. |
| Publish evidence | Native files, dated finding, indexes, graph pack | Local reviewable documentation; no external post or Git push. |
| Restore media | Restoration receipt and private managed status | Both services runningHTTP200, original stopped incumbent unchanged, GPU686MiB. |
