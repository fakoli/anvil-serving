# Benchmark results

> **Looking for the maintained decision view?** Start with the
> [hardware-first benchmark portal](benchmarks/index.md), choose
> [RTX PRO 6000](benchmarks/hardware/rtx-pro-6000.md) or
> [RTX 5090](benchmarks/hardware/rtx-5090.md), then use the
> [model dossiers](benchmarks/models/index.md) and
> [run catalog](benchmarks/runs.md). This stable URL remains the chronological
> campaign archive, including Fast-tier, voice, and historical rounds.

The maintained text deployment is RadixArk Qwen3.8 Flash Next NVFP4 at
exclusive TP=2/262,144 across both equal cards, router concurrency one, and a
client contract of 253,952 prompt tokens plus an 8,192-token output reserve.
The former DeepSeek Infernal Invocation r18 1M and r15 393K deployments and
official-FP8 Qwen3.8 27B SGLang single service and FP8/BF16 vLLM split remain
retained text/image/OCR/video recipes. Qwen3.5 122B NVFP4 and the Agents-A1
split remain qualified rollback- or promotion-era evidence. The earlier r16
650K profile, Laguna S 2.1, and GPT-OSS Puzzle remain qualified historical
recipes but are not the immediate restoration contract for this profile.
Gemma 4 and ThinkingCap remain historical controls. Fakoli
Dark has two equal RTX PRO 6000 cards. Nemotron 3.5 ASR and Qwen3-ASR were historically measured on the
now-removed RTX 5090 while the PRO 6000 was protected. Older sections below
preserve what was concluded at their dates.

This page is the public, searchable summary of the model and end-to-end benchmarks that currently inform anvil-serving's reference deployment. It is deliberately a summary, not a generic model leaderboard: every number depends on the recorded model revision, engine, quantization, context limit, hardware, workload, and topology.

The dated [findings](findings/README.md) contain the full commands, raw artifacts, failure cases, and decision history. Results below were last updated **2026-08-26**.

## Qwen3.8 Flash Next NVFP4 262K promotion (2026-08-26)

The exact RadixArk ModelOpt NVFP4 revision `7b719225` was qualified through a
digest-pinned SGLang image on both RTX PRO 6000 cards at exclusive TP=2,
262,144 context, concurrency one, no speculation, and the bounded SM120/WSL2
portable-QSA compatibility lane. Direct and authenticated routed retrieval
passed at 253,325 prompt tokens while retaining an 8,192-token output reserve.
Routed coding, JSON, tools 20/20, streaming tools, tool-result continuation,
and Responses passed; the repeated thinking-disabled suite passed intelligence
6/6, session 3/3, and tools 3/3.

The engine reported 416,064 KV tokens. At 4K/c1 it measured 214.612 ms median
TTFT, 2.627 s median E2E, and 12.801 tok/s median decode. A c2 diagnostic
completed 4/4 but queued behind the one-running-request scheduler, so the
promoted admission contract remains c1. Real OpenClaw, Hermes, and Pi turns
selected the Primary and completed without fallback after credential drift and
Pi provider/env-reference defects were repaired. The human-authorized
fix-forward promotion is complete; DeepSeek is now retained historical
evidence. See the [promotion finding](findings/2026-08-26-qwen38-flash-next-promotion.md).

## Qwen3.8 27B GGUF 250K qualification on RTX 5090 (2026-08-21)

A managed llama.cpp b10548 campaign compared Unsloth Q4_0 without speculation
against the same weights plus the matching Q4_0 MTP head at concurrency one
and 262,144 served tokens. Both passed exact retrieval through 253,822 actual
prompt tokens while preserving an 8,192-token output reserve. The MTP arm
raised short decode from 69.1 to 104.1 tok/s and reduced short E2E from 0.91 to
0.74 seconds, though TTFT and prefill regressed. It also passed tools 20/20, a
tool call after 110,875 actual prompt tokens, agentic 16/18, three neutral
101-request endurance sessions, and an 18/18 image/OCR/UI corpus.

Conventional Q6_K plus the same MTP head was not loaded: its optimistic
capacity bound was 199,930 tokens and its margin at the required 258,192-token
envelope remained negative. Q4_0+MTP3 is therefore the preferred RTX 5090
`FAST-TIER` challenger. Promotion is deferred because the isolated SWE worker
could not complete, the image health probe targets the wrong port, and runtime
position warnings need resolution. An August 22 follow-up passed real OpenClaw
and Hermes routed identity, no-fallback, and shell-tool/result-continuation
smokes. The 250K routed gate remains closed because the bounded route declared
stale 131,072-token SGLang/NVFP4 metadata and video capability instead of the
262K llama.cpp image-only recipe. See the
[GGUF qualification](findings/2026-08-21-qwen38-27b-gguf-250k-rtx5090.md).

## DeepSeek Infernal Invocation r18 1M qualification (2026-08-21)

The exact digest-pinned r18 B12X W4A8/FP8-compressed-MLA-KV profile was
qualified on both RTX PRO 6000 cards at TP=2/DCP=1, 1,048,576 tokens,
maxseq8, batch4,096, and fixed probabilistic DSpark K5. Calibrated retrieval
passed through 1,040,063 actual prompt tokens. The complete direct functional
and post-reload gates passed, as did repeated intelligence/session/tools 12/12,
an additional structured-tool soak 160/160, client-shaped output-reserve
probes, c8 short capacity, and c2 at 490,861 prompt tokens per request.

Against an otherwise identical no-spec control, K5 raised median decode from
76.4 to 142.1 tok/s at 4K and 76.3 to 129.5 at 32K. The engine reported
1,323,176 KV tokens, or 1.26 full configured windows; router admission is
therefore one full-window request. The operator-authorized guarded transaction
is complete. Authenticated routing passed the functional API/tool subset with
exact identity, and Mini generation 2 aligned Hermes, Pi, and OpenClaw to the
1M Primary contract while retaining their compaction policies. Real Hermes
terminal-tool, normal Pi, and running OpenClaw-gateway turns passed. See the
[r18 qualification and promotion](findings/2026-08-21-deepseek-v4-flash-0731-infernal-r18-1m-promotion.md).

## Qwen3.8 27B RTX 5090 recipe frontier (2026-08-21)

An extensive current-source review covered the official SGLang selector, X,
Reddit, Hugging Face checkpoints, vLLM issues and proposed fixes, EXL3, NInfer,
GGUF reports, and independent hardware sites. The green SGLang cookbook label
is a bounded 8,192-input/1,024-output/concurrency-one verification, not proof
that the configured 128K or 262K window fits.

The exact DFlash2 screenshot recipe exposed 24,347 KV tokens; its best tuned
BF16 arm reached 70,262. A second matched candidate kept the same RadixArk
target and added native MTP3 plus ReplaySSM. Decode improved 80.5% at 4K and
67.9% at 64K, with tools 20/20, but a separately loaded 5.73 GB draft left only
70,231 KV tokens. Median 64K end-to-end latency was 1.9% slower because prefill
dominated. Both speculative families are rejected as replacements.

EXL3 has the strongest published external fidelity/context evidence, while
NInfer has the largest speed upside. Neither currently clears the local desk
screen: EXL3's own profiles trade prefill against fidelity and report no
all-gates winner; NInfer lacks a comparable tool and long-context reasoning
gate on a stable upstream runtime. The exact no-speculation 128K baseline was
restored and passed a 105,649-token retrieval plus the complete tool/API subset.
No route or promotion changed. See the
[recipe research finding](findings/2026-08-21-qwen38-27b-rtx5090-recipe-research.md).

## Qwen3.8 27B RadixArk NVFP4 on RTX 5090 (2026-08-17)

A separate single-RTX-5090 qualification loaded the exact RadixArk ModelOpt
NVFP4 checkpoint through digest-pinned SGLang at TP=1, 131,072 tokens,
concurrency one, FP8 E4M3 KV, CPU multimodal feature transport, and no MTP.
It passed coding, JSON, retrieval at 119,675 actual prompt tokens, tools 20/20,
direct image/OCR/video, the complete deterministic multimodal corpus 30/30
(image 12/12, mixed 4/4, video 14/14), and count-boundary cases 4/4 at eight
images and two videos per request. The model used 20.14 GB for weights and the
host reported 3,928 MiB free after startup.

This is now the preferred locally proven RTX 5090 computer-use perception and
native-video challenger. It is not a deployment change: routing, GUI action
loops, concurrency above one, controlled decode rate, and the FP8-KV scale
warning remain open, and promotion requires a separate human gate. LFM2.5-VL
1.6B remains a promising lightweight frame-caption/OCR companion rather than
the primary temporal video reasoner. See the
[128K qualification](findings/2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k.md).

