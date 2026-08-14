# Qwen3.8 27B official BF16 and FP8 qualification

**Date:** 2026-08-14

**Evidence:** official artifact provenance, local `functional`, `capacity`,
bounded `quality`, long-context retrieval, multimodal, and matched setting A/Bs

**Decision:** `challenger`, `no-promotion`; both official direct baselines left
healthy, with no router or client configuration change

## Outcome

The exact official BF16 and official FP8 Qwen3.8 27B checkpoints both loaded on
one RTX PRO 6000 Blackwell Max-Q each in symmetric split mode. Both passed the
complete thinking-disabled API gate, three-repetition coding/tool/session
checks, adaptive `low`, `medium`, and `xhigh` reasoning-control probes, 4K
capacity, and retrieval through an actual 241,250-token prompt. The BF16 lane
also passed all 30 deterministic image, video, and mixed-media attempts.

Official FP8 was the stronger text baseline: 47.9 versus 26.9 tok/s median c1
decode at 4K, and 104.71 versus 125.14 seconds TTFT at the 241,250-token row.
At c5 it completed 30/30 requests at 51 aggregate output tok/s. This result does
not erase the FP8 runtime caveat: vLLM warned that missing attention q/prob
scaling factors defaulted to 1.0, so an independent matched quality A/B is still
required before claiming cache-precision equivalence.

## Immutable identity and artifact gate

- BF16: `Qwen/Qwen3.8-27B` revision
  `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`.
- Official FP8: `Qwen/Qwen3.8-27B-FP8` revision
  `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`.
- Runtime: `vllm/vllm-openai` image index digest
  `sha256:4a2f33a884222f7049b983263ad9976f89452bb81affecf5b67d89ad35c1bc31`,
  engine revision `3a0914114705fa38d4c3171d0746c1a6b6f10209`, CUDA 13.0.1.
- Hardware: two equal 96 GB RTX PRO 6000 Blackwell Max-Q cards over PCIe,
  one TP=1 serve per card. Their memory is independent, not a unified 192 GB
  pool.

All 18 BF16 and 66 FP8 safetensors files matched the SHA-256 identities in the
pinned Hugging Face LFS inventories. The repositories contained no executable,
Python, pickle, PyTorch `.bin`/`.pt`/`.pth`, or native-library model payloads;
neither config declared `auto_map`, and no recipe used `trust_remote_code`.
Third-party quantizations were not downloaded or tested.

## Baseline gates

| Gate | BF16 multimodal | Official FP8 text |
|---|---:|---:|
| Thinking-disabled functional | All pass | All pass before and after experiments |
| Repeated quality | Intelligence, session, tool all 3/3; 31,225-token retrieval pass | Same |
| Adaptive reasoning control | `low`, `medium`, `xhigh` visible answer plus separate reasoning evidence | Same |
| 4K c1 decode / E2E p50 | 26.9 tok/s / 2.46 s | 47.9 tok/s / 1.69 s |
| 4K max configured admission | c2: 27 aggregate output tok/s | c5: 51 aggregate output tok/s |
| 31,225 prompt tokens | 8.05 s TTFT | 5.88 s TTFT |
| 139,428 prompt tokens | 53.44 s TTFT | 42.04 s TTFT |
| 241,250 prompt tokens | 125.14 s TTFT | 104.71 s TTFT |
| Image/video/mixed corpus | 30/30 | Text-only recipe; not run |

The multimodal corpus result comprises 12/12 image, 14/14 video, and 4/4 mixed
attempts. It covers OCR, charts, spatial counting, multi-image comparison,
temporal order, state change, event localization, long-video continuity, and
mixed video-plus-image inputs. The corpus manifest SHA-256 is
`ebff9dcc87a7fd13f801fc19eeea7271aec01a99fe560d721be99c1c9becad49`.

## Setting A/Bs on official FP8

### MTP=3

Adding only `method=mtp,num_speculative_tokens=3` passed the full functional and
repeated quality gates. Median c1 decode rose from 47.9 to 94.8 tok/s, while c5
aggregate throughput moved only from 51 to 54 tok/s. The repeated 31K quality
probe measured 100.4 tok/s decode with all checks passing. vLLM warned that the
inherited 4,096 batched-token cap may be suboptimal for MTP=3, and observed
draft acceptance varied by workload. A later batch-token tune must remain a
separate one-variable A/B.

The first MTP start failed before model load because the private registry
serialized the JSON value without retained quotes. That failure is preserved;
changing only the outer quoting allowed the same MTP value to load.

### Prefix caching

With only prefix caching enabled, a five-request burst sharing 30,000 prefix
tokens dropped from 9.18 seconds cold TTFT to 0.41 seconds warm and rose from
23 to 142 aggregate output tok/s. The exact cache-disabled control remained at
16.59 and 16.39 seconds TTFT across its two passes, both at 9 aggregate output
tok/s. The endpoint omitted `cached_tokens`, so this is timing-based reuse
evidence rather than counter-backed cache accounting.

### Unquantized KV

Changing only KV dtype from FP8 to `auto` produced a bfloat16-model-dtype KV
lane. It passed all functional gates and a 244,573-token prompt. Short-context
performance was effectively unchanged at 47.8 tok/s c1 and 51 aggregate
output tok/s c5. The engine reported 929,913 KV tokens and 3.55 full 262K
windows, versus 1,825,809 tokens and 6.96 windows for FP8 KV. This is a viable
accuracy-oriented control, not a performance win.

## Decision and remaining gates

The official FP8 text recipe plus MTP=3 is the strongest measured interactive
configuration, and prefix caching is compelling for repeated large-prefix
sessions. Neither is promoted: the MTP batch-token warning needs a clean tune,
the FP8 attention-scaling caveat needs an independent quality comparison, and
the durable separate-worker context/agentic/SWE campaign still requires an
approved candidate router alias. Direct tool and coding checks are not
SWE-bench evidence.

The sanitized machine-readable summary and private raw-artifact SHA-256
references are in
[qualification-summary.json](2026-08-14-qwen38-27b-official-qualification-evidence/qualification-summary.json).
Raw operational artifacts remain private because they contain live topology,
direct endpoint, and operator-path details. No route or promotion changed.
