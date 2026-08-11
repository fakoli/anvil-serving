# DeepSeek V4 Flash 0731 r33 batch-token A/B

**Date:** 2026-08-10

**Evidence:** `external-prior`, local `functional`, `capacity`, and bounded
`performance`

**Decision:** keep the 4,096-token candidate live and direct-only; use it as
the basis for the next GPU-only 393K qualification arm; `no-promotion`

## Outcome

Halving `MAX_NUM_BATCHED_TOKENS` from 8,192 to 4,096 on the otherwise matched
r33 target-only control reduced profiled peak activation memory from 1.73 to
1.14 GiB per TP rank and increased the minimum-rank GPU KV allocation from
15.27 to 15.99 GiB. A fresh bracketed replay reproduced the 8,192 baseline,
so this is not a stale-log comparison.

The engine-reported KV capacity increased from 283,917 to 553,243 tokens and
its stated full-131K concurrency increased from 2.17x to 4.22x. That large
token-count change is not yet a 393K capacity result: the KV byte allocation
rose only 4.715%, and this A/B retained a 131,072-token configured context.
Cache geometry or engine accounting remains unresolved until a separately
configured long-context serve starts and processes an actual prompt above
300,000 tokens.

## Documentation prior

The creator's
[pinned r33 compose](https://github.com/local-inference-lab/blackwell-llm-docker/blob/426da51285d0666508003b03a75a442139fb7979/examples/docker-compose-ds4-v20-r33.yml)
uses a 131,072-token context, 8,192 batched tokens, 16 sequences, and 0.975 GPU
utilization by default. The
[pinned r33 guide](https://github.com/local-inference-lab/rtx6kpro/blob/6c111c20c2bf2efec038e4daf14fc67030717e46/models/ds4dspark-v20-r33.md)
grounds the runtime provenance.

Official
[vLLM tuning documentation](https://docs.vllm.ai/projects/ascend/en/v0.24.0rc/tutorials/models/Qwen3.5-27B-Qwen3.6-27B.html)
defines `max-num-batched-tokens` as the number processed per step. It states
that larger values can reduce latency while increasing activation-memory
pressure, and that profiling subtracts peak HBM usage from the configured HBM
budget to size KV. That page documents an Ascend backend, so it is a mechanism
prior rather than proof for CUDA or RTX PRO. The local A/B supplies the
hardware-specific evidence.

## Controlled configuration

Both arms used the exact DeepSeek revision `9e165c30`, image digest
`fdde59fed7f9`, r33 B12X W4A8 mixed NVFP4-MoE/FP8 weights and activations, FP8
DS-MLA KV, target-only/no-spec decoding, TP=2, DCP=1, `max_num_seqs=1`,
131,072 configured context, utilization 0.975, InstantTensor `BUFFERED`, and
zero host or filesystem KV offload. Only the batch-token ceiling changed
functionally. Port, served name, and container name changed solely to preserve
evidence identity.

The portable candidate is the
[4,096-token recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r33-b12x-nospec-maxseq1-batch4096-131k-recipe.toml).

## Results

| Measurement | 8,192 control | 4,096 candidate | Change |
|---|---:|---:|---:|
| Minimum-rank KV allocation | 15.27 GiB | 15.99 GiB | +0.72 GiB / +4.715% |
| Engine-reported GPU KV tokens | 283,917 | 553,243 | +269,326 / +94.861% |
| Reported 131K concurrency | 2.17x | 4.22x | +2.05x |
| Peak activation per rank | 1.73 GiB | 1.14 GiB | -0.59 GiB / -34.104% |
| Weight allocation per rank | 75.64 GiB | 75.51 GiB | -0.13 GiB |
| Functional preflight | 6/6 | 6/6 | unchanged pass |
| Largest actual prompt | 119,503 | 119,503 | unchanged pass |
| Largest-request TTFT | 17.445 s | 17.364 s | -0.461% |
| Largest-request effective prefill | 7,537 tok/s | 7,344 tok/s | -2.569% |
| Largest-request decode | 73.86 tok/s | 75.20 tok/s | +1.820% |

The 4,096 arm passed smoke, JSON, 20/20 typed tools, streaming tools,
tool-result continuation, and the Responses API at high reasoning. It also
passed the same 117.5K, 118.5K, and 119.5K context-target ladder. The largest
request contained 119,503 API-reported prompt tokens. One request per target
supports a capacity comparison, not a performance ranking; small timing
deltas should be treated as bounded observations.

## What this proves and what it does not

This proves that the lower batch-token ceiling reduces the r33 profile's
activation-memory demand and assigns more GPU memory to KV on the exact dual
RTX PRO 6000 topology, without breaking the bounded functional or 119.5K
capacity gates. It also establishes that the historical 8,192 result is
reproducible under the current runtime.

It does not prove a request above 300K, 393K startup, concurrency beyond one
active request, broad task-quality equivalence, or a causal explanation for
the near-doubling of reported KV tokens. The mismatch between +4.715% KV bytes
and +94.861% reported tokens is retained explicitly rather than normalized
away.

## Next qualification arm and live state

The next clean experiment is GPU-only: 393,216 configured tokens,
`max_num_seqs=1`, batch 4,096, FP8 DS-MLA KV, target-only, and zero host
offload. It must first start with a reported KV pool at least 393,216 tokens,
then process an actual prompt above 300,000 tokens and pass post-probe
functional checks. If that fails, the already prepared native-offload path
remains the fallback rather than the first variable.

The 4,096/131K candidate was returned to healthy exclusive TP=2 ownership and
left running for follow-up. A post-reload smoke, JSON, 3/3 typed-tool, and
tool-result-continuation gate passed, and shared-memory inspection found zero
reclaimable files. It remains direct-only. No router alias, promoted
assignment, or rollback contract changed.

The sanitized machine-readable comparison is
[comparison.json](2026-08-10-deepseek-v4-flash-0731-r33-batch-token-ab-evidence/comparison.json).
Private raw artifacts are represented there by SHA-256 only because the full
operational evidence contains private topology details.