## DeepSeek Infernal Invocation r15 393K promotion (2026-08-16)

The r15 recipe was inspired by and translated from Martin Vit's
(`voipmonitor`) pinned `local-inference-lab/rtx6kpro` and
`local-inference-lab/blackwell-llm-docker` work. His upstream receipt qualified
131,072 tokens on native Linux with two RTX PRO 6000 Blackwell GPUs on direct
PCIe root ports. The 393,216-token WSL2 result is an independent local
qualification, not a transfer of that upstream stability claim.

The exact digest-pinned B12X W4A8/FP8-compressed-MLA-KV profile passed a
matched K5/no-spec A/B. K5 measured 150.0 versus 76.4 tok/s median decode at
4K/c1 and 119.245 versus 76.767 at 32K/c1. Direct retrieval passed at 351,118
actual prompt tokens and authenticated routed retrieval at 340,119. The full
functional gate, repeated tools/session/unified-diff/timeout checks 12/12, c8
short concurrency, c2 long concurrency, streaming, tool-result continuation,
Responses, and OpenClaw-compatible Anthropic wire requests passed.

After explicit human approval, a guarded transaction installed the exact
r15 identity as the exclusive TP=2 text `llm.primary`, restarted the router,
and verified post-restart readiness and admission. The endpoint-adapted r33
393K profile is the transactional rollback. A fresh actual Mini OpenClaw turn
remains unproven because the reachable Mini controller lacks the current
OpenClaw status tool. See the
[promotion finding](findings/2026-08-16-deepseek-v4-flash-0731-infernal-r15-393k-promotion.md).

## Qwen3.8 27B video-router expansion (2026-08-16)

The existing model passed the complete deterministic corpus directly at
30/30, including 14/14 video attempts. After adding fail-closed router media
admission and `vision.video`, the admitted routed subset passed 28/28 live.
Two-video overflow returned 413, malformed input returned a sanitized 400,
and video SSE, grounded tool use, and the full Primary regression gate passed.
The qualified Qwen ceiling was one request, two images, and one video. The model was
not restarted. See the
[video qualification](findings/2026-08-16-qwen38-27b-video-router.md).

## Read these results correctly

- Treat a row as evidence for its exact tested configuration, not for every variant of that model family.
- Compare rows only when their workload and topology are comparable. A faster inference run does not establish coding quality, tool reliability, or routing eligibility.
- Quality-profile and production changes remain human-gated. A benchmark can recommend a change; it never promotes a model by itself.
- External benchmark data is an advisory prior, not a local result. See [External benchmarks](EXTERNAL-BENCHMARKS.md) for its import and comparison workflow.

## Qwen3.8 27B SGLang FP8 single-service promotion (2026-08-15)

The later router-only AI-MBP25 coding-agent campaign passed the agentic smoke
2/2, the broader agentic scout 16/18, and a fixed five-instance SWE-bench
Verified scout 5/5 under the official grader. Both agentic failures were the
debug-loop repetitions; SWE tasks used 19-57 model requests. This is bounded
evidence for the exact then-current Qwen profile, not a full-suite 100% SWE claim. See
the [agentic and SWE scout](findings/2026-08-15-qwen38-27b-agentic-swe-scout.md).

The human-approved Qwen profile then ran one official-FP8 SGLang TP=1 service
on one RTX PRO 6000, with the second equal card empty. Primary, general vision,
and OCR shared the same 393,216-token service. It used FP8 E4M3
KV, one running request, 2,048-token chunks, memory fraction 0.85, five GDN
states, EAGLE MTP `3/1/4`, and CPU multimodal feature transport. Admission was
initially bounded to two images and no video; the subsequent router-only
qualification expanded it to one video while retaining the two-image and
concurrency-one ceilings.

The exact earlier profile measured 0.577-second median TTFT, 0.962-second
median E2E, 6,261 effective prefill tok/s, and 111.4 decode tok/s at 4K/c1.
Promotion acceptance passed 108K retrieval, tools 20/20, direct and routed
media 18/18, the Responses subset, and fresh Hermes/OpenClaw Primary turns
without fallback. See the [promotion finding](findings/2026-08-15-qwen38-27b-sglang-fp8-single-promotion.md).

The former vLLM FP8/BF16 split remains a retained managed recipe. A retained
no-video router profile is the narrower historical capability profile.

## Qwen3.8 27B official-FP8 MTP-depth qualification (2026-08-15)

MTP=4 and MTP=5 were tested concurrently at the then-current TP=1/393K/maxseq1
shape, then swapped across the two equal cards. Both passed complete direct
functional checks, repeated deterministic intelligence/session/tool checks,
and a cold request with 388,979 actual prompt tokens.

The swap changed the performance conclusion. The first placement appeared to
favor MTP=4 by about 6.9% in decode, but the faster result followed the card.
On a fixed card, MTP=5 exceeded MTP=4 decode by only 0.4-1.3% and made median
E2E slightly worse. The historical matched MTP=3 result on the production
lane remained better than either deeper setting. MTP=3 therefore remained the
selected Qwen depth; MTP=4 and MTP=5 are retained `no-promotion` controls. The exact FP8
plus BF16 split was restored, directly requalified, and readmitted. See the
[MTP-depth qualification](findings/2026-08-15-qwen38-27b-mtp-depth-qualification.md).

## Qwen3.8 27B SGLang MTP and multimodal qualification (2026-08-15)

Official FP8 and audited Inferact NVFP4 were tested with the SGLang cookbook's
in-checkpoint EAGLE MTP `3/1/4` configuration at TP=1, 393,216 tokens, and
concurrency one, then swapped across the two equal RTX PRO 6000 cards. Across
five matched 4K runs, official FP8 rose from 48.0 to 111.3 decode tok/s and
NVFP4 rose from 57.9 to 98.1. NVFP4 retained lower TTFT and higher effective
prefill; official FP8 won speculative decode because its sampled acceptance
was higher.

Both models passed coding, JSON, 20/20 tools, streaming and tool-result
recovery, Responses, 131K and 389K retrieval, plus repeated deterministic
intelligence 6/6, session 3/3, and tools 3/3. The earlier multimodal crash was
isolated to SGLang's automatic CUDA-IPC feature transport in this exact
runtime. CPU feature transport let both MTP profiles pass bounded image
understanding and OCR. Video, multiple images, the 32-image ceiling, and host
memory pressure remain untested. The exact then-current vLLM split was restored and
readmitted; no route or promotion changed. See the
[SGLang MTP/multimodal qualification](findings/2026-08-15-qwen38-27b-sglang-mtp-multimodal-qualification.md).

## Qwen3.8 27B TP/MTP/long-context matrix (2026-08-14)

The pinned official BF16 and official FP8 checkpoints completed 16 matched
configurations: split TP=1 at 393K, then exclusive TP=2 at 393K, 600K, and
1.01M, each with no-MTP and MTP=3. Every arm passed the complete functional
gate and one cold retrieval at 388,979, 598,729, or 985,107 actual prompt
tokens. The 4K performance figures are 10-request c1 p50/p95 runs; each extreme
row is only 1/1 and is not a latency distribution.

TP=2 cut 393K control TTFT from 272.9 to 168.7 seconds for BF16 and from 239.3
to 154.8 seconds for official FP8, but official-FP8 4K decode changed only 47.6
to 48.8 tok/s. MTP raised short decode 1.76-2.40x, peaking at 93.6 tok/s on the
single-card FP8 lane, while consuming 7-11% of reported KV tokens and providing
no repeatable extreme-context TTFT benefit. TP=2 therefore earns a
prefill/capacity role, not a universal speed claim. The 600K and 985K rows took
about 5.2-5.8 and 13.0-13.7 minutes to first token, so they remain deliberate
batch-like profiles. The exact original 262K split services were restored and
passed fresh co-resident acceptance at the close of that matrix. The later
human-approved promotion is recorded above. See the
[full matrix and sanitized result set](findings/2026-08-14-qwen38-27b-tp-mtp-context-matrix.md).

## Qwen3.8 27B official FP8 1M-context continuation (2026-08-14)

The official FP8 checkpoint was configured for 1,010,000 tokens on one RTX PRO
6000 with TP=1, FP8 KV, maxseq1, chunked prefill, and no MTP or prefix caching.
It passed retrieval at 316,849, 422,449, and 633,649 actual prompt tokens, then
passed **825,049 actual prompt tokens at 3/3**. Those largest runs averaged
**956.739 seconds** request-to-completion, so the result is stable offline/batch
capacity rather than an interactive default. A post-stress functional gate
passed, and the original 262K FP8 lane was restored and requalified.

