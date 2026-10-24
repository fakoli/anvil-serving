# r1-B monitoring limitations

This note explains the written `formal/r1-b/*/monitoring.json` results from the approved assembler. It does not alter the receipts or rerun collection.

All nine lanes have retrospective readiness history at the exact warmup-plan epoch. Each history receipt has one successful `up`, `scrape_count`, `scrape_min`, `sample_age`, and collection-freshness vector: `up=1`, `scrape_count=3`, `scrape_min=1`, collection freshness `=1`, and sample age within the assembler's 0–30 second bound. This is actual later Prometheus evidence about the retained historical timestamp; it is not proof that the original runner enforced a readiness gate at the time.

| Lane(s) | Assembler result | Exact limiting condition |
| --- | --- | --- |
| `original-reverse`, `original-forward`, `cold`, `decode-c1`, `decode-c4`, `small12-original` | `generation_up_freshness_and_traffic_ranges=false` | No `monitoring-samples.jsonl` was written. Each retained `up-1.json` is a successful query with an empty discovery result, while `up-2.json` has one `up=1` value. No second actual successful sample exists, so reconstructing one would fabricate monitoring evidence. The assembler stops on the missing sample file before judging range coverage. |
| `small12`, `pageheavy` | `generation_up_freshness_and_traffic_ranges=false` | Both have two actual retained `up=1` samples with path/hash provenance, and their readiness histories pass. Their `query-ranges/identity.json` series nevertheless ends with `up=0`: `small12` at `1792714789`, `pageheavy` at `1792715024`. The assembler rejects any retained `up` or GPU collection-success value other than 1, so these range exports fail despite the sample pair. |
| `fixed-work` | passed | It has two actual retained successful samples and retained identity, engine, freshness, and GPU/host range coverage with the required values. This is lane-scoped retrospective monitoring evidence only. |

The empty discovery polls remain retained in the scoped collection receipt with their raw paths and SHA-256 values. The raw range exports remain retained for every lane; absence of a sample file does not mean their telemetry was discarded or that it would necessarily fail later checks. The next runner's explicit count/min/age readiness checks and complete range exports are required to resolve these B limitations prospectively.
