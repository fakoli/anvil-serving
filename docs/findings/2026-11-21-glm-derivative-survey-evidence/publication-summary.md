# Publication summary: GLM derivative survey

<!-- benchmark-publication-summary/v1 -->

The [dated finding](../2026-11-21-glm-derivative-survey.md) and retained raw
receipts are authoritative.

## Canonical facts

- **Headline:** the matched local GLM pair tied at 49/60; AZ-Focus used 55.83%
  less *summed individual sequential C1 request time* (215.195 s versus
  487.233 s).
- **Boundary:** this is neither outer-process wall time nor a throughput,
  time-to-solve, capacity, accuracy-uplift, or promotion result.
- **Important caveat:** the paired SWE result is only five official images and
  broad capacity/concurrency remains unmeasured; the pre-existing monitoring
  registration gap is retained.
- **Decision:** `no-promotion`; no deployment or default changed.
- **Evidence:** [evidence index](README.md) and
  [artifact ledger](artifact-manifest.json).

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| 49/60 tie and summed request-time result | fixed 60-case local subset; same parser and 16,384-token cap | [`raw/mia-vs-az327-60.json`](raw/mia-vs-az327-60.json) |
| Bounded C4 observations are not a broad speed result | separate unique-cache, eight-request pairs | [`graphs/paired-c4-matrix-data.json`](graphs/paired-c4-matrix-data.json) |
| QAD R3 bounded multimodal and agentic receipts | one NVIDIA GPU; no timing claim | [`qad-r3-capability-summary.json`](qad-r3-capability-summary.json) |
