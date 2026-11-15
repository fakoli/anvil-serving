# Cache-layout and DFlash scouts: retain the current configuration

**Date:** 2026-11-01 UTC. **Decision:** `no-promotion`; retain the existing configuration.

<!-- benchmark-result-card/v1 -->

## Result card

The two local runtime experiments completed on the dual RTX PRO 6000 Blackwell
Max-Q pair. Neither demonstrated a 10% end-to-end latency improvement against
both its original-before and restored-original controls. The original model
and route were restored after each trial. The target model selection is private.

| Setup | Recorded value |
|---|---|
| Model | Same private target within each trial; repository, revision, served identity and asset fingerprints withheld |
| Hardware | Two RTX PRO 6000 Blackwell Max-Q GPUs, 96 GB class each, TP2/DCP2, native Linux; 300 W limits |
| Runtime | Same pinned vLLM fork within each trial; FP4 target weights, FP8 target KV; exact image withheld |
| Recipe | [Sanitized configuration and reproduction boundary](2026-11-01-runtime-scouts-evidence/reproduction.md) |
| Measurement path | Direct online endpoint; standardized warmup, unique synthetic prompts; routed restoration checks separate |
| Workload | Approximately 6K/25K actual input tokens, requested concurrency 1/4, max reasoning requested, temperature 0, 4,096-token output cap |
| Evidence | `functional`, bounded `capacity` scout; natural output, no request canaries or controlled-decode qualification |
| Decision | Retain original MTP3 configuration; neither candidate promoted |

| Headline measurement | Local result | Conditions |
|---|---:|---|
| Timed requests completed | 144/144 in each trial | 48 before, 48 candidate, 48 restored; no request failures |
| Material latency wins | 0/4 workload groups in each trial | At least 10% lower pooled median E2E required against both controls |
| Cache-layout token slots | +38.60% | 1,330,579 to 1,844,162 at the same fixed KV byte budget |
| DFlash2 K3 token slots | −40.63% | 1,330,579 to 789,904 at the same fixed KV byte budget |
| Original configuration restored | Both trials | Identity checks, direct/routed checks and readmission passed |

**Why it matters:** more reported cache slots did not make the layout candidate
faster. DFlash was compatible with this target/runtime/hardware combination,
but its BF16 draft KV reduced the available token pool without a material
latency win. Keeping the existing configuration is the supported local decision.

**Important caveat:** these are small natural-output scouts, not controlled
speculative-speedup, coding-quality, full-context, endurance or p99 evidence.
The withheld model identity prevents independent public reproduction and
transfer of the result to a named checkpoint.

[Artifact manifest](2026-11-01-runtime-scouts-evidence/artifact-manifest.json) ·
[Evidence index](2026-11-01-runtime-scouts-evidence/README.md) ·
[Dossier](../benchmarks/models/private-runtime-scouts.md) ·
[Measured hardware](../benchmarks/hardware/rtx-pro-6000.md)

## Configuration and method

Both experiments held target weights, runtime, TP2/DCP2, C4 scheduler, FP8 target
KV, batch limit 4,096, the 52 GiB container RAM limit and no container swap fixed.
The KV allocation remained **7,650,410,496 bytes per GPU**.

- **Cache-layout trial:** MTP3 throughout; only internal maximum context changed
  from 327,680 to 1,048,576, with isolated endpoint labels. No external drafter
  was loaded. The larger setting changes allocation/accounting; it does not
  establish a measured million-token request or a new client context contract.
- **DFlash trial:** 327,680 configured context throughout; MTP3 was replaced by
  DFlash2 K3, draft TP2, TRITON_ATTN, BF16 draft KV and standard rejection
  sampling. Positive speculation counters and stable process identity were
  checked. Higher draft lengths and other cache precisions were not tested.

Each experiment ran original → candidate → restored original. Each arm had
three repetitions of the four input/concurrency combinations, four requests
per repetition. The native nominal 8K/32K targets produced **6,065–6,068** and
**25,279–25,281** actual input tokens. Prompts and seeds matched across arms;
responses were synthetic one-line summaries with natural, variable output
length. The 144 timed requests per trial exclude warmup and preflight.

The tables pool twelve request timings per entry using the ordinary median.
The charts retain each four-request native run's nearest-rank p50, so their
points have a different aggregation. Visible-content TTFT is distinct from
the first reasoning-or-content output; both are retained in native evidence.

## Cache-layout results

