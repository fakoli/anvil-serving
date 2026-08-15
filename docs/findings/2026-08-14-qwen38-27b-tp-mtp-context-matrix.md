# Qwen3.8 27B TP, MTP, and long-context matrix

**Date:** 2026-08-14

**Evidence:** local `functional`, 10-request c1 `capacity`, cold long-context
retrieval, engine cache accounting, and exact restoration

**Decision:** official FP8 plus MTP=3 is the strongest measured interactive
text lane; TP=2 is a long-prefill/capacity option rather than a universal speed
upgrade; 600K and 1M remain deliberate batch-like profiles; `no-promotion`

## Outcome

Both pinned official Qwen3.8 27B checkpoints passed every requested topology,
context, and MTP arm on two 96 GB RTX PRO 6000 Blackwell Max-Q cards. Split
TP=1 ran BF16 and official FP8 concurrently at a 393,216-token setting. The
exclusive TP=2 sequence tested each checkpoint at 393,216, 600,000, and
1,010,000 configured tokens, always with matched no-MTP and MTP=3 arms.

Every arm passed coding, JSON, 128K retrieval, 20/20 tool calls, streaming
tools, tool-result continuation, and the Responses API. Every extreme-context
row also retrieved correctly: 388,979 actual prompt tokens at the 393K setting,
598,729 at 600K, and 985,107 at 1.01M. These boundary rows are one cold request
each, not latency distributions; the 4K results are 10-request p50/p95 runs.

The practical result is not “TP=2 is twice as fast.” At 388,979 tokens, TP=2
cut BF16 control TTFT from 272.9 to 168.7 seconds and FP8 from 239.3 to 154.8
seconds, a 38% and 35% reduction. At ordinary 4K input, BF16 decode improved
from 25.8 to 35.9 tok/s, while official FP8 changed only from 47.6 to 48.8
tok/s. TP=2 is most valuable when the request is prefill-bound or needs the
larger sharded KV pool; split TP=1 remains more efficient when two independent
single-card services are useful.

## Immutable identities and common protocol

- BF16 multimodal: `Qwen/Qwen3.8-27B` revision
  `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`.
- Official FP8 text: `Qwen/Qwen3.8-27B-FP8` revision
  `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`.
- Runtime: `vllm/vllm-openai` image digest
  `sha256:4a2f33a884222f7049b983263ad9976f89452bb81affecf5b67d89ad35c1bc31`,
  vLLM revision `3a0914114705fa38d4c3171d0746c1a6b6f10209`.
- Common settings: FP8 KV, chunked prefill, prefix cache disabled,
  `max_num_seqs=1`, 4,096 batched tokens, and independently verified thinking
  disabled. MTP arms added only `method=mtp,num_speculative_tokens=3`.
- TP=2 used both PCIe cards without NVLink. GPU P2P could not be enabled, so
  vLLM disabled custom allreduce and selected PyNCCL over the socket-backed
  local path. Aggregate VRAM is sharded capacity, not unified memory.

The earlier artifact-safety gate remains binding: all official safetensors
matched the pinned Hugging Face LFS identities, and no executable model payload,
`auto_map`, `trust_remote_code`, or third-party quantization entered this run.

## Normal-request performance

All rows are 10/10 at c1 with 4,096 configured input tokens and a 256-token
output cap. TTFT and E2E are median client-observed values.

| Weights | TP | Setting | MTP | TTFT p50 / p95 | Prefill p50 | Decode p50 | E2E p50 |
|---|---:|---:|---:|---:|---:|---:|---:|
| BF16 | 1 | 393K | off | 0.848 / 1.021 s | 4,259 tok/s | 25.8 tok/s | 2.515 s |
| BF16 | 1 | 393K | 3 | 0.884 / 1.061 s | 4,084 tok/s | 62.0 tok/s | 1.584 s |
| Official FP8 | 1 | 393K | off | 0.802 / 0.988 s | 4,502 tok/s | 47.6 tok/s | 1.706 s |
| Official FP8 | 1 | 393K | 3 | 0.834 / 0.997 s | 4,326 tok/s | **93.6 tok/s** | 1.295 s |
| BF16 | 2 | 393K | off | 0.731 / 0.986 s | 4,939 tok/s | 35.9 tok/s | 1.923 s |
| BF16 | 2 | 393K | 3 | 0.758 / 0.994 s | 4,760 tok/s | 75.6 tok/s | 1.325 s |
| Official FP8 | 2 | 393K | off | 0.711 / 0.726 s | 5,081 tok/s | 48.8 tok/s | 1.587 s |
| Official FP8 | 2 | 393K | 3 | 0.744 / 0.776 s | 4,837 tok/s | 85.9 tok/s | 1.240 s |
| BF16 | 2 | 600K | off | 0.740 / 0.955 s | 4,867 tok/s | 35.4 tok/s | 1.948 s |
| BF16 | 2 | 600K | 3 | 0.764 / 0.769 s | 4,728 tok/s | 70.4 tok/s | 1.373 s |
| Official FP8 | 2 | 600K | off | 0.709 / 0.725 s | 5,093 tok/s | 49.1 tok/s | 1.584 s |
| Official FP8 | 2 | 600K | 3 | 0.743 / 0.754 s | 4,852 tok/s | **91.6 tok/s** | **1.197 s** |
| BF16 | 2 | 1.01M | off | 0.732 / 0.741 s | 4,933 tok/s | 36.1 tok/s | 1.921 s |
| BF16 | 2 | 1.01M | 3 | 0.764 / 0.770 s | 4,725 tok/s | 67.4 tok/s | 1.385 s |
| Official FP8 | 2 | 1.01M | off | 0.716 / 0.731 s | 5,032 tok/s | 49.2 tok/s | 1.590 s |
| Official FP8 | 2 | 1.01M | 3 | 0.756 / 0.767 s | 4,773 tok/s | 91.5 tok/s | 1.225 s |

