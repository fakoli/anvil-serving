# Request coverage

| Request | Evidence | Outcome |
|---|---|---|
| Research recipes in the usual places | source-registry.json, research-synthesis.md, runtime-alternatives.md, independent-review.md | Completed bounded official/HF/runtime/community search. No external matched v2.6 TP2/SM120 quality result found. |
| Test MiMo | Three versioned managed recipes, startup logs, partial greedy preflight, native official-sampling diagnostic | Final recipe loaded and answered requests, but both completed correctness probes failed. |
| Benchmark as a GLM replacement | GLM strict C4 final 100/100; MiMo correctness evidence | Comparison blocked by MiMo correctness. No valid MiMo capacity, speed, long-context, vision or repository-task result. |
| Coding evidence | GLM SWE native and official reports | Official 0/1 unresolved; bug-specific test passed, two HTTP 502 failures and legacy pytest API error confound attribution. Single task is not a coding ranking. |
| Preserve service | restoration.json and final direct/routed checks | Exact GLM model/image/checkpoint/config restored; six direct checks and authenticated router smoke passed. |

Configured context is not measured capacity. MiMo requested/advertised 327,680 tokens while the final recipe allocated 91,342 full-attention KV tokens. A source-reviewed SWA-ratio adjustment remains unrun because correctness failed first. No promotion was authorized or performed.
