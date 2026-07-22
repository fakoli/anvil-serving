# ADR-0008 raw-evidence gap — 2026-07-22

- **Related:** [ADR-0008](../adr/0008-heavy-tier-speculative-decoding.md),
  [ADR-0027](../adr/0027-public-findings-are-durable-evidence.md),
  [issue #175](https://github.com/fakoli/anvil-serving/issues/175)
- **Evidence type:** public provenance correction / missing-artifact record
- **Historical decision date:** 2026-07-02
- **Correction date:** 2026-07-22

## Gap

ADR-0008 reports a live NEXTN speculative-decoding A/B on Qwen3.5-35B-A3B AWQ, including
throughput, TTFT, draft acceptance, cache-hit, error, and restart observations. Its raw benchmark
logs were not committed to the public product repository.

The #175 audit fetched `fakoli/anvil-serving-notes` through
`7b46ceb6ae62252f8f808f6c065706a24e7970bb` and searched all refs for the ADR's exact throughput
values (`187.81`, `268.15`, `486.16`, `630.64`), `96.2%` cache-hit observation,
`Qwen3_5ForCausalLMMTP`, and the four-draft-token flag. No matching narrative or raw artifact was
present. No prior byte size or SHA-256 digest is available because no artifact path was recorded.

## Consequence

The measurements remain an attributed historical observation in the ADR, but external readers
cannot independently recompute them. They must not be used as current qualification or promotion
evidence for another checkpoint, engine build, quantization, GPU topology, or routing policy.
Equivalent future decisions require a fresh sanitized narrative and bounded raw artifacts under
ADR-0027. This record does not fabricate replacement evidence or imply private access can fill the
gap.

No product or serve configuration changes as a result of this provenance correction.