Raising the configured TP=2 window did not materially harm the 4K control
workload. MTP raised short-request decode by 1.76-2.40x across the matrix, with
roughly 24-40 ms additional median TTFT. Official FP8 was the best text choice:
its TP=1 MTP lane reached 93.6 tok/s while leaving the second card available;
the fastest measured point was TP=2/600K/MTP at 91.6 tok/s decode and 1.197 s
E2E, statistically too close to call faster from one 10-request run.

## Cold long-context performance and cache fit

Each row below is a single cold correctness pass. `KV tokens` is vLLM's startup
accounting, not demonstrated concurrent full-window throughput.

| Weights | TP | Setting | MTP | Actual prompt | TTFT | Effective prefill | KV tokens | Result |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| BF16 | 1 | 393K | off | 388,979 | 272.9 s | 1,425 tok/s | 1,044,963 | pass |
| BF16 | 1 | 393K | 3 | 388,979 | 280.0 s | 1,389 tok/s | 934,269 | pass |
| Official FP8 | 1 | 393K | off | 388,979 | 239.3 s | 1,625 tok/s | 1,842,232 | pass |
| Official FP8 | 1 | 393K | 3 | 388,979 | 244.8 s | 1,589 tok/s | 1,662,785 | pass |
| BF16 | 2 | 393K | off | 388,979 | 168.7 s | 2,305 tok/s | 3,771,158 | pass |
| BF16 | 2 | 393K | 3 | 388,979 | 172.2 s | 2,259 tok/s | 3,417,016 | pass |
| Official FP8 | 2 | 393K | off | 388,979 | **154.8 s** | **2,512 tok/s** | 4,653,572 | pass |
| Official FP8 | 2 | 393K | 3 | 388,979 | 158.8 s | 2,449 tok/s | 4,224,785 | pass |
| BF16 | 2 | 600K | off | 598,729 | 344.1 s | 1,740 tok/s | 3,780,310 | pass |
| BF16 | 2 | 600K | 3 | 598,729 | 347.0 s | 1,726 tok/s | 3,469,767 | pass |
| Official FP8 | 2 | 600K | off | 598,729 | **321.2 s** | **1,864 tok/s** | 4,666,321 | pass |
| Official FP8 | 2 | 600K | 3 | 598,729 | 324.2 s | 1,847 tok/s | 4,289,922 | pass |
| BF16 | 2 | 1.01M | off | 985,107 | 820.6 s | 1,201 tok/s | 3,778,148 | pass |
| BF16 | 2 | 1.01M | 3 | 985,107 | 817.8 s | 1,205 tok/s | 3,497,360 | pass |
| Official FP8 | 2 | 1.01M | off | 985,107 | 784.1 s | 1,256 tok/s | 4,666,574 | pass |
| Official FP8 | 2 | 1.01M | 3 | 985,107 | **780.1 s** | **1,263 tok/s** | 4,328,571 | pass |

MTP did not improve extreme-context prefill in a repeatable way. It added 2.8
to 7.1 seconds at 393K/600K and produced small, non-repeated four-second wins
at 1M. Meanwhile it consumed 7-11% of the reported KV-token pool. Use MTP when
the answer is long enough for decode speed to matter; do not justify it as a
long-context TTFT optimization.

Official FP8 reduced control TTFT versus BF16 by 8.2% at 393K, 6.7% at 600K,
and 4.4% at 985K. The advantage narrows because attention/prefill dominates as
context grows. The 600K rows take about 5.4-5.8 minutes to first token and the
985K rows about 13.0-13.7 minutes. Those settings fit and retrieve correctly,
but they are offline or batch-like capacity, not interactive defaults.

## Failures, caveats, and restoration

- The first 393K harness target requested 390,000 tokens but tokenized beyond
  the configured lane and returned HTTP 400 before inference. The retained
  calibrated target produced 388,979 actual prompt tokens and passed.
- `--eval-repetitions 3` did not repeat context rows; it emitted one request per
  target. The report therefore says 1/1 for every extreme row and does not
  present those values as p50/p95.
- The installed global CLI was stale and initially ignored recipe
  `model_path`. No benchmark was taken from those provisional containers. They
  were unloaded, and all measured arms were relaunched through the isolated
  worktree module with immutable snapshot paths.
- Official FP8 retained the warning that missing attention q/prob scale values
  defaulted to 1.0. vLLM also auto-disabled DeepGemm for this architecture on
  Blackwell and fell back to CUTLASS to avoid the reported accuracy problem.
- No router alias, client configuration, or promotion changed. After the final
  arm, the exact original single-card BF16 and FP8 262K direct services were
  restored on their original cards. Both passed the complete functional suite
  while co-resident, and shared memory contained zero files and zero
  reclaimable bytes.

The sanitized machine-readable result set is
[summary.json](2026-08-14-qwen38-27b-tp-mtp-context-matrix-evidence/summary.json).
Portable recipes are linked from the maintained model dossier. Raw operational
artifacts remain private because they contain live endpoint, card identity, and
operator-path data.