The official 1M flags were already present in the first 27B vLLM recipe; the
later same-day edit changed the declared vLLM floor and difficulty. The new
2.4T-A95B sibling recipe does not fit this two-card host. No third-party NVFP4
checkpoint was pulled, no route changed, and the model remains a
`challenger`, `no-promotion`. See the
[dated continuation and raw artifacts](findings/2026-08-14-qwen38-27b-1m-context.md).

## Dual-PRO exclusive TP=2 campaign (2026-08-01)

Fakoli Dark's two RTX PRO 6000 Blackwell Max-Q cards were assigned together to
one model at a time over PCIe without NVLink. All other inference was offline.
The table uses c1 and requested 32K prompts; actual prompt depth is shown in the
[dated finding](findings/2026-08-01-dual-pro-tp2-model-campaign.md).

| Candidate / exact lane | Completion | First-output latency p50 | Effective prefill p50 | Decode p50 | Repeated quality | Outcome |
|---|---:|---:|---:|---:|---|---|
| Qwen3.5 122B A10B NVFP4, thinking off | 12/12 | 2.32 s TTFT | 12,821 tok/s | 67.5 tok/s | intelligence 6/6, session 3/3, tools 3/3 | TP=2 `no-promotion`; single-card rollback unchanged |
| Nemotron 3 Super 120B NVFP4, TP=2 + EP=2, thinking off | 12/12 | 2.84 s TTFT | 10,025 tok/s | 59.5 tok/s | intelligence 6/6, session 3/3, tools 3/3 | `no-promotion` |
| Laguna S 2.1 NVFP4, thinking off | 12/12 | **1.97 s TTFT** | **15,134 tok/s** | **70.9 tok/s** | intelligence 6/6, session 3/3, tools 3/3 | TP=2 `no-promotion`; single-card rollback unchanged |
| DeepSeek V4 Flash 0731, `reasoning_effort=low` | 11/12 | 2.70 s TTFO; 29.11 s first-visible TTFT | 7,818 tok/s from TTFO | 11.5 tok/s combined reasoning/visible | intelligence 6/6, session 3/3, tools 3/3 | `challenger`, `no-promotion`; one reasoning-only exhaustion |
| Inkling Small NVFP4, `reasoning_effort=low` | 12/12 | 2.79 s TTFO; 4.63 s first-visible TTFT | 7,844 tok/s from TTFO | 73.5 tok/s combined reasoning/visible | intelligence 6/6, session 3/3, tools 3/3 | `no-promotion`; reasoning-off Responses caveat retained |

The three thinking-disabled comparison rows use `capacity-v3`. DeepSeek and
Inkling use the new `capacity-v4-reasoning` contract, so their prefill boundary
is first reasoning or visible output and their decode count includes reasoning
tokens. Inkling also completed a separate reasoning-off 12/12 capacity lane at
2.84-second TTFT and 74.6 tok/s visible decode. This campaign
also found no genuine NVFP8-labeled DeepSeek or Inkling artifact; it tested the
official fitting quantization instead. Exact revisions, images, longer-context
lanes, raw JSON, and runtime fixes are in the
[complete campaign record](findings/2026-08-01-dual-pro-tp2-model-campaign.md).

### DeepSeek 0731 r16 B12X and DSpark follow-up

The official release revision was requalified on a pinned r16 vLLM/B12X image
at 131,072 tokens with DSpark K5. The translated WSL2 recipe passed low, high,
and max reasoning preflights; completed 32K, 64K, and 128K correctness probes;
and passed 27/27 repeated coding, intelligence, session, and tool attempts. A
warmed 125,785-prompt-token request measured 19.44 seconds TTFO, 23.81 seconds
first-visible TTFT, 6,469 effective prefill tok/s, and 128.9 tok/s combined
reasoning/visible decode.

In the same-image 4K/c1 A/B, DSpark raised median per-request decode from 64.9
to 130.7 tok/s, aggregate output from 59.6 to 101.7 tok/s, and reduced median
E2E from 3.88 to 1.60 seconds. Cumulative counters accepted 55.1% of drafted
tokens. DSpark used 1.6-2.3 GiB more VRAM than the no-spec control, and neither
lane preserved the required 3 GiB reported free on both GPUs. This is therefore
a priority `challenger` performance recipe, not a promotion. Exact identities,
context telemetry, matched controls, failures, and raw artifacts are in the
[r16 qualification](findings/2026-08-01-deepseek-v4-flash-0731-r16-dspark-qualification.md).

The 2026-08-02 native-offload follow-up derived one narrow WSL2 image that
keeps global V2 UVA enabled while skipping CUDA host registration only for the
process-shared offload mmap. At a 262,144-token ceiling it passed cold requests
through 249,573 prompt tokens; that row measured 43.75-second TTFO,
45.58-second first-visible TTFT, 5,705 effective prefill tok/s, and 135.2 tok/s
decode. An initial identical start failed because four orphan mmap files filled
`/dev/shm`, not because of model geometry. The product now checks live process
and container ownership twice before exact-path cleanup and applies that
postcondition to managed native-offload load/unload. A 16 GiB follow-up sized
the CPU tier above the measured 506,283-token GPU KV cache; after six distinct
150K planned-context requests, exact replay added 113,408 external hits and
loaded 1,001,721,600 bytes CPU-to-GPU in 0.344 seconds, with 0.825-second TTFO
and 1.974-second visible TTFT. The model remains
`no-promotion`; see the
[256K qualification](findings/2026-08-02-deepseek-v4-flash-0731-native-kv-offload-256k.md).

### DeepSeek 0731 650K Primary promotion

After a separate human gate, the GPU-only 650K/maxseq16 profile became
`llm.primary` for one Pi/OpenClaw coding user. Pi on Fakoli Dark, Pi on Fakoli
Mini, and OpenClaw on Fakoli Mini passed live high-reasoning smokes. The router
now supports an optional per-tier output cap; this tier declares 32,768 and a
live 50,000-token request was clamped, completed, and returned a warning.

The initially selected 1M/maxseq16 profile was removed after two real client
shapes fatally exceeded its 514.25 MiB locked B12X workspace. One failure used
a 19,118-token Pi prompt with only 5,120 requested output tokens, proving that
output clamping alone cannot make the 1M profile safe. The promoted 650K serve
still waives the standing 3 GiB reported-free VRAM policy and therefore remains
an explicit single-user, exclusive TP=2 deployment. See the
[promotion record](findings/2026-08-02-deepseek-v4-flash-0731-primary-promotion.md).

### DeepSeek 0731 r33 393K Primary promotion

After a separate human gate on 2026-08-11, the digest-pinned r33 B12X/DSpark
K5 profile became `llm.primary` at 393,216 tokens, maxseq16, and 4,096-token
batching. It retains FP8 DS-MLA KV and uses no host offload. The engine reported
725,543 GPU KV tokens. A calibrated direct ladder passed through 359,900 actual
prompt tokens; the largest row measured 65.2-second TTFT and 5,599 effective
prefill tok/s.

OpenClaw and Hermes now target `llm.primary` with 393,216 context, 32,768
maximum output tokens, and high reasoning. Their gateways restarted and
isolated client-path markers passed. These are not client requests above 300K. The
legacy routed nominal-320K needle generated a byte-based 450,028-token
admission estimate and correctly failed 413, so a calibrated routed context
job remains open. The requested SWE smoke also remains unscored because the
installed worker wheel could not load its benchmark profiles. See the
[promotion record](findings/2026-08-11-deepseek-v4-flash-0731-r33-393k-promotion.md).

## RTX 5090 Omni choices (as of 2026-07-27)

> For the current routed occupant, see the
> [benchmark portal](benchmarks/index.md) and
> [RTX 5090 page](benchmarks/hardware/rtx-5090.md). The section below records what was
> concluded on its date.

At that date the routed reference tier was the exclusive 30B Nemotron Omni serve. A
second, unpromoted `Qwen/Qwen2.5-Omni-3B` configuration was locally
validated as the `omni-voice-stack` choice with dedicated STT and
TTS serves co-resident on the RTX 5090.

