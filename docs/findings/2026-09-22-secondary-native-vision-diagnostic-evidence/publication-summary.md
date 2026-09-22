# Publication summary: Huihui Qwen3.8 secondary native vision diagnostic

<!-- benchmark-publication-summary/v1 -->

This is derivative publishing copy. The [dated finding](../2026-09-22-secondary-native-vision-diagnostic.md) and linked retained artifacts are authoritative.

## Canonical facts

- **Finding identity:** [2026-09-22 secondary native vision diagnostic](../2026-09-22-secondary-native-vision-diagnostic.md), SHA-256 `e5fb472c00a93b1d2787cd884b8409798db05e7f4ef13e45cfcecf0e30f2cc9d`, 2,821 bytes
- **Status:** `compatibility-only`, `not-qualified`, `no-promotion`
- **Diagnostic:** 12 frozen synthetic one-image cases at C1, a 1,024-token completion limit, and temperature 0; 11 of 12 exact whole-answer assertions passed
- **Retained failure:** `adversarial-2` expected `Payment pending | 402` and returned `PAYMENT PENDING | Invoice 402`, retaining an extra `Invoice` label
- **Request policy:** the evaluator omitted `chat_template_kwargs` after `thinking_mode=unsupported`; the evidence policy forbade reasoning output. This is a request-policy observation, not a runtime reasoning limitation.
- **Separate preflights:** explicit `enable_thinking=false` was denied before inference; the native-policy 512-token case reached `length`; the native 1,024-token policy preflight passed with `stop`
- **Measurement limits:** all 12 diagnostic requests ended with `stop`, without reasoning output or transport error. Sequential full-request latency is descriptive only; cold/warm state, runtime controls, TTFT, decode rate, concurrency, and capacity are not recorded.
- **Decision:** this diagnostic does not qualify a model, select a configuration, authorize an action, or support promotion.

## Claim ledger

| Public claim | Conditions | Evidence |
| --- | --- | --- |
| 11/12 exact whole-answer assertions | 12 frozen synthetic one-image cases at C1 and a 1,024-token completion limit | [Finding outcome](../2026-09-22-secondary-native-vision-diagnostic.md#outcome) |
| `adversarial-2` retained an extra `Invoice` label | Expected `Payment pending \| 402`; observed `PAYMENT PENDING \| Invoice 402` | [Finding outcome](../2026-09-22-secondary-native-vision-diagnostic.md#outcome) |
| Three native preflights have distinct outcomes | One explicit thinking request was denied before inference; 512-token native policy reached `length`; 1,024-token native policy passed | [Finding configuration and preflights](../2026-09-22-secondary-native-vision-diagnostic.md#configuration-and-preflights) |
| Compatibility-only, no qualification or promotion | Sequential direct diagnostic; runtime and measurement limits remain unrecorded | [Finding limitations](../2026-09-22-secondary-native-vision-diagnostic.md#measurement-limitations-and-retained-evidence) · [artifact manifest](artifact-manifest.json) |
