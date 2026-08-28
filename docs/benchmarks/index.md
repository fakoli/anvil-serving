# Benchmarks

Measured local-model results on real Blackwell hardware: exact model revision,
engine, quantization, context, concurrency, and retained artifacts for every
number. These are local decision records, not a universal leaderboard — a
passing run never changes a serve or route without a separate human gate.

**Last evidence review: 2026-08-28.**

## Start with the numbers

The **[model comparison table](comparison.md)** puts every measured
configuration — TTFT, throughput, context, reasoning mode, and recipe link —
in one place, grouped by card. If you have one of these GPUs and want to know
what runs on it and how well, start there.

For the compact publication contract, use the
**[finding format](finding-format.md)**. It defines screenshot-ready result
cards, X and Reddit variants, accessible alt text, and a claim ledger back to
the full finding and raw artifacts.

## Browse by hardware

| Hardware | Current role | Start here |
|---|---|---|
| 2× NVIDIA RTX PRO 6000 Blackwell Max-Q, 192 GB aggregate, sm_120 | Current Fakoli Dark topology; split workloads or exclusive TP=2 | [RTX PRO 6000](hardware/rtx-pro-6000.md) |
| NVIDIA GeForce RTX 5090, 32 GB, sm_120 | Current isolated qualification lane plus historical Fakoli Dark results | [RTX 5090](hardware/rtx-5090.md) |

Fakoli Dark now has two equal PRO 6000 cards. Aggregate VRAM is not unified
memory, and the cards communicate over PCIe without NVLink. Exclusive TP=2
runs must prove that both devices were selected and all other inference was
offline. Fakoli Mini remains model-free in the reference topology. Historical
mixed-card tests still say which card was measured and which was merely
protected or co-resident.

## Browse by model

Each **[model dossier](models/index.md)** is a stable, model-centered summary:
current status, every measured configuration, working recipes, and links to the
dated findings behind each conclusion.

## Run context, agentic, and repository evaluations

The [context, agentic, and SWE job guide](context-agentic-swe.md) describes the
registered-worker workflow, bounded profiles through 640K, independent scoring,
pinned mini-SWE-agent and official SWE-bench grading, cancellation, and the
measured-versus-prior evidence boundary. The guide defines methodology and
commands; it does not claim a live model result by itself.

## Production aliases and recent controls

1. **RadixArk Qwen3.8 Flash Next NVFP4** — `current` text/image/OCR/video
   Primary at 262,144 tokens in exclusive TP=2 across both RTX PRO 6000 cards,
   with router concurrency one, four-image/one-video admission, a
   253,952-plus-8,192 client envelope, and the qualified hash-gated SM120
   QSA-fast MTP3 profile.
2. **DeepSeek V4 Flash 0731 Infernal Invocation r18/r15** — former text
   Primary profiles with retained TP=2 long-context, performance, quality, and
   real-client evidence.
3. **Qwen3.8 27B official FP8 SGLang single service and FP8/BF16 vLLM split** —
   former text/image/OCR/video deployments with retained managed recipes.
4. **Qwen3.5 122B A10B NVFP4**, **Agents-A1 plus Omni**, **DeepSeek r16 650K**,
   **Laguna S 2.1**, and **GPT-OSS Puzzle 88B** — retained qualified or
   promotion-era recipes, not the immediate selected deployment.
5. **Gemma 4** and **ThinkingCap Qwen3.6 27B** — historical strict-quality
   controls.
6. **Nemotron 3.5 ASR** and **Qwen3-ASR 0.6B** — historical RTX 5090
   measurements. The RTX PRO 6000 was protected, not benchmarked.
7. **RadixArk Qwen3.8 27B NVFP4** — historical RTX 5090 multimodal challenger;
   direct 128K text/tools/image/OCR/video evidence, no route or promotion.
8. **FLUX.2 Klein 4B FP8 and Wan2.2 TI2V 5B** — RTX 5090 ComfyUI
   generation candidates with direct functional/capacity evidence; both remain
   unavailable pending independent perceptual review and client acceptance.

On 2026-08-28, exact digest- and revision-pinned ComfyUI v0.33.4 workflows
qualified a 512×512 FLUX.2 Klein PNG and a 17-frame, 512×288 Wan2.2 H.264 MP4
on one RTX 5090. Peak GPU memory was 12,919 MiB and 18,263 MiB respectively
from a 943 MiB worker baseline. The worker was removed afterward; no workflow,
route, or deployment was promoted. See the
[media qualification](../findings/2026-08-28-comfyui-media-qualification.md).

On 2026-08-26, after the explicit human gate, the exact RadixArk Qwen3.8 Flash
Next NVFP4 revision became the text Primary at TP=2/262K/c1 and was then fixed
forward to the hash-gated PR #36556 SM120 QSA fast path with matched MTP3
`3/1/4`. It subsequently expanded in place to image/OCR/video after direct
media 30/30, live routed repeats 57/60 strict, edge cases 8/8, a six-size
context curve, and fresh OpenClaw/Hermes/Pi vision acceptance. It measured
155.9 tok/s at 4K, 114.7 at the new 128K target sweep, and 112.9 at the 254K
target; the separate full-reserve gate retained 102.0 tok/s. See the
[Qwen3.8 Flash Next dossier](models/qwen38-flash-next.md) and
[vision promotion record](../findings/2026-08-26-qwen38-flash-next-vision-promotion.md).

