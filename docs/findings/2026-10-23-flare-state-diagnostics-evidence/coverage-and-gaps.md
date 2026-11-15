# Coverage and gaps

| Requested outcome | Evidence | Status | Limit |
| --- | --- | --- | --- |
| Isolate protected regression | `analysis/protected-abba-2.json` | Complete matched A/B/B/A diagnostic population | Does not replace prior 3% protected threshold or qualify performance |
| Check native state roundtrip | `analysis/state-logprob.json` | Reduced-geometry signed-package roundtrip passed | No host offload, full logits, or exact-128 proof |
| Localize sampled distribution difference | `analysis/state-logprob.json` | Four top-20 observations retained | Case 51 has MTP batch-shape confounder; no causal attribution |
| Observe scheduler behavior | `analysis/trace-paired-1/trace-paired-1-summary.json` | 72/72 measured requests and 144 samples | No HTTP-to-slot/generation mapping; graph mixtures differ; not request timing |
| Numerical qualification | prior campaign and `review/final-trace-causal-review.md` | Not covered | Needs raw-logit or independent teacher-forced package |
| Promotion/default change | `summary.json` | Not covered and not authorized | `promoted=false` |