The small-Omni capacity probe completed 6/6 requests at concurrency two with a
2,048-token prompt and 128-token output cap. TTFT p50/p95 was 0.04/0.06
seconds, end-to-end p50/p95 was 0.32/0.33 seconds, and aggregate output
throughput was 243 tok/s. Text/JSON/4K, image, OCR, and basic audio-input gates
passed. The separate voice round trip passed with 0.0 WER in 710.68 ms.
Measured GPU use for Qwen plus voice plus display was 28,743 of 32,607 MiB.

These are bounded capability and capacity results, not a general quality
comparison or router promotion. The
[small Omni plus voice finding](findings/2026-07-27-omni-voice-stack-qualification.md)
records exact identities, raw evidence, the noisy Omni audio response, and the
Gemma 3n license blocker.

## Fast-tier comparison (as of 2026-07-16)

> This is a dated campaign snapshot, not the current routing state. The
> [benchmark portal](benchmarks/index.md) is the maintained decision view.

At that date the reference Fast tier on Fakoli Dark's RTX 5090 was **`leon-se/gemma-4-E4B-it-FP8-Dynamic`**, served as `gemma4-e4b-it` with FP8 KV cache and a 32K context limit. The July 16 official-checkpoint/template rerun retained this control: it passed all repeated quality gates, while the new-template E2B, E4B, and 12B Fast candidates each failed the strict timeout-triage check with thinking disabled.

| Candidate / tested configuration | Measured voice total / LLM stage | Loaded-endpoint TTFT / end-to-end | Approx. decode rate | Outcome |
|---|---:|---:|---:|---|
| Gemma 4 E4B FP8-Dynamic control, legacy embedded template, 32K | — | 460 ms TTFT p50 at 30K, c1; 580 ms at c2 | 49 tok/s c1; 79 tok/s c2 aggregate | **Current Fast tier**; repeated chat/context/tool/session/intelligence gates passed. |
| Official Gemma 4 E2B/E4B/12B W4A16, July 15 template | — | 430 / 630 / 1430 ms TTFT p50 at 32K, c1 | 96 / 41 / 22 tok/s aggregate | Protocol and long-context gates passed; all three rejected for Fast by the strict thinking-disabled quality gate. |
| Qwen3.6-35B-A3B, vLLM NVFP4, 32K | 377.52 ms / 165.40 ms | 1489.36 ms / 2302.37 ms | 236.16 tok/s | Former Fast tier; all historical bakeoff hard gates passed. |
| Qwen3.6-27B control, vLLM NVFP4, 32K | 1130.21 ms / 814.83 ms | 6203.94 ms / 9041.91 ms | 67.65 tok/s | Former Fast-tier control. |
| Devstral Small 2, vLLM FP8, 8K | 923.98 ms / 433.12 ms | 742.46 ms / 3755.56 ms | 57.75 tok/s | Promising coding/agent fallback, but the successful run required an 8K context limit. |
| GLM-4.7-Flash, llama.cpp `UD-Q4_K_XL`, 32K | 2376.21 ms / 961.49 ms | 6196.05 ms / 7417.46 ms | 157.20 tok/s | Tool and session checks passed, but it was not competitive for the Fast voice role. |
| Gemma-4-31B, vLLM NVFP4, 32K then 8K | — | — | — | Rejected for this RTX 5090 recipe; no viable loaded endpoint. |

### Bakeoff notebook (repeatable comparison)

The hand-assembled fast-tier report above is now repeatable. Record each
candidate run and render the comparison:

```bash
# append a bakeoff run (alongside --evidence-out)
anvil-serving eval benchmark quality --candidate-id C --config-id CFG \
  --notebook .anvil/benchmarks.sqlite --notebook-task fast-tier --notebook-hardware rtx4090

# render the candidate matrix + rubric + win/lose/hold determination
anvil-serving eval benchmark external notebook render --task fast-tier --hardware rtx4090 --baseline current
```

The rubric weights and hard gates live in
`anvil_serving/external_benchmarks/notebook.py` (pure, self-checked). Runs
are append-only; the view is latest-per-(candidate, config, task, hardware).

Externally-authored eval suites (e.g. a session-evals `suite.json`) run through the
same deterministic check engine with `--suite-file`:

```bash
anvil-serving eval benchmark quality --candidate-id C --config-id CFG \
  --suite-file ~/.anvil-serving/eval-data/2026-07-11-planning-regression/suite.json \
  --evidence-out evidence.json
```

