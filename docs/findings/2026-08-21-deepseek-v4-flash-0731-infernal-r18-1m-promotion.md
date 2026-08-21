# DeepSeek V4 Flash 0731 Infernal Invocation r18 1M qualification

**Date:** 2026-08-21
**Decision at publication:** qualified for the operator-authorized guarded
promotion; the live router transaction and Mini client acceptance remain
separate acceptance steps.
**Measured hardware:** two NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation
Edition cards in exclusive TP=2 over PCIe without NVLink, under Windows 11,
Docker Desktop, and WSL2.

## Result

Martin Vit's Infernal Invocation r18 recipe was translated to the managed
Anvil Serving lifecycle, pinned byte-for-byte at its upstream revisions, and
qualified locally with the requested 1,048,576-token context and fixed
probabilistic DSpark K5 configuration. The target passed the complete direct
functional gate, repeated deterministic agentic checks, a 160-call structured
tool soak, client-shaped reserve probes, short and long concurrency, calibrated
retrieval through 1,040,063 actual prompt tokens, a matched no-spec control,
and a clean post-reload gate.

The engine reported 1,323,176 GPU KV tokens, or 1.26 full configured windows,
with eight admitted sequences. Router admission is intentionally bounded to
one full-window request at a time. Native KV offload and LMCache are disabled.

## Immutable upstream identity and credit

