# Flare C1 r3 post-run reconciliation

**Status:** terminal, failed strict output gate; 0/8 performance-eligible. This is a public-safe offline reconciliation of the frozen run, not a retry or a runner pass. The native CLI output, journal, and telemetry remain unchanged in private evidence.

## Request outcome

The native capacity command attempted eight serial requests. It recorded zero semantically accepted rows, eight failed rows, eight HTTP responses received, and no eligible timing population. The outer campaign wrapper retained its `native_control_mismatch` failure.

| Request indexes | Native result | Retained output evidence | Eligibility |
|---|---|---|---|
| 0, 1, 3, 5, 6 | `stream_validation_failed` | No visible content; `finish_reason=length`; each used 16,384 completion tokens and had 16,034–16,360 reasoning chunks. | Failed; no visible-output canary result or response timing was available. |
| 2 | `controlled_output_failed` | Canary passed; 96 code words, zero extra words. | Failed strict 128-word contract. |
| 4 | `controlled_output_failed` | Canary passed; 150 code words, zero extra words. | Failed strict 128-word contract. |
| 7 | `controlled_output_failed` | Canary passed; 121 code words, zero extra words. | Failed strict 128-word contract. |

The wrapper expected `capacity-v4-reasoning`, while the native CLI selected `capacity-v3` because no semantically accepted rows contributed aggregate reasoning chunks. That selection matches the pinned CLI rule. The wrapper also required canary and timing objects for every response-backed failure, although the five no-visible-content rows fail before those fields exist. These findings explain the wrapper's validation mismatch; they do not convert the native strict-output failures into passes.

## Accounting and resource boundary

Independent post-run reconciliation matched eight admitted requests, eight received HTTP responses, and eight server-side completed requests. Response-backed prompt usage totaled 74,361 tokens, exactly matching 74,025 prefill plus 336 cache-reused prompt tokens. The generated-token counter delta was 108,467 and is descriptive only.

The native result has zero aggregate timing samples and zero eligible requests. No throughput, latency distribution, or decode result is available. Failure-row elapsed time is not substituted for response timing.

Post-run gauges showed running, waiting, and HTTP-inflight all at zero. Sampled cgroup OOM, OOM-kill, and group-kill counters remained zero. During the run, selected-GPU memory was sampled at 97,211 MiB used of 97,887 MiB, with 131 MiB free; this is resource context, not capacity or performance evidence. The reported cgroup peak is lifetime-scoped, not a run-only peak.

## Interpretation

The native strict quality gate failed for all eight requests. The wrapper failure remains recorded as `native_control_mismatch`; the offline reconciliation does not resubmit or rewrite the frozen run. This cell provides no speed ranking, model-quality pass, or promotion evidence. Exact prior evaluation-route/output-cap configuration and GLM incumbent readmission passed local checks. The router runtime uses the reviewed PR #704 overlay, not the original image; remote-memory terminal remains unknown and drained is false.