Median end-to-end seconds; lower is better. These controls belong only to the
cache-layout experiment.

| Actual input | Concurrency | Original before | Layout candidate | Original restored |
|---|---:|---:|---:|---:|
| ~6K | 1 | 1.735 | 1.819 | 1.761 |
| ~6K | 4 | 5.222 | 4.958 | 4.672 |
| ~25K | 1 | 4.129 | 4.107 | 4.210 |
| ~25K | 4 | 11.355 | 12.404 | 11.494 |

At ~25K/C4 the layout candidate was 9.25% slower than the first control and
7.92% slower than the restored control. Its higher token throughput accompanied
more generated tokens, so it is not evidence of faster responses. Engine-reported
cache slots increased 38.60%, then returned to the original value after restoration.

[Native-derived summary](2026-11-01-runtime-scouts-evidence/cache-layout/summary.json) ·
[Per-run chart](2026-11-01-runtime-scouts-evidence/cache-layout/comparison.svg) ·
[Chart data and source hashes](2026-11-01-runtime-scouts-evidence/cache-layout/graph-data.json)

## DFlash results

Median end-to-end seconds; lower is better. These are separately measured
before/after controls, not the cache-layout controls.

| Actual input | Concurrency | Original before | DFlash2 K3 | Original restored |
|---|---:|---:|---:|---:|
| ~6K | 1 | 1.934 | 1.830 | 1.952 |
| ~6K | 4 | 5.061 | 4.962 | 5.807 |
| ~25K | 1 | 4.104 | 4.195 | 4.204 |
| ~25K | 4 | 11.893 | 12.007 | 12.085 |

The ~6K/C4 result improved 14.55% against the restored control but only 1.96%
against the first control. It therefore fails the predeclared requirement to
improve at least 10% against both. None of the four groups met that requirement.

DFlash reached four sampled active requests and retained at least 2,143 MiB
free GPU memory in the measured samples, with no OOM kills. The reported cache
pool fell 40.63%; BF16 draft KV shares the fixed byte budget with target KV.
This tradeoff describes this exact trial configuration, not all DFlash recipes.

[Native-derived summary](2026-11-01-runtime-scouts-evidence/dflash/summary.json) ·
[Per-run chart](2026-11-01-runtime-scouts-evidence/dflash/comparison.svg) ·
[Chart data and source hashes](2026-11-01-runtime-scouts-evidence/dflash/graph-data.json)

## Gates, restoration and evidence limits

All 72 timed batches across the two experiments reconciled request, prompt-token
and generated-token counters. Direct smoke, JSON, needle, tools, streaming-tools
and tool-result checks passed. Restored direct and routed checks passed. The
original identity/recipe/image/endpoint/GPU bindings matched, candidates were
removed and the original route was readmitted after each trial. These are dated
restoration records, not a live status endpoint or a new fleet acceptance claim.

The cache-layout trial sampled engine occupancy up to four before/in the
candidate and three after restoration; the DFlash trial sampled four in every
arm. Client concurrency and sampled engine occupancy are separate measurements.
Configured context, reported KV slots and observed short-request C4 do not prove
four simultaneous maximum-context requests. Container lifetime RAM peaks are
retained but do not establish memory savings because container lifetimes differ.

Natural output length, absent request canaries, small sample populations and no
matched no-speculation arm prevent controlled speculative-speedup claims. No
hard coding, vision, endurance, full-window capacity or general model-quality
qualification was performed. The native `performance_eligible` field reflects
the historical observe-mode runner; it does not override these publication limits.

Private model/draft identities, asset fingerprints, endpoint values, GPU UUIDs,
container IDs and machine paths are withheld. Sanitized native timing envelopes
and journals preserve measurements and schemas; journal hashes were recomputed
after redaction. Telemetry retains numeric resource/counter data and omits
private container identity. Restoration identity equality is an operator-reviewed
receipt, not publicly reproducible asset attestation. See the
[redaction and reproduction boundary](2026-11-01-runtime-scouts-evidence/reproduction.md).

The independent review reproduced the measured medians and agreed with the
no-promotion decision. This publication does not install the isolated recovery
runner changes, upgrade vLLM, change the model or deploy another draft.

## What remains worth testing

Retain MTP3 and the original cache layout. A future experiment would need a
specific workload benefit, controlled output, request canaries and a matched
no-speculation control before claiming speculative acceleration. Higher draft
lengths, different draft-cache precision and long-context capacity remain
unmeasured ideas, not recommended deployment changes.
