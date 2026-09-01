# Agents-A1 FP8 versus Qwen3.5 122B at 262K

> **Publication redaction:** Operator-specific GPU UUIDs in linked raw evidence
> were replaced with stable labels. Hardware class, measurements, and event
> ordering are unchanged.

**Observed:** 2026-07-29
**Host:** Fakoli Dark, one NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation
Edition (96 GB, sm_120)
**Decision:** Agents-A1 official FP8 wins this bounded head-to-head, but the
result does not by itself authorize replacing the current Qwen Primary.

## What was matched

Both candidates served a 262,144-token window on the same GPU at concurrency
one with thinking disabled. They received the same 8K and 240K capacity request
shapes and the unchanged 30-attempt
[`multimodal-corpus/v1`](https://github.com/fakoli/anvil-serving/blob/main/benchmarks/corpora/agents-a1-v1/corpus.json)
manifest, SHA-256
`ebff9dcc87a7fd13f801fc19eeea7271aec01a99fe560d721be99c1c9becad49`.
At least 16K of configured headroom remained above the planned 240K lane.

This is a production-shaped profile comparison, not a weights-only experiment.
Agents-A1 used compressed-tensors FP8 weights and FP8 KV in vLLM
`f25953cc`; Qwen used ModelOpt NVFP4 weights and BF16 KV in NVIDIA vLLM
26.06. Exact revisions, image IDs, hardware identity, and source dates are in
the [campaign identity](2026-07-29-agents-a1-qwen-262k-head-to-head-evidence/campaign-identity.json)
and [source registry](2026-07-29-agents-a1-qwen-262k-head-to-head-evidence/source-registry.json).

## Result

Both profiles passed smoke, deterministic JSON, the approximately 240K needle,
and 20/20 tool calls. Agents-A1 then established the stronger capacity and
multimodal result:

| Measurement | Agents-A1 FP8 | Qwen3.5 122B NVFP4 |
|---|---:|---:|
| Model memory reported by vLLM | 35.31 GiB | 73.22 GiB |
| Available KV cache | 51.93 GiB / 5,277,426 tokens | 13.84 GiB / 571,950 tokens |
| Reported maximum 262K concurrency | 20.13x | 2.18x |
| 8K TTFT p50 / p95 | 0.252 / 0.531 s | **0.146** / 0.935 s |
| 8K effective prefill p50 | 29,168 tok/s | **50,215 tok/s** |
| 8K decode p50 | **188.1 tok/s** | 78.7 tok/s |
| 8K E2E p50 | **0.586 s** | 0.748 s |
| 240K TTFT p50 / p95 | **32.97 / 33.44 s** | 68.91 / 70.06 s |
| 240K effective prefill p50 | **6,920 tok/s** | 3,304 tok/s |
| 240K decode p50 | **155.8 tok/s** | 60.3 tok/s |
| 240K E2E p50 | **33.38 s** | 69.74 s |
| Image attempts | 12/12 | 12/12 |
| Video attempts | **12/14** | 0/14 |
| Mixed video/image attempts | **4/4** | 0/4 |
| Overall multimodal | **28/30** | 12/30 |

The 240K capacity requests contained 231,426 prompt tokens after exact
tokenization for both profiles. Client-observed effective prefill includes
queueing, scheduling, transfer, and first-token work; it is not a kernel-only
rate. Aggregate output throughput is also retained in the raw files, but the
per-request decode and latency metrics are more useful at concurrency one.

Agents-A1 used 51.78% less reported model memory, retained 3.75 times as much
KV memory, and reported 9.23 times as many full-window KV slots. At 240K its
TTFT was 52.15% lower, effective prefill 2.09 times higher, and decode 2.59
times higher. Qwen's only clear speed win was short-context first-token/prefill
work; Agents-A1 still completed the 8K request faster because its decode was
2.39 times higher.

## Video boundary

Qwen's official architecture and NVIDIA quant card describe video input, but
the exact current NGC 26.06 serving image could not decode the corpus H.264
files. OpenCV/FFmpeg reported `Could not find decoder for codec_id=27`, and
vLLM returned HTTP 400 `Could not open video stream`. Frames never reached
the model, so this run does **not** establish that Qwen lacks video
understanding. It does establish that the exact deployed runtime cannot
currently satisfy the direct-video contract.

The complete corpus was still run unchanged: every image passed, while every
video and video-containing mixed request retained the same actionable decoder
failure. Router qualification stopped at the direct-runtime boundary and no
live route was changed.

## Verdict and promotion boundary

For the measured 262K, thinking-disabled, single-GPU profile, **Agents-A1
official FP8 is the better serving candidate**. It matches Qwen's tested text
functional contract, uses much less weight memory, preserves substantially
more KV capacity, is faster end-to-end at short and long context, and is the
only tested profile whose runtime delivered video.

Qwen remains the current Primary after this campaign. Its earlier qualification
included a complete repeated protocol-v3 quality suite; that full suite was not
rerun head-to-head against Agents-A1 at 262K. Before considering promotion,
run the same repeated chat, context, tools, session recall, unified-diff, and
timeout-triage suite at the new Agents-A1 context, then apply the separate
human gate.

## Evidence and restoration

- [Machine-readable comparison](2026-07-29-agents-a1-qwen-262k-head-to-head-evidence/comparison.json)
- [Agents-A1 240K preflight](2026-07-29-agents-a1-qwen-262k-head-to-head-evidence/agents-a1-fp8-262k-preflight-240k.json)
- [Qwen 240K preflight](2026-07-29-agents-a1-qwen-262k-head-to-head-evidence/qwen35-122b-video-262k-preflight-240k.json)
- [Agents-A1 8K capacity](2026-07-29-agents-a1-qwen-262k-head-to-head-evidence/agents-a1-fp8-262k-capacity-8k-c1.json)
- [Qwen 8K capacity](2026-07-29-agents-a1-qwen-262k-head-to-head-evidence/qwen35-122b-video-262k-capacity-8k-c1.json)
- [Agents-A1 240K capacity](2026-07-29-agents-a1-qwen-262k-head-to-head-evidence/agents-a1-fp8-262k-capacity-240k-c1.json)
- [Qwen 240K capacity](2026-07-29-agents-a1-qwen-262k-head-to-head-evidence/qwen35-122b-video-262k-capacity-240k-c1.json)
- [Agents-A1 multimodal](2026-07-29-agents-a1-qwen-262k-head-to-head-evidence/agents-a1-fp8-262k-multimodal-c1.json)
- [Qwen multimodal](2026-07-29-agents-a1-qwen-262k-head-to-head-evidence/qwen35-122b-video-262k-multimodal-c1.json)
- [Before state](2026-07-29-agents-a1-qwen-262k-head-to-head-evidence/serve-state-before.json)
  and [restoration proof](2026-07-29-agents-a1-qwen-262k-head-to-head-evidence/serve-state-after.json)

Both campaign containers were removed after exact identity checks. Every
pre-campaign managed serve remains absent; the production router retained its
exact image, healthy state, port, and restart policy; and the RTX PRO 6000
returned to its 510 MiB idle allocation. No promotion occurred.