- Source recipe: [`ds4dspark-infernal-invocation-r18.md`](https://github.com/local-inference-lab/rtx6kpro/blob/12272507e64752af7a6cde28540edf8ee6c20e59/models/ds4dspark-infernal-invocation-r18.md),
  `local-inference-lab/rtx6kpro` commit
  `12272507e64752af7a6cde28540edf8ee6c20e59`.
- Compose source: [`docker-compose-ds4-infernal-invocation-cu133-r18.yml`](https://github.com/local-inference-lab/blackwell-llm-docker/blob/203e1a11a373a0486897a5a284409ba4417d0f7a/examples/docker-compose-ds4-infernal-invocation-cu133-r18.yml),
  `local-inference-lab/blackwell-llm-docker` commit
  `203e1a11a373a0486897a5a284409ba4417d0f7a`.
- Upstream author/publisher: Martin Vit (`voipmonitor`). The local result is an
  independent WSL2 qualification and does not transfer or expand the upstream
  native-Linux receipt.
- Model: `deepseek-ai/DeepSeek-V4-Flash-0731` revision
  `9e165c30e2704aec5d9d593cce3eebd58bbef1cb`.
- Runtime image:
  `voipmonitor/vllm:infernal-invocation-vllmf0fa1ce-b12x75787c7-fi1ac6942-cu133-torch213-20260818-r18`,
  registry digest
  `sha256:414ec7d0d28358cfd8af0697f330f5c8acbb80e4dc4e5ba69c9fd5b5855ea804`.
- Runtime trees: vLLM `f0fa1cefc1865d316c2478525f550e7646addc40`,
  B12X `75787c7a7431b3bea414d2ebf5f2b8671b23eb33`, and LMCache
  `e045d729bc5c4c63a40e13d032f42923de97812f`.

The upstream receipt physically qualified a 262,144-token configuration. Its
1M scheduler shape was therefore an external recipe prior, not proof of local
1M execution. This finding supplies the missing local full-window and
client-shaped evidence.

## Exact translated configuration

| Layer | Qualified value |
|---|---|
| Target | B12X W4A8 with FP8 compressed MLA KV and FP32 sliding-compressor state |
| Parallelism | TP=2, DCP=1 |
| Context | `MAX_MODEL_LEN=1048576` |
| Scheduler | `MAX_NUM_SEQS=8`, `MAX_NUM_BATCHED_TOKENS=4096` |
| Memory | `GPU_MEMORY_UTILIZATION=0.975`; model load 81.09 GiB per rank |
| Speculation | fixed probabilistic DSpark K5; FULL target, draft, and context-KV graphs |
| Transfer | InstantTensor `BUFFERED` |
| Cache | prefix cache enabled; native KV/L2/LMCache off |
| WSL2 | direct NCCL P2P and NCCL cuMem device/host allocation disabled; expandable segments disabled |

The checked-in [K5 recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-infernal-r18-b12x-dspark5-maxseq8-batch4096-1m-recipe.toml)
and [matched no-spec control](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-infernal-r18-b12x-nospec-maxseq8-batch4096-1m-recipe.toml)
pin the complete managed configuration.

## Functional, quality, and tool gates

- Direct preflight passed coding smoke, structured JSON, a 32K needle, tools
  20/20, streaming tools, tool-result continuation, and the Responses subset.
- Repeated deterministic quality passed intelligence 6/6, session 3/3, and
  tools 3/3 with zero recorded failures at 512 visible tokens plus 4,096
  reasoning-headroom tokens.
- Eight additional shared-prefix batches passed 160/160 structured tool calls.
  A bounded post-soak log window contained zero xgrammar/FSM, traceback, OOM,
  HTTP 4xx/5xx, or error-level entries.
- After the matched no-spec control, K5 was reloaded through the managed
  exclusive-mode transaction and again passed smoke, JSON, tools 20/20,
  streaming tools, tool-result continuation, and Responses.

## Context and client-shaped capacity

| Workload | Actual prompt tokens | Result | Request time |
|---|---:|---:|---:|
| calibrated retrieval | 354,010 | pass | 60.882 s |
| calibrated retrieval | 585,118 | pass | 92.087 s |
| calibrated retrieval | 900,118 | pass | 175.999 s |
| near-limit calibrated retrieval | 1,040,063 | pass | 187.347 s |
| Hermes-shaped reserve, 20K / max 5,120 | 14,357 | pass | retained privately |
| OpenClaw-shaped reserve, 32K / max 8,192 | 23,453 | pass | retained privately |

The near-limit request returned the exact needle with a visible answer and
`finish_reason=stop`, leaving approximately 8.5K tokens of combined context
and output headroom. It proves local execution near the declared window; it is
not a broad 1M reasoning-quality score.

## Performance and matched speculative A/B

All A/B rows use the same checkpoint, image, TP/DCP, context, scheduler,
batching, KV format, and request shape. Only DSpark K5 is changed.

| Workload | K5 median decode | No-spec median decode | K5 median E2E | No-spec median E2E |
|---|---:|---:|---:|---:|
| 4K, c1, 3 requests | 142.1 tok/s | 76.4 tok/s | 1.576 s | 2.350 s |
| 32K, c1, 3 requests | 129.5 tok/s | 76.3 tok/s | 4.098 s | 4.660 s |

K5 improved median decode by 86.0% at 4K and 69.7% at 32K. Median effective
prefill was 8,579 versus 8,438 tok/s at 4K and 8,993 versus 9,287 tok/s at
32K; the 32K result therefore attributes the end-to-end win to speculative
decode rather than a prefill claim.

Additional K5 capacity completed 8/8 at c8/4K with 272.33 aggregate output
tok/s. A long c2 run completed 2/2 at 490,861 prompt tokens per request with
no preemption or workspace failure. These are bounded concurrency results,
not a claim that eight simultaneous full-window requests fit.

## Promotion and rollback boundary

The operator explicitly authorized validation and promotion. Qualification
selects the K5 arm and rejects the no-spec arm for deployment performance.
The live transaction still must:

1. install the exact router profile without changing auxiliary, secondary,
   voice, OCR, general-vision, or video aliases;
2. retain the exact r33 393K fixed-port profile as transactional rollback;
3. update Mini Hermes, Pi, and OpenClaw to the 1,048,576-token Primary contract
   while preserving their existing compaction policies and unrelated settings;
4. restart only the affected gateways and pass real client/tool acceptance;
5. record post-transaction router identity and rollback readiness.

Until those steps pass, this publication is `promotion-ready`, not a claim
that r18 is the live Primary.

## Public evidence

The [sanitized summary](2026-08-21-deepseek-v4-flash-0731-infernal-r18-1m-promotion-evidence/summary.json)
contains the immutable identities and decision-relevant aggregates. Raw request
bodies, prompts, private topology, GPU identities, route URLs, and logs remain
private operator evidence.

## Caveats

- The result is exact to this checkpoint, digest, runtime trees, WSL2
  translation, two-card topology, DSpark K5 policy, and scheduler shape.
- Reported post-load free VRAM is under 0.5 GiB per card. The profile is an
  exclusive AI workload and does not establish a co-resident graphics reserve.
- A startup-only PyTorch compile diagnostic logs deprecated enum constant
  registration at error level. The post-soak request window is clean; this is
  retained as a runtime caveat rather than misreported as a request failure.
- The 160-call tool soak targets the prior xgrammar/FSM regression but cannot
  prove absence of every future structured-output edge case.
- Full-window concurrency is one. Short c8 and long c2 evidence do not raise
  that router admission ceiling.