On 2026-08-16, after the explicit human gate, the exact digest-pinned r15
TP=2/393K profile became the text Primary at that date. A matched control measured K5 at
150.0 versus 76.4 tok/s median decode at 4K/c1 and 119.245 versus 76.767 at
32K/c1. Direct retrieval passed at 351,118 actual tokens and authenticated
routed retrieval at 340,119; tools, streaming, Responses, c8, c2 long-context,
and repeated agentic checks passed. The fixed-port r33 393K profile is the
transactional rollback. See the [DeepSeek dossier](models/deepseek-v4-flash.md)
and [r15 promotion record](../findings/2026-08-16-deepseek-v4-flash-0731-infernal-r15-393k-promotion.md).

The upstream r15 recipe was authored by Martin Vit (`voipmonitor`) and was
qualified upstream at 131,072 tokens on native Linux with two RTX PRO 6000
Blackwell GPUs on direct PCIe root ports. The local 393,216-token WSL2 result
is a separate qualification, not a transferred upstream claim.

On 2026-08-15, after a separate human gate, the exact official-FP8 SGLang
TP=1/393K Qwen profile became current. Its guarded acceptance passed 108K
retrieval, tools 20/20, direct and routed media 18/18, the supported Responses
subset, and fresh Hermes/OpenClaw Primary turns without fallback. It is now a
former deployment with retained evidence. See the
[Qwen3.8 dossier](models/qwen38-27b.md) and
[single-service promotion record](../findings/2026-08-15-qwen38-27b-sglang-fp8-single-promotion.md).

On 2026-08-16, the then-current Qwen model passed 30/30 direct deterministic
media attempts, including video 14/14. A managed router-only expansion added
`vision.video` and fail-closed admission for one video; the live admitted
subset passed 28/28 along with streaming, tool-use, malformed-input, overflow,
and Primary regression probes. See the
[video-router finding](../findings/2026-08-16-qwen38-27b-video-router.md).

On 2026-08-17, the separate single-RTX-5090 RadixArk NVFP4 profile advanced
from its 64K baseline to a 131,072-token served window. It returned a retrieval
marker at 119,675 actual prompt tokens, passed tools 20/20, direct
image/OCR/video, the complete media corpus 30/30, and boundary cases 4/4 at
eight images and two videos. It is the preferred 5090 computer-use perception
challenger, but remains direct-only and `no-promotion`; GUI action-loop, routed
admission, concurrency, and FP8-KV scale follow-up remain open. See the
[128K qualification](../findings/2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k.md).

The 2026-08-14 matched vLLM split remains retained historical evidence. Its routed FP8
tools 20/20, BF16 media 30/30, and one 32-image request remain historical
capability evidence, not a current admission declaration.

On 2026-08-15, official-FP8 MTP=4 and MTP=5 both passed functional,
near-393K, and repeated deterministic-quality gates. A cross-card swap showed
that the apparent first-run speed gap followed the GPU lane: MTP=5 exceeded
MTP=4 decode by only 0.4-1.3% on the same card and did not improve E2E. MTP=3
remained selected within the Qwen profile. See the
[MTP-depth qualification](../findings/2026-08-15-qwen38-27b-mtp-depth-qualification.md).

Also on 2026-08-15, a digest-pinned SGLang no-speculation A/B qualified
official FP8 and audited Inferact NVFP4 as TP=1/393K text controls on both card
placements. NVFP4 reduced matched 4K TTFT 22.6% and raised decode 20.6%, while
both candidates passed 388,979 actual prompt tokens and bounded deterministic
quality. See the
[SGLang/NVFP4 qualification](../findings/2026-08-15-qwen38-27b-sglang-nvfp4-qualification.md).

A matched MTP=3 follow-up then raised official-FP8 SGLang decode from 48.0 to
111.3 tok/s and Inferact NVFP4 from 57.9 to 98.1. Both retained the complete
functional and repeated deterministic-quality gate and passed a 389K
retrieval probe. The prior multimodal crash was isolated to SGLang's automatic
CUDA-IPC feature transport in this exact runtime; forcing CPU transport let
both checkpoints pass bounded image understanding and OCR with MTP enabled.
The later matched consolidation corpus added two-image ordering and supported
the human promotion of official FP8; Inferact NVFP4 remains `no-promotion`.
The then-promoted official-FP8 service subsequently qualified one video. The
32-image ceiling, concurrency above one, and host-memory pressure remain open.
See the
[MTP/multimodal qualification](../findings/2026-08-15-qwen38-27b-sglang-mtp-multimodal-qualification.md).

## How to read the evidence

Evidence labels describe what was observed:

| Label | Meaning |
|---|---|
| `external-prior` | Official or community research used to choose a recipe; not local proof. |
| `compatibility-only` | Loaded or answered bounded probes; no qualification claim. |
| `functional` | Independent behavior gates passed for the stated contract. |
| `capacity` | Context, concurrency, throughput, latency, or residency was measured. |
| `quality` | A declared quality workload was measured with retained results. |
| `historical-invalid` | Retained run is incomplete, incomparable, or missing identity needed for reuse. |

Decision labels are separate: `current`, `rollback`, `challenger`,
`no-promotion`, and `rejected`. A quality result can still be `no-promotion`;
a failed load can be `rejected` while remaining useful compatibility evidence.

Full comparison rules, instrument definitions, and artifact requirements:
[Methodology and evidence rules](methodology.md).

## Complete history

- [Run catalog](runs.md) — every retained, decision-relevant run, indexed by
  hardware and date.
- [Findings index](../findings/README.md) — the dated reports with full
  commands, raw artifacts, and failure cases.
- [Finding publication format](finding-format.md) — compact result and copy
  formats without weakening evidence boundaries.
- [External benchmarks](../EXTERNAL-BENCHMARKS.md) — advisory priors imported
  from official and community sources, kept separate from local results.
- [Chronological campaign archive](../BENCHMARKS.md) — the stable-URL summary
  of historical rounds, including Fast-tier and voice campaigns.