The spec shape is `{suite, date, work_class, evals: [{id, prompt|messages,
visible_answer_tokens?, reasoning_headroom_tokens?, max_tokens?, tools?,
expect_tool?, checks?}]}`. New comparisons use the two explicit allocations;
`max_tokens` is a legacy total cap and cannot be combined with them. `checks`
use deterministic case-insensitive substring or validated regular-expression semantics and
`expect_tool` the tool-call validator. Per-eval results land in the evidence JSON under
`suites.<suite name>`, with failed checks recorded in the top-level `failures` list.
`--suite-file` alone runs only the external suite; add `--suite chat,tool,...` to run
built-in suites in the same evidence artifact. Malformed specs — including vacuous
checks that could never fail (typo'd assertion keys, empty needles) — are rejected
before any request is sent.

Cross-model reasoning runs should also select the model family's actual control
(`--thinking-mode` or `--reasoning-effort`), set equal visible-answer allocations,
record explicit reasoning headroom, and use repeated attempts. Protocol-v3
artifacts retain the full visible answer, finish reason, reasoning-channel
metadata, per-attempt budgets, pass rates, and distinct classifications for
reasoning exhaustion, visible-answer exhaustion, and an ordinary wrong visible
answer. The API still enforces one combined completion cap; the allocations are
recorded intent rather than a claim of hard server-side partitioning.

These rows are from the [Fast-tier LLM bakeoff](findings/2026-07-08-fast-tier-llm-bakeoff.md) and its [human-gated promotion record](findings/2026-07-08-fast-tier-promotion.md). The voice artifacts in that bakeoff measure STT, LLM, and TTS stage timing, but their STT hypothesis is empty with WER `1.0`; they are **not** semantic speech-recognition accuracy results. The displayed decode rate is derived from the recorded evidence as `output_tokens * 1000 / (e2e_ms - ttft_ms)`.

## Agents-A1 FP8 versus Qwen3.5 122B at 262K (2026-07-29)

The same RTX PRO 6000, concurrency-one, thinking-disabled lane compared
Agents-A1 official FP8 and the current Qwen3.5 122B NVFP4 checkpoint at
262,144 configured tokens. Both passed smoke, JSON, approximately 240K
retrieval, and 20/20 tools. At 231,426 actual prompt tokens, Agents-A1 measured
32.97 seconds TTFT, 6,920 effective prefill tok/s, and 155.8 decode tok/s;
Qwen measured 68.91 seconds, 3,304 tok/s, and 60.3 tok/s.

Agents-A1 reported 35.31 GiB model memory and 51.93 GiB KV versus Qwen's
73.22 GiB and 13.84 GiB. On the unchanged corpus, both passed 12/12 images;
Agents-A1 passed 12/14 video and 4/4 mixed attempts, while Qwen's exact NGC
26.06 runtime failed every video-containing request before inference because
its OpenCV/FFmpeg build lacked an H.264 decoder.

Agents-A1 won this bounded serving comparison. It subsequently passed the
complete repeated protocol-v3 suite at the 262K profile and received the
separate human promotion gate; Qwen is now the immediate rollback. See the
[dated head-to-head](findings/2026-07-29-agents-a1-qwen-262k-head-to-head.md)
and the [promotion record](findings/2026-07-29-agents-a1-primary-promotion.md)
with linked raw evidence.

## Agents-A1 FP8 Primary promotion (2026-07-29)

The exact official FP8 profile passed a three-repetition, thinking-disabled
protocol-v3 suite covering 32K/128K/240K context, tools, session recall,
unified diff, and timeout triage at a required 100% rate. Context TTFT was
1.571, 12.839, and 35.209 seconds respectively; no reasoning leaked into the
disabled contract. The managed transaction then passed smoke, JSON, 240K
retrieval in 24.9 seconds, and 20/20 tools before installing the exact router
configuration and verifying the served model identity.

The Primary is now `InternScience/Agents-A1-FP8` revision
`4d7d59380f327b76e73bc71f40e0c589ad0ca1d5`, served as
`agents-a1-fp8-mm-262k` with FP8 KV, c1 admission, four-image/one-video limits,
and thinking hard-disabled by the router. Qwen3.5 122B is the immediate
managed rollback. The strict multimodal corpus caveat remains explicit at
28/30; both missed cases named the correct event interval but omitted one
required assertion word, and BF16 reproduced the same result.

## Qwen3.5 122B Primary qualification (2026-07-28)

The qualified Primary is **`nvidia/Qwen3.5-122B-A10B-NVFP4`**, served as
`qwen35-122b-a10b-nvfp4` on the RTX PRO 6000. Revision
`98915d837c4e7c87ac8296d02e89de19b3207e6d` runs in pinned NVIDIA vLLM 26.06
with ModelOpt FP4 weights, BF16 KV cache, one admitted sequence, and the
checkpoint's native **262,144-token** window. The final profile loads the vision
tower, accepts one image per prompt, disables video, and enables thinking by
default while preserving per-request disable.

Thinking-disabled smoke, JSON, 240K retrieval, and 10/10 tools passed. The
repeated protocol-v3 suite passed chat, 32K/128K/240K context, tools, session
recall, unified diff, and timeout triage at 100%. Thinking-enabled use separately
passed a 128K gate. Image understanding and verbatim OCR passed with thinking
disabled and enabled, and default thinking produced reasoning evidence. With
the vision tower resident, vLLM retained 571,950 KV-cache tokens (2.18 full
windows), and the 240K retrieval gate measured 52.9 seconds TTFT.

MTP is disabled, and the runtime marks the ModelOpt and Mamba prefix-cache paths
experimental. Laguna S 2.1 remains the immediate managed rollback. See the
[dated qualification](findings/2026-07-28-qwen35-122b-primary-qualification.md)
and linked raw JSON.

## Agents-A1 multimodal and quantization qualification (2026-07-28)

Agents-A1 BF16, official FP8, and ProtoLabs NVFP4 were compared on the single
RTX PRO 6000 with pinned vLLM `f25953cc`, FP8 KV, a 131,072-token operational
window, and thinking disabled. BF16 and FP8 passed text, image, OCR, direct
`video_url`, tools, streaming, Responses, session, and 128K c1/c2/c4 gates.
They also produced the same 28/30 strict multimodal corpus result: all 12 image
and four mixed-media attempts passed, while two video outputs found the exact
event interval but omitted one required assertion word. That matching failure
is not an FP8 quant regression, but it does block the predeclared 100%
multimodal hard gate.

| Profile | 8K throughput | Memory observation | Outcome |
|---|---|---|---|
| BF16 multimodal | c1/c8/c16: 90/151/162 tok/s | 65.53 GiB model, 19.53 GiB KV | Correctness control; 28/30 multimodal |
| Official FP8 multimodal | c1/c8/c16/c32: 104/193/200/218 tok/s | 35.31 GiB model, 49.66 GiB KV | Principal production-shaped candidate; 28/30, no promotion |
| Official FP8 text | c1/c8/c16/c32: 101/190/207/225 tok/s | 34.46 GiB model, 50.81 GiB KV | Matched text control |
| ProtoLabs NVFP4 text | c1/c8/c16: 105/187/204 tok/s | 21.03 GiB model | Pareto-preferred compact text profile; no image/video |

The FP8 vision tower costs approximately 1.5 GiB of practical runtime
headroom. Video adds request-dependent visual-token and decode pressure rather
than persistent weights, so the isolated router profile starts at one video
and four images with explicit admission estimates. NVFP4 is not a speed win
over FP8, and its publisher documents a vision-tower crash on this stack; its
qualification is text-only. All 240K requests failed closed at the served
131,072-token boundary.

The official FP8 runtime also lacked a GPU-specific E=256/N=512 MoE kernel
config. The complete 18-batch target-GPU tuner took 3h 30m 50s. Its exact tune
loaded and preserved functional gates, but the paired three-run 8K c16 A/B
regressed aggregate throughput by 1.399% (214.21 to 211.21 tok/s), so the tune
is rejected and remains inert. The isolated router passed same-dialect video,
media admission, tools, and SSE; malformed and unsupported cross-dialect video
now fail as sanitized 4xx responses in both streaming and non-streaming form.
Full source review, storage evidence, Creative Commons fixtures, publication
timings, raw results, router boundary, and decision table are in the
[dated multimodal qualification](findings/2026-07-28-agents-a1-multimodal-qualification.md).
No production route changed.

## Laguna S 2.1 Heavy qualification (2026-07-26)

The immediate Primary rollback is **`poolside/Laguna-S-2.1-NVFP4`**, served as
`laguna-s-2.1-nvfp4` on the RTX PRO 6000 with a 262,144-token window. The
deployment pins checkpoint revision
`07614121b31898586430f189d27a25a0be310843` and vLLM image
`nightly-f25953cc59f9b4ba9b04b16228d2b86dcfbcbdb1`. The router forces
`chat_template_kwargs.enable_thinking=false`.

Thinking-disabled smoke, JSON, 120K promotion retrieval, and tools 10/10 passed.
The repeated protocol-v3 gate also passed 32K, 128K, and 240K context retrieval,
tools 3/3, multi-turn recall 3/3, unified diff 3/3, and timeout triage 3/3.
The first thinking-enabled smoke exhausted the full 4,352-token completion
allowance without a visible answer; an immediate rerun passed. That intermittent
exhaustion is why disabled thinking is part of the production contract.

Short-output capacity completed 10/10 at concurrency one and 40/40 at concurrency
eight. TTFT p50 was 0.07 seconds at c1 and 3.44 seconds at c8; aggregate output
was 75.46 and 83.24 tok/s. These are batch-capacity figures, not controlled
long-decode rates. GPT-OSS Puzzle 88B is the sole declared managed rollback.
Commands, exact identity, failure evidence, and raw artifacts are in the
[Laguna S qualification record](findings/2026-07-26-laguna-s-heavy-qualification.md).

### Release-sweep recheck and Agents-A1 challenger (2026-07-27)

A smaller pre-release recheck retained the Laguna thinking-disabled contract:
smoke, JSON, 120K retrieval, and tools 20/20 passed, as did the selected
repeated quality suites. Four-request c1 capacity measured 0.079-second TTFT
p50 and 62.18 aggregate tok/s; eight-request c8 measured 2.22-second TTFT p50
and 86.68 aggregate tok/s. This corroborates, but does not replace, the larger
July 26 qualification.

`InternScience/Agents-A1` revision
`addff08f1653ee72765c5cf458fe84556bb34f8e` was also loaded on the RTX PRO
6000 as an unpromoted challenger. Its default-thinking smoke failed with no
visible answer and `finish_reason=length`; with thinking disabled, smoke, JSON,
120K retrieval, tools 20/20, intelligence 6/6 attempts, session 3/3, and tool
quality 3/3 passed. Capacity measured 0.30-second TTFT p50 and 85.19 aggregate
tok/s at c1, and 1.38-second TTFT p50 and 142 aggregate tok/s at c8. Retain it
for future comparison only, with thinking disabled as part of the tested
contract. The [release-readiness sweep](findings/2026-07-27-anvil-serving-release-readiness-sweep.md)
links the raw artifacts and lifecycle caveats.

## GPT-OSS Puzzle 88B Heavy compatibility (2026-07-18)

The former Heavy tier and current rollback is **`nvidia/gpt-oss-puzzle-88B`**, served as
`gpt-oss-puzzle-88b` from an exact local Anvil vLLM image on the RTX PRO 6000.
The deployment pins checkpoint revision
`9c0e0746a0d2218b28cc7b2cb3ce4e1a2f50fdb2`, serves a 131,072-token window
with FP8 KV cache, and uses the native Harmony template and OpenAI tool parser.
The router supplies `reasoning_effort=high` by default. The complete reusable procedure is
the [GPT-OSS Puzzle 88B recipe](benchmarks/gpt-oss-puzzle-88b-recipe.md).

This transition is not a cross-model quality or throughput ranking. The exact
production shape passed smoke and JSON, a 120K requested needle check, 20/20
shared-prefix tool calls, the original Harmony parser regression 10/10 without a
request-level stop-token workaround, Responses API, streaming SSE, and a complete
tool-result continuation. The observed needle prompt was 99,100 tokens; the prior
exact-image qualification separately retains a 130,696-prompt-token near-limit
retrieval.

Post-promotion live measurement on the final image completed 10/10 direct Heavy
requests at concurrency one and 40/40 at concurrency eight. At 8K fixed context,
direct TTFT p50/p95 was 0.393/0.956 seconds at c1 and 0.766/1.075 seconds at c8;
E2E p50/p95 was 0.473/1.035 and 0.906/1.148 seconds. The tiny mixed completions
make their 3.85 and 17.85 aggregate tok/s capacity figures unsuitable as
controlled decode rates. The authenticated `planning` router path separately
completed 10/10 at c1 with 0.484/0.718-second TTFT p50/p95.

The repeated protocol-v3 suite passed 32K and 128K context, tool calling 3/3,
session recall 3/3, and timeout triage 3/3. Unified-diff formatting passed 2/3,
so the strict 100% quality gate failed. This is a real remaining quality caveat,
while the tool result demonstrates the intended runtime improvement over the
pre-fix image's 0/3 HTTP-500 failure. Root cause, fork/upstream relationship,
immutable revisions, router validation, commands, and raw artifacts are in the
[GPT-OSS Puzzle Heavy enablement record](findings/2026-07-18-gpt-oss-puzzle-heavy-promotion.md).

## Gemma 4 July 15 template matrix (2026-07-16)

### Current 31B optimization follow-up (2026-07-17)

The current official `google/gemma-4-31B-it-qat-w4a16-ct` checkpoint with the newly pinned Google
template ran healthily at 128K on the RTX PRO 6000 Max-Q under vLLM 0.25.1. Its warmed c1 diagnostic
decode was **62.3 tok/s** (two 512-token responses) and the 128K probe recorded **74.97 s TTFT**.
The official Q4 MTP assistant is **not compatible** with this W4A16 target: native MTP initializes,
then fails its engine profile on incompatible 6400/10752 projection dimensions. Do not deploy that
pair. The 300 W Max-Q power limit and different QAT/NVFP4 checkpoints make the approximately
46--48 s external RTX PRO 6000 NVFP4 128K TTFT reports an advisory reference, not a direct
regression comparator. Full artifacts, WSL2 scope, and failure evidence are in the
[dated optimization finding](findings/2026-07-17-gemma4-31b-optimization.md). **No Heavy
promotion changed.**

Official Gemma 4 12B IT QAT W4A16 is a historical Heavy rollback, served as
`gemma4-12b-it-w4a16-ct` through vLLM 0.25.1 on the RTX PRO 6000 with FP8 KV,
a 256K context limit, five admitted sequences, and thinking enabled by router
default. It replaced ThinkingCap after the July 16 human-approved guarded
promotion and remained Heavy until the July 18 Puzzle compatibility transition.

| Heavy configuration | Repeated quality | 32K TTFT p50 / aggregate output | Quality context TTFT (32K / 128K / 240K) | Outcome |
|---|---|---:|---:|---|
| ThinkingCap Qwen3.6 27B FP8 control | pass | 4.84 s / 3 tok/s | 7.83 / 57.60 / 124.70 s | Valid rollback |
| Gemma 4 12B W4A16, July 15 template | **pass** | **1.52 s / 21 tok/s** | **6.96 / 44.61 / 97.33 s** | **Immediate Heavy rollback** |
| Gemma 4 26B BF16 | fail timeout triage 0/3 | 0.73 s / 36 tok/s | capacity TTFT 11.93 s at 120K, 34.07 s at 240K | Faster, strict-quality failure |
| Gemma 4 31B W4A16 | pass | 4.02 s / 7 tok/s | 15.44 / 112.30 / 248.57 s | Quality pass, materially slower |

The 12B promotion gate passed disabled-thinking smoke/JSON, a 240K needle,
20/20 tools, a separate enabled-thinking reasoning-evidence gate, router reload,
and exact post-reload identity. The first live attempt failed closed on a
256-visible-token `finish_reason=length` and automatically restored the validated
ThinkingCap rollback; the corrected 512-visible-token gate then passed without
removing any check. The Fast tier did not change. Full matrix, pinned revisions,
template hashes, failed starts, two-turn tool replay, cache cleanup, and raw
artifacts: [Gemma 4 chat-template bakeoff](findings/2026-07-16-gemma4-chat-template-bakeoff.md).

### Unsloth Gemma 4 NVFP4 follow-up (2026-07-16)

> **Concurrency-128 correction:** a later same-day vLLM 0.25.1 retest reproduced a large NVFP4
> continuous-batching gain. On the PRO 6000, 12B NVFP4 beat official QAT by 35.7% at c128/1K and
> 45.0% at c128/8K; on the 5090 with Model Runner V2 it beat QAT by 35.2% at c128/8K. The c1
> decode conclusion below remains true, but it does not describe high-concurrency serving. The
> production engine was upgraded to vLLM 0.25.1 with WSL2 pinned memory enabled; NVFP4 and V2
> remain unpromoted because the prior quality failures and V2 thinking-budget limitation remain.
> See [the c128 and WSL2 retest](findings/2026-07-16-gemma4-vllm0251-wsl2-c128.md).

The same-day Unsloth 12B, 26B-A4B, and 31B NVFP4 release was tested through
the existing vLLM 0.25.1 WSL2 recipe on both Blackwell GPUs. **No production
tier changed.** The publisher's approximately 1.5x 12B speed claim was not
reproduced locally: in matched three-attempt, 1,024-token diagnostics, NVFP4
was 7.4% slower than official QAT on the RTX 5090 and 9.3% slower on the RTX
PRO 6000.

| Candidate / tested configuration | Hardware and window | Repeated quality | Loaded capacity c1 / c2 | Equal-length diagnostic | Outcome |
|---|---|---|---:|---:|---|
| Unsloth Gemma 4 12B NVFP4 | RTX 5090, 32K | fail: timeout triage 1/3, thinking disabled | 55 / 144 tok/s at 8K fixed context | 103.82 tok/s | No Fast quality or decode-rate win |
| Unsloth Gemma 4 12B NVFP4 | RTX PRO 6000, 256K | fail: repeated tool 1/3 | 21 / 76 tok/s | 98.86 tok/s | Tool argument regression; keep official QAT Heavy |
| Unsloth Gemma 4 26B-A4B NVFP4 | RTX 5090, 32K | fail: timeout triage 1/3, thinking disabled | **121 / 233 tok/s at 8K fixed context** | **218.09 tok/s** | Fastest local Gemma variation; promotion blocked |
| Unsloth Gemma 4 26B-A4B NVFP4 | RTX PRO 6000, 256K | fail: timeout triage 1/3 | **45 / 122 tok/s** | **191.46 tok/s** | Full-window speed candidate; promotion blocked |
| Unsloth Gemma 4 31B NVFP4 | RTX PRO 6000, 256K | **pass** | 7 / 30 tok/s | 51.49 tok/s | Quality pass, materially too slow |

The 26B-A4B checkpoint is the best future speed candidate, while 31B is the
only larger checkpoint that cleared the full repeated Heavy gate. At 240K,
quality-context TTFT was 48.27 seconds for 26B-A4B and 223.32 seconds for 31B.
The Unsloth template is not byte-identical to Google's canonical July 15
template and tolerates pre-serialized string tool arguments; this is recorded
alongside the 12B tool failure. Full revisions, context matrix, functional
preflights, diagnostic caveats, runtime/kernel evidence, and raw artifacts:
[Gemma 4 Unsloth NVFP4 follow-up](findings/2026-07-16-gemma4-unsloth-nvfp4-follow-up.md).

## Blackwell candidate bakeoff (2026-07-10)

Six community-shortlisted candidates measured against the production baselines on Fakoli Dark
(RTX 5090 32 GB fast track; RTX PRO 6000 96 GB heavy track). Full narrative, failure records,
and raw evidence: [Blackwell local model bakeoff](findings/2026-07-10-blackwell-local-model-bakeoff.md).
**No production tier changed as a result of this bakeoff.**

| Candidate / tested configuration | Hardware | Context | Preflight | Tool calls | Decode rate | Long-context | Role verdict |
|---|---|---:|---|---|---:|---|---|
| MiniMax-M2.7-REAP-139B-A10B, vLLM NGC 26.04 NVFP4, 64K | PRO 6000 | 65,536 | pass (thinking disabled) | pass | 97.2 tok/s | 64K pass (TTFT 14.3 s); no 131K headroom | Best measured heavy candidate of the base round - superseded by Puzzle-75B (extension table below); not promoted (community REAP checkpoint) |
| Ornith-1.0-35B, vLLM NGC 26.04 FP8, 131K | PRO 6000 | 131,072 | pass (thinking disabled) | pass 20/20 | 29.2 tok/s | 131K pass — needle 11.9 s, fastest 131k full-prefill measured (13.1 s) | Retain as agentic/long-context specialist; not promoted |
| Nemotron-3-Nano-30B-A3B, vLLM NGC 26.04 NVFP4 + PIECEWISE graphs + nano_v3 parser, 131K | RTX 5090 | 131,072 | ALL PASS | pass | 15.0 tok/s | 131K pass (FULL graphs hang — upstream bug workaround required) | Keep experimental |
| Nemotron-3-Nano-Omni-30B, pinned vLLM **nightly v0.23** NVFP4, 64K | RTX 5090 | 65,536 | **PASS** text, image, and OCR | pass (release gate 3/3; historical 20/20) | 224.08 tok/s aggregate at c2/2K/128 output cap; historical 27.3 tok/s long decode | 64K pass (historical TTFT 3.1 s) | **Current exclusive Omni stack** for auxiliary text, image, and OCR; pinned-nightly caveat |
| Gemma-4-31B-IT NVFP4, vLLM gemma4-unified, six configs | RTX 5090 | none fit | fail (KV OOM ladder) | — | — | — | Reject under tested configuration (32 GB + WSL2 legacy runner); llama.cpp GGUF / PRO 6000 untested |
| DeepSeek-V4-Flash NVFP4, NGC + nightly attempts | PRO 6000 | not reached | — | — | — | — | Not enough evidence (engine-version reject; nightly load aborted) |

### Extension round (2026-07-11)

| Candidate / tested configuration | Hardware | Context | Preflight | Tool calls | Decode rate | MTP A/B | Role verdict |
|---|---|---:|---|---|---:|---|---|
| Nemotron-Labs-3-Puzzle-75B-A9B NVFP4, vLLM nightly, MTP n=3, 131K | PRO 6000 | 131,072 | ALL PASS | pass 20/20 | 137.0 tok/s (long-gen) | **1.50×** (91.4 → 137.0) | **Best measured candidate for the heavy role; not promoted** (official checkpoint; pin a stable engine first) |
| Qwen3.6-27B-Text-NVFP4-MTP (community), vLLM nightly, MTP n=3, 262K | PRO 6000 | **262,144 verified** | ALL PASS | pass 20/20 | 95.0 tok/s (long-gen) | 1.36× (69.9 → 95.0) | 262K big-KV experiment validated; community checkpoint; not promoted |
| Qwen3.5-35B-A3B Q4_K_M, llama.cpp, 64K | RTX 5090 | 65,536 | pass in window | pass 20/20 | ~147 tok/s decode, 178 ms TTFT | untested (draft-mtp) | Strongest fast-tier challenger (intelligence 2/2); not promoted |
| Gemma-4-E4B-it QAT UD-Q4_K_XL, llama.cpp, 64K | RTX 5090 | 65,536 | pass in window | pass 20/20 | 97.0 tok/s, 61 ms TTFT | — | Low-latency specialist; not promoted (upstream PLE gap open) |

Baselines measured in the same window: production heavy gpt-oss-120b (all gates pass, 131K,
intelligence 2/2) and production fast qwen36-35b-a3b (matches its 2026-07-08 promotion profile).

### Qwen3.6-27B Heavy variation bakeoff (2026-07-12)

Three Qwen3.6-27B checkpoints were tested on the single RTX PRO 6000 with
vLLM nightly, MTP n=3, FP8 KV, a 262K native context limit, and a five-sequence
admission cap. All three passed full preflight at 131K, the current built-in
Heavy eval, and 5/5 concurrent request completion. The independent ten-question
ARC-Challenge slice scored 9/10 for community NVFP4 and 10/10 for both FP8
variants.

ThinkingCap FP8 is the **selected resident Qwen3.6 Heavy candidate**: in a
thinking-enabled five-question tie-break it produced 4/5 correct visible finals
within a 1,024-token budget versus 1/5 for NVFP4, with 6.69 s versus 9.14 s
median latency. NVFP4 remains faster with thinking disabled (8K TTFT p50
0.63 s single / 3.22 s at concurrency five, versus ThinkingCap's 1.01 s /
4.66 s). See the
[dated finding and raw artifacts](findings/2026-07-12-qwen36-27b-heavy-variation-bakeoff.md).

This changes the recommendation within the Qwen3.6-27B comparison only. The
selected endpoint remains an unpromoted experiment serve; no production router
profile changed. The native 262K window was served and 131K was correctness-
validated. The model-card YaRN extension to 1.01M was not enabled or tested.

### Qwen3.5-122B MXFP4 follow-up (2026-07-12)

The cached `olka-fi/Qwen3.5-122B-A10B-MXFP4` checkpoint was re-served on the
single RTX PRO 6000 at 131K through vLLM's sm_120 Marlin W4A16 fallback. Full
preflight passed, but the standard 8K benchmark measured only **30.57 tok/s**
(TTFT p50 720.79 ms), below the prior local NVFP4 result of 38.8 tok/s. The new
externally-authored deterministic planning suite passed **1/5** cases. See the
[dated finding and raw artifacts](findings/2026-07-12-qwen35-122b-mxfp4-benchmark.md).

This result does not change the Heavy recommendation: Nemotron Labs 3 Puzzle
75B remains the best measured Heavy candidate, still unpromoted pending a pinned
stable engine. The Qwen MXFP4 recipe is retained only for reproducible engine and
weight comparisons; the materially different next experiment is llama.cpp with
the actual MXFP4_MOE GGUF path reported by the external single-card benchmark.

### Nemotron Puzzle deterministic-eval recheck (2026-07-12)

Nemotron Puzzle 75B was reloaded using its pinned checkpoint revision and the
same vLLM nightly image used in the extension round. Full preflight passed,
including the 128K needle and 20/20 tool calls. Its conventional 8K benchmark
reported 15.22 aggregate output tok/s and 458.93 ms TTFT p50, but the model
generated only 101 tokens across ten requests; the prior controlled 137.0 tok/s
long-generation measurement remains the useful decode result.

On the same new deterministic planning suite used for Qwen, Nemotron passed
**0/5** cases versus Qwen's 1/5. This adds no quality-promotion evidence and
does not change the recommendation: Nemotron remains the best measured Heavy
capacity candidate but stays unpromoted pending a pinned stable engine and
broader quality calibration. See the
[dated recheck and raw artifacts](findings/2026-07-12-nemotron-puzzle-recheck.md).

### GPT-OSS-120B deterministic-eval control (2026-07-12)

The production GPT-OSS-120B Heavy serve passed full preflight, including the
128K needle and 20/20 tool calls. Its conventional short 8K run measured 29.87
aggregate output tok/s and 655.67 ms TTFT p50; the established 183.2 tok/s
controlled long-generation result remains the meaningful decode baseline.

On the exact 256–384-token planning suite, GPT-OSS scored **0/5**, but four
cases returned no visible answer: native hidden reasoning consumed the entire
completion budget and ended with `finish_reason: length`. A diagnostic copy
that changed only the cap to 2,048 produced visible content for all five cases
and scored **1/5**. Therefore the exact-cap GPT-OSS score is not a valid model
quality comparison. `--suite-file` comparisons involving reasoning-channel
models need model-aware reasoning headroom or explicit reasoning-effort control
and should retain finish-reason/reasoning metadata. See the
[dated control and raw artifacts](findings/2026-07-12-gpt-oss-120b-deterministic-recheck.md).

**Historical operator verdict for these artifacts: the protocol was broken.** Do not
use the reported Qwen 1/5, Nemotron 0/5, or GPT-OSS 0/5 results for model
ranking or promotion. Protocol-v3 now adds reasoning controls, explicit
visible/reasoning allocations, finish/reasoning metadata, robust deterministic
regex checks, failure classification, and repeated runs; only new artifacts
that actually use those fields are eligible for comparison. This verdict does
not imply that deterministic checks over valid visible answers are themselves
nonfunctional.

The original built-in GPT-OSS bakeoff was rerun as a control. Its 131K context,
tool, session, and unified-diff checks passed, but the timeout-triage
intelligence case returned no visible answer after spending its full 256-token
budget in native reasoning. The older eval is therefore narrower and mostly
functional, but its intelligence score has the same missing reasoning-control
problem and is not currently stable promotion evidence.

### Heavy intelligence challengers (2026-07-12)

Two official NVFP4 checkpoints were validated one at a time on the single RTX
PRO 6000 through vLLM nightly at 131K with a five-sequence admission cap.
Mistral Small 4 119B completed 5/5 requests at concurrency five and scored 9/10
on the ARC sanity slice, but failed both built-in intelligence checks on the
final no-prefix-cache recipe. Nemotron 3 Super 120B completed 5/5, passed every
built-in Heavy check, and scored 5/5 on the thinking-enabled ARC tie-break.

Nemotron 3 Super was therefore the **best validated Heavy experiment in that round**
and the selected resident direct endpoint at capture time, superseding both Nemotron Puzzle's
capacity-only recommendation and ThinkingCap's Qwen-only selection. It is not
promoted into the production router. The short ARC slices remain sanity checks,
not general-quality or promotion evidence, and the served 131K window does not
validate Nemotron's advertised 1M maximum. See the
[dated finding and raw artifacts](findings/2026-07-12-heavy-intelligence-challengers.md).

The repaired protocol-v2 rerun strengthens that choice. Across three attempts
per item, Nemotron with 1,024 reasoning-headroom tokens scored 15/15 on the
five-item ARC sanity slice and 23/30 attempts with 8/10 stable items on a
ten-category MMLU-Pro slice. Mistral needed 2,048 headroom tokens to reach
15/15 ARC and then scored 14/30 with 5/10 stable MMLU-Pro items. Doubling
Nemotron's headroom to 2,048 did not improve its MMLU-Pro result and added 57
seconds of wall time. Poolside Laguna XS 2.1 NVFP4 was also tested through
vLLM and SGLang but rejected on this sm_120 host because neither tested recipe
produced trustworthy output. See the
[protocol-v2 finding and raw artifacts](findings/2026-07-12-rtx-pro-6000-heavy-eval-v2.md).

### Qwen3.6 protocol-v2 comparison and Unsloth NVFP4 follow-up (2026-07-12)

The same repaired repeated ARC and MMLU-Pro slices were run across the community
NVFP4+MTP checkpoint, official FP8, ThinkingCap FP8, and Unsloth's July 2026
NVFP4 checkpoint on the single RTX PRO 6000. At the matched 1,024-token
reasoning-headroom point, ThinkingCap was the strongest Qwen: 5/5 stable ARC
items and 7/10 stable MMLU-Pro items. The other three Qwen variants were
dominated by completion-budget exhaustion at that cap, so those constrained
scores are not intelligence rankings.

A model-specific headroom calibration selected 4,096 tokens for ThinkingCap.
Its three-repetition confirmation reached **9/10 stable MMLU-Pro items and
27/30 passing attempts**, while retaining its 15/15 ARC result at 1,024. This
is the highest stable quality-slice score in the current Heavy round, ahead of
Nemotron 3 Super's 8/10, but it costs materially more reasoning budget and wall
time. Nemotron remained the better matched-budget/latency result. ThinkingCap was
promoted as the routed **Heavy default** on 2026-07-12, then superseded by
Gemma 4 12B on 2026-07-16. ThinkingCap
passed a thinking-disabled functional gate (coding, JSON, 131K needle, 20/20
tools) and a separate thinking-enabled gate with 256 visible tokens plus 4,096
reasoning-headroom tokens. Both gates retained finish/reasoning evidence before
the guarded router promotion; GPT-OSS-120B was its complete rollback state.
See the [promotion finding and raw evidence](findings/2026-07-12-thinkingcap-heavy-promotion.md).

The Unsloth checkpoint used its required vLLM 0.25.0 / FlashInfer 0.6.13 /
CUTLASS DSL 4.5.2 path with native FlashInfer-CUTLASS NVFP4 and embedded MTP.
It passed full preflight and 5/5 requests at concurrency five, but needed 8,192
reasoning-headroom tokens to reach a one-pass 9/10 calibration and was slower
than ThinkingCap's 4K operating point. See the
[dated finding and raw artifacts](findings/2026-07-12-qwen36-protocol-v2-comparison.md).

Protocol-v3 external suites are fail-closed and resource-bounded: no more than
100 evals, 20 repetitions per item, 500 aggregate attempts, 65,536 completion
tokens per attempt, or 2,000,000 requested quality tokens per run. Regex checks
accept only a conservative deterministic-marker subset (literals, anchors,
boundaries, non-repeated character classes, `\s*`, and final-marker `[*]*`),
not arbitrary Python regexes.

## OpenClaw interaction and voice evidence

The current shared Dark-host STT qualification uses 24 deterministic
LibriSpeech human recordings plus six separately reported synthetic agent
phrases. Parakeet `tdt-0.6b-v3` remains the routed default. Qwen3-ASR 0.6B is
now a qualified but unpromoted replacement candidate: its 3.621% primary WER
was within the predeclared one-point margin of Parakeet's 3.343%, while its
113.58 ms sequential p95 was 36.1% faster. Nemotron 3.5 ASR was stable but is
not qualified because its 6.685% WER exceeded the margin.

| STT candidate / exact tested configuration | Sequential primary p50 / p95 | Primary normalized WER | Concurrency-4 primary p95 | Outcome |
|---|---:|---:|---:|---|
| Parakeet `tdt-0.6b-v3`, current Dark endpoint | 72.35 / 177.87 ms | 3.343% | 240.43 ms | **Current default**; no route change in this qualification. |
| Qwen3-ASR 0.6B, revision `5eb1441`, official Qwen base plus pinned vLLM parser patch | 67.40 / 113.58 ms | 3.621% | 137.36 ms | **Qualified replacement candidate**; human promotion gate still required. |
| Nemotron 3.5 ASR Streaming 0.6B, revision `f3d3333`, Transformers 5.13.0 one-shot endpoint | 121.60 / 225.45 ms | 6.685% | 747.82 ms | **Not qualified**; WER regressed 3.343 absolute points. Native streaming/NIM remains untested. |

The complete method, exact image IDs, auto-language probes, restoration proof,
runtime fixes, and raw per-sample evidence are in the
[Nemotron 3.5 ASR qualification](findings/2026-07-28-nemotron35-asr-qualification.md).

The earlier July 8 single-sample smoke remains historical compatibility
evidence, not the current quality comparison. It used former default
`tdt_ctc-110m`, identified Qwen's provider-prefix quirk, and rejected two
Whisper Turbo vLLM recipes for repeated hallucination. Full methodology and all
17 raw runs are in the
[historical STT model benchmark](findings/2026-07-08-stt-model-benchmark.md).

| Scenario | Scope | Measured result | Interpretation |
|---|---|---|---|
| OpenClaw COLO interaction benchmark | Mini gateway to Dark router; `chat-fast`; 10 requests | 10/10 HTTP 200; latency p50/p95 568.6 / 1259.9 ms; exact-generation throughput p50/p95 82.77 / 171.82 tok/s | Current route and interaction path was functional. The run carried a warning because it did not include `--run-generations`. |
| Optional Mini-local audio baseline | `mini-audio`; baseline Qwen3.6-27B | TTFA 611.29 ms; full turn 789.06 ms; STT / LLM / TTS 106.28 / 356.82 / 325.95 ms | Useful same-host baseline only; it is not a valid reference A/B topology. |

The interaction result is documented in the [live OpenClaw Talk validation](findings/2026-07-08-openclaw-talk-live-validation.md). The audio baseline is preserved in the [voice latency candidate matrix](findings/2026-07-08-voice-latency-candidate-matrix.md) and [final voice recommendation](findings/2026-07-08-voice-latency-final-recommendation.md). For reference OpenClaw Talk and candidate A/B testing, Fakoli Mini stays model-free: use Dark-host audio or a Mini-side proxy to Dark rather than treating the Mini-local row as a candidate comparison.

## Publish a new benchmark result

Publish every user-relevant model benchmark in the same change that records the result. This keeps the public documentation useful while preserving the evidence needed to interpret it.

1. Run the applicable correctness gate before capacity testing (`preflight` before `benchmark`; functional checks before a voice or gateway claim). Save the machine-readable artifact.
2. Add a dated narrative under `docs/findings/` and list it in [the findings index](findings/README.md). Include the tested and served model identifiers, capture date, hardware and host/topology, engine and version, quantization, context and concurrency, exact command or artifact path, metrics, gate outcomes, failures, and caveats.
3. Update this page when the result changes the current recommendation, the reference deployment, or a comparison a reader needs to make. Link the dated finding rather than duplicating raw JSON.
4. Mark external data as an advisory prior and negative or incomplete runs as such. Do not turn a capacity result into a quality claim, and do not conceal failed load, context, tool, or topology gates.
5. Do not change a router profile, a production serve, or cloud routing merely because the documentation was updated. Those changes retain their explicit human approval gates.

For the command-level workflow and artifact expectations, see [Operator playbooks](OPERATOR-PLAYBOOKS.md#start-validate-and-benchmark-a-serve). Contributors and agents must follow this publication contract; the repository guidance in `CONTRIBUTING.md` and `AGENTS.md` makes it part of every model-benchmark change.
