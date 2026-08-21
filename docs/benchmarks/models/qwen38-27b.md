# Qwen3.8 27B

## Current status and review date

Former human-approved single-service profile: official FP8 on SGLang served
Primary, multimodal, OCR, and video at TP=1/393,216 with EAGLE MTP `3/1/4`
and CPU feature transport; the second GPU was empty. On 2026-08-16 it was
superseded as text Primary by DeepSeek Infernal Invocation r15. Review date:
2026-08-21. The review includes MTP=4/5, official-FP8 versus Inferact NVFP4, the
matched BF16 consolidation A/B, and the guarded live promotion.
The 2026-08-16 review adds direct and routed video qualification plus
fail-closed router admission for one video.
The 2026-08-17 review adds a separate RTX 5090 qualification of the RadixArk
NVFP4 checkpoint, first at 65,536 and then at 131,072 tokens, including native
video, mixed media, and eight-image/two-video boundary coverage. It is a
measured direct challenger only; no route or promotion changed.
The 2026-08-20 review adds a matched stock-versus-Sharp-v22.1 chat-template A/B
on that 128K RTX 5090 profile. Sharp passed the functional gate but did not
improve the bounded thinking-enabled diagnostic, so the stock template remains
selected and no route or promotion changed.
The 2026-08-21 review adds the exact official RTX 5090 NVFP4 DFlash2
high-throughput/float32 arm. It booted at memory fraction 0.945 but exposed
only 24,347 KV tokens and failed the retained 128K capacity gate. A bounded
BF16/single-slot tuning ladder raised the safe ceiling to 70,262 KV tokens and
passed a 49,549-token retrieval plus tools 20/20, but still could not satisfy
the route contract. Both arms are rejected as replacements and the stock 128K
recipe remains selected.
The same review adds a dated external-source registry and a matched local
MTP3/ReplaySSM A/B. ReplaySSM made recurrent-state replay negligible and decode
rose 80.5% at 4K and 67.9% at 64K, but SGLang's separately loaded 5.73 GB MTP
draft left only 70,231 KV tokens. Median 64K end-to-end latency was also 1.9%
slower. The candidate is rejected as a 128K replacement; EXL3, NInfer, vLLM
TurboQuant, and alternative NVFP4 recipes remain research leads only.

## Immutable identity

- Official BF16 revision:
  `Qwen/Qwen3.8-27B@1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`.
- Official FP8 revision:
  `Qwen/Qwen3.8-27B-FP8@017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`.
- Runtime image digest:
  `sha256:4a2f33a884222f7049b983263ad9976f89452bb81affecf5b67d89ad35c1bc31`;
  vLLM revision `3a0914114705fa38d4c3171d0746c1a6b6f10209`.
- SGLang qualification image:
  `lmsysorg/sglang@sha256:506525a5907ea22c9d445afb7c03603959b912de034d86915cf17da814f1a124`;
  image-label revision `c4271c3fe1262fc2adbd162c33b25de5255251c5`.
- Inferact NVFP4 qualification revision:
  `Inferact/Qwen3.8-27B-NVFP4@6128240ebaf4eaa7bad2b3d1c72c37d677c5f462`.
- RTX 5090 RadixArk NVFP4 qualification revision:
  `RadixArk/Qwen3.8-27B-NVFP4@554ebba9b5f1b79dc11246341960360e6ef05ef4`.
- Sharp chat-template qualification revision:
  `peculiar-ragdoll/Qwen-Sharp-Chat-Templates@3dc34df52c63dd22ada21f96435e069deaa8d7da`.
- DFlash2 draft qualification revision:
  `incoai/Qwen3.8-27B-DFlash2@dedf8df68adfb1afeaf7b7480c0a0243108177b4`.
- DFlash2 runtime image:
  `lmsysorg/sglang:dev@sha256:8acc563e39f4e79118cc3c11cb5a8893ca8da140b2280cdd24a9f3bfe38835a0`;
  image-label source revision
  `f825d729363136a2d4a4b330fa694d0b37a878fa`.

## Tested hardware and topology

The former profile used one TP=1 serve on one of two equal 96 GB RTX PRO 6000
Blackwell Max-Q cards and left the other card empty. Prior tests include one
TP=1 serve per card in split mode and exclusive TP=2 at 393K, 600K, and 1.01M.
The cards are independent PCIe devices; aggregate VRAM is not unified memory,
and the TP=2 runtime could not enable GPU P2P.
The 2026-08-17 challenger used one 32 GB RTX 5090 at TP=1. It is a distinct
hardware and context lane and is not directly comparable to the 96 GB-card
393K production history.

## Engine, quantization, KV, context, and concurrency recipe

BF16 multimodal used BF16 weights, FP8 KV, 262,144 native context, and two
sequences. Official FP8 text used FP8 weights and KV, the same native context,
and five sequences. Both used vLLM V1 with chunked prefill, no prefix caching,
and no speculative decoding for the control. MTP=3, prefix caching, and
unquantized KV were isolated one-variable arms.

The extended-context arm kept official FP8 weights, FP8 KV, TP=1, chunked
prefill, no prefix caching, and no MTP, but configured 1,010,000 tokens with
one admitted sequence and the official nested `text_config` override.

The matched TP/MTP matrix fixed one admitted sequence and 4,096 batched tokens
for both checkpoints. Split TP=1 used 393,216 tokens. Exclusive TP=2 used
393,216, 600,000, and 1,010,000 tokens. Every point had an otherwise identical
no-MTP control and `method=mtp,num_speculative_tokens=3` arm.

The historical split selected the 393,216-token TP=1 MTP=3 arm for both vLLM
models. The later single-service profile instead ran the official-FP8 SGLang
MTP=3 multimodal arm for Primary, general vision, OCR, and video. It admitted
one running request, two images, and one video; the second GPU was empty. The
former FP8/BF16 split is also retained as historical managed evidence.

The SGLang control A/B held TP=1, 393,216 tokens, one running request, FP8
E4M3 KV, FlashInfer attention, 2,048-token chunks, memory fraction 0.85,
disabled prefix cache, one GDN state slot, text-only mode, and no speculative
decoding fixed. The follow-up added cookbook EAGLE `3/1/4` and raised the GDN
state cache to five slots. Its multimodal arms retained MTP and forced CPU
feature transport instead of the failing automatic CUDA-IPC path. Both rounds
compared official FP8 with Inferact ModelOpt NVFP4 and swapped placement across
the equal cards.

The retained RTX 5090 RadixArk arm uses SGLang at the same pinned image digest,
TP=1, 131,072 tokens, one running request, FP8 E4M3 KV, FlashInfer attention,
2,048-token chunks, disabled radix cache, one Mamba/GDN state slot, CPU
multimodal feature transport, no MTP, thinking disabled, and explicit ceilings
of eight images and two videos. The managed recipe is
`configs/qwen38-27b-radixark-nvfp4-sglang-rtx5090-128k-mm-recipe.toml`; the
otherwise matched 64K recipe is retained as rollback.

The rejected DFlash2 arm kept the same target snapshot and stable served name,
but used the separately pinned draft, DFLASH with eight draft tokens, the
official RTX 5090 memory fraction 0.945, float32 Mamba state, full-memory ratio
10, `extra_buffer_lazy`, and one admitted request. It configured 262,144 model
context but allocated only 24,347 target KV tokens. Its retained recipe is
`configs/qwen38-27b-radixark-nvfp4-sglang-rtx5090-262k-dflash2-recipe.toml`.
The best measured diagnostic changed Mamba state to BF16, pinned one persistent
state slot, disabled radix caching and prefill CUDA graphs, and retained memory
fraction 0.945. It allocated 70,262 KV tokens; its reproducible recipe is
`configs/qwen38-27b-radixark-nvfp4-sglang-rtx5090-dflash2-debug-recipe.toml`.

## Evidence by measurement class

Both official variants passed the thinking-disabled functional gate, repeated
coding/tool/session checks, adaptive reasoning-control probes, and retrieval
through 241,250 actual prompt tokens. BF16 passed 30/30 image/video/mixed-media
attempts. Official FP8 measured 47.9 tok/s c1 decode and 51 aggregate output
tok/s at c5, versus BF16's 26.9 tok/s c1 and 27 aggregate output tok/s at c2.

On official FP8, MTP=3 increased c1 decode to 94.8 tok/s and retained the
repeated quality gate; prefix caching reduced a repeated 30K-prefix c5 burst
from 16.39 seconds TTFT with caching disabled to 0.41 seconds warm; unquantized
KV retained correctness and 244,573-token retrieval but halved reported
full-window capacity from 6.96 to 3.55 windows without a 4K speed gain.

The 1M-configured continuation passed a monotonic retrieval ladder through
825,049 actual prompt tokens. The largest point passed 3/3 with exact output
and a 956.739-second mean request-to-completion latency. A full post-stress gate
also passed. This is stable offline/batch capacity evidence, not an interactive
latency result or proof of a one-million-token API prompt.

The later topology matrix passed every arm at 388,979 actual prompt tokens for
393K, 598,729 for 600K, and 985,107 for 1.01M. At 393K, TP=2 reduced control
TTFT 38% for BF16 and 35% for official FP8. Official FP8 TP=2 control measured
154.8/321.2/784.1 seconds TTFT across the three largest rows. MTP raised 4K
decode 1.76-2.40x but used 7-11% of the engine-reported KV-token pool and did
not improve extreme-context TTFT consistently. Each largest row is one cold
pass; only the 4K 10-request runs carry p50/p95 statistics.

The 2026-08-15 official-FP8 follow-up tested MTP=4 and MTP=5 concurrently,
then swapped the settings across the two equal cards. Both passed complete
functional checks, repeated deterministic intelligence/session/tool suites,
and one cold 388,979-token request. On a fixed card, MTP=5 exceeded MTP=4
decode by only 0.4-1.3% and made E2E slightly worse. The earlier apparent 6.9%
MTP=4 lead reversed with placement and was lane variance. On the production
Compute B lane, the historical matched MTP=3 control remains ahead at 93.6
tok/s versus 91.6 for MTP=5 and 90.4 for MTP=4.

The same day's SGLang control qualified both official FP8 and Inferact NVFP4
on two placements. Both passed complete functional checks, deterministic
intelligence/session/tools, and 388,979 actual prompt tokens. Across five 4K
runs per model, NVFP4 averaged 0.429 seconds TTFT, 8,409 effective prefill
tok/s, 57.9 decode tok/s, and 1.244 seconds E2E, versus official FP8's 0.554
seconds, 6,512 tok/s, 48.0 tok/s, and 1.451 seconds. NVFP4 retained a smaller
advantage at the near-limit row: 248.75 versus 258.13 seconds TTFT. The card
swap reproduced the ranking, but the then-current vLLM MTP=3 result remained
much faster at 93.6 decode tok/s.

The matched SGLang MTP=3 follow-up changed the decode ranking. Across five 4K
runs and both card placements, official FP8 averaged 0.569 seconds TTFT, 6,341
effective prefill tok/s, 111.3 decode tok/s, and 0.954 seconds E2E. NVFP4
averaged 0.448 seconds, 8,065 tok/s, 98.1 tok/s, and 0.914 seconds. Relative to
their no-spec controls, MTP raised decode 131.9% for official FP8 and 69.4% for
NVFP4. Both passed 389K retrieval and repeated intelligence 6/6, session 3/3,
and tools 3/3. CPU feature transport also let both MTP profiles pass bounded
single-image understanding and OCR.

The consolidation A/B then compared official BF16, official FP8, and Inferact
NVFP4 under the same SGLang MTP=3/393K/CPU-transport shape. All three passed
18/18 across scene, OCR, chart, UI, spatial-count, and two-image comparison
cases. Official FP8 cut median media latency 35.8% versus BF16 and raised 4K
decode from 62.7 to 111.4 tok/s. NVFP4 cut media latency 51.1%, halved TTFT,
and doubled effective prefill versus BF16, but decoded 12.3% slower than
official FP8. Official FP8 is therefore the preferred single-service
challenger; video, 32-image, concurrency, host-memory-pressure, broad vision
quality, client acceptance, and a human gate were still required before the
subsequent replacement of the then-current split.

The human-approved promotion then applied that exact official-FP8 profile.
The managed cutover passed exact identity, coding, JSON, a 108K retrieval
needle, and 20/20 tools. Direct and routed copies of the repeated image corpus
both passed 18/18; routed image/OCR, streaming tools, tool-result recovery, and
the Responses subset passed as well. Fresh Hermes and OpenClaw Primary turns
completed without fallback. The qualified admission ceiling was two images,
one video, and concurrency one after the 2026-08-16 router-only expansion; the
broader 32-image and concurrency gates were not silently inherited from BF16.

The video follow-up kept the model and recipe unchanged. The complete direct
deterministic corpus passed 30/30, including video 14/14 and mixed media 4/4.
Video latency across those attempts was 2.935 seconds p50 and 9.904 seconds
p95. The live routed admitted subset passed 28/28; the excluded case contains
four images plus a video and correctly receives 413 under the two-image limit.
Two-video overflow, malformed input, SSE ordering, grounded tool use, and the
complete Primary regression gate also passed.

The 2026-08-17 RTX 5090 RadixArk NVFP4 qualification first established the 64K
baseline, then retained a separate 128K recipe. The 128K arm passed coding and
JSON, exact retrieval at 119,675 actual prompt tokens, and 20/20 tool calls.
Its native multimodal run passed 30/30: image 12/12, mixed media 4/4, and video
14/14, including temporal ordering, state change, event localization, video
OCR, 120-second continuity, and real-world clips. A separate boundary corpus
passed 4/4: two eight-image attempts and two two-video attempts. The loaded
weights consumed 20.14 GB; the engine exposed a 167,789-token FP8 KV pool and
the host reported 3,928 MiB free after startup. This is bounded
single-concurrency qualification evidence, not a broad computer-use benchmark
or a promotion.

Evidence classes are `functional`, `capacity`, bounded `quality`, and
multimodal. A later durable router-only campaign added separate agentic and
SWE-bench evidence: agentic smoke passed 2/2, the scout passed 16/18 with both
failures in the debug-loop case, and all five fixed SWE-bench Verified scout
instances resolved under the official grader. That five-instance sample is
bounded evidence, not a full-benchmark score.

The 2026-08-20 RTX 5090 A/B kept the RadixArk weights, SGLang digest, 128K
shape, GPU, and request budgets fixed. Sharp v22.1 passed the complete
thinking-disabled functional preflight. On the thinking-enabled MMLU-Pro
diagnostic it matched stock at 24/30, including the same two budget-exhausted
items, while using 10.8% more completion tokens and taking 10.7% longer. On a
smaller thinking-disabled behavior suite it used 5.1% fewer tokens and was 5.0%
faster, but passed 15/18 versus stock's 18/18 because it missed a declared
literal-question-mark contract. This is bounded template evidence, not a
general model-quality score.

The 2026-08-21 DFlash2 trial is `compatibility-only` plus measured `capacity`.
An initial local transcription at memory fraction 0.895 loaded both models but
could admit zero requests. The corrected official 0.945 arm started, allocated
five Mamba cache slots, and served short coding/JSON plus twenty HTTP-successful
tool requests. Its 24,341-token maximum input then rejected a 105,649-token
retrieval prompt. DFlash scheduler samples showed 5.05-5.60 accepted tokens per
step, but no controlled speed or quality benchmark was retained.

The follow-up capacity ladder measured 30,984 KV tokens with BF16 state at
memory fraction 0.90, 64,790 after raising only the fraction to 0.945, the same
64,790 with prefill graphs disabled, and 70,262 after also disabling radix and
pinning one persistent Mamba slot. The final arm passed coding, JSON, a 49,549-
token retrieval prompt, and tools 20/20. It still fell 35,387 tokens short of
the existing 105,649-token route gate. The exact stock 128K recipe was restored
and passed that complete gate again.

The matched MTP3/ReplaySSM candidate used the same target snapshot and API
contract. At 4K, median decode improved from 76.54 to 138.19 tok/s and E2E fell
from 911 to 664 ms. At 64K, decode improved from 69.75 to 117.10 tok/s, but
effective prefill fell from 5,936 to 5,581 tok/s and E2E rose from 11,309 to
11,528 ms. Startup allocated 20.14 GB target weights, a separate 5.73 GB draft,
0.28 GB for one FP32 Mamba state slot, and only 70,231 target/draft KV tokens.
It passed the complete bounded functional/tool surface at a 49,549-token
prompt, then was rejected at the hard context gate. The baseline restoration
passed the same surface at 105,649 prompt tokens.

## Decision and promotion state

Former human-approved single-service profile. Official FP8 on SGLang with MTP
`3/1/4` served Primary, general vision, OCR, and video at 393,216 tokens on one
card while the other card was dormant. Direct video qualification passed
14/14 and the live admitted corpus passed 28/28; Hermes/OpenClaw Primary client
paths passed without fallback. The SGLang profile and former vLLM FP8/BF16
split are retained managed recipes, but neither is the immediate text
rollback; DeepSeek r33 393K now fills that role. TP=2 at 600K and 1.01M remains
an offline/batch experiment.
The RadixArk NVFP4 RTX 5090 recipe is the locally qualified 32 GB challenger
when native vision and video are required. It remains `no-promotion`, and its
131,072-token served window should not be conflated with the former 393K lane.
Sharp v22.1 is rejected for this exact recipe; the stock template remains the
qualified configuration.
The exact official DFlash2 high-throughput/float32 profile is also rejected as
a replacement for that 128K service. The BF16/no-radix/single-slot arm is the
best measured short-context lead, but it is likewise rejected as a route
replacement. Any distinct short-context deployment requires its own truthful
served name, capability contract, benchmark, and human promotion gate.
MTP3 plus ReplaySSM is rejected for the same route for a different memory
reason: ReplaySSM removes the recurrent-state multiplier, but the 5.73 GB draft
weight reservation still caps KV at 70,231. External full-context candidates
remain dormant until one proves lower local end-to-end latency without losing
the quality, tool, multimodal, or restoration contracts.

## External recipe watch and local follow-up

The 2026-08-15 external refresh added two dormant, official-weight vLLM
qualification recipes. They change only the speculative depth from the
qualified TP=1/393K MTP=3 recipe:
`configs/qwen38-27b-fp8-tp1-393k-mtp4-recipe.toml` and
`configs/qwen38-27b-fp8-tp1-393k-mtp5-recipe.toml`.
A same-product community sweep reports the best decode at depth 5, but its
prompts, concurrency, runtime details, and quality method differ from the local
campaign. The local two-lane and cross-card follow-up found no meaningful E2E
win for depth 4 or 5, so MTP=3 remains the selected Qwen depth and both deeper recipes remain
dormant `no-promotion` controls.

The SGLang cookbook follow-up is complete for no-speculation and in-checkpoint
MTP text serving. Digest-pinned executable recipes retain official FP8 and the
explicitly approved Inferact NVFP4 checkpoint. The NVFP4 snapshot passed full
Safetensors structure and immutable LFS SHA-256 verification before load.
Bounded image/OCR and a repeated six-case corpus pass on BF16 and both
quantized checkpoints when CPU feature transport is forced. Two-image ordering
is covered. The former official-FP8 profile subsequently passed one-video
qualification; NVFP4 video, the 32-image ceiling, concurrency, broad vision
quality, and host-memory-pressure remain open. The default CUDA-IPC path still fails in this exact WSL2/Docker/runtime
combination. The widely shared 200+ tok/s result also adds a DSpark draft and
remains an `external-prior`, not a local result.

Other NVFP4, GGUF, AutoRound, and custom `.ninfer` artifacts remain excluded
from the active production queue. Inferact NVFP4 remains a `no-promotion`
control. RadixArk NVFP4 is now a qualified RTX 5090 challenger with native
video evidence, while the official-FP8 SGLang arm remains the retained former
96 GB-card Qwen profile. The Unsloth GGUF fallback was not downloaded because
the native NVFP4 target passed its required gates.

## Failures and gotchas

- The Sharp v22.1 A/B produced complete attempt records, but the generic
  inspector flags unrelated built-in suites as `not_run`, missing aggregate
  chat timing, and thinking control as `requested_unverified` in the quality
  lanes. Those artifacts are bounded diagnostics, not promotion-grade evidence.
- Both stock and Sharp exhausted the 2,048-token completion cap on the same two
  MMLU-Pro items. Sharp also failed the narrow ambiguous-request punctuation
  contract despite safely declining to guess and requesting the missing input.
- Official FP8 startup warned that absent attention q/prob scaling factors
  defaulted to 1.0. No independent quality result proves equivalence to
  unquantized KV.
- vLLM warned that 4,096 batched tokens may be suboptimal with MTP=3. A later
  tune must be a matched one-variable A/B. The same warning appeared for
  MTP=4 and MTP=5.
- MTP=4/5 short decode differed by roughly 7-8% between equal card roles.
  Cross-card placement is therefore required before attributing a small
  speculative-depth delta to the recipe.
- The MTP=4/5 deterministic quality artifacts contain complete suite attempts
  but no aggregate chat timing fields; they are bounded behavioral evidence,
  not timing comparisons.
- The durable separate-worker campaign subsequently completed through the
  approved router alias. It exposed a repeated debug-loop weakness and a wide
  19-57 model-request range across the five resolved SWE tasks; neither the
  16/18 agentic result nor 5/5 fixed SWE sample should be generalized to a full
  benchmark score.
- General-vision output is materially more verbose than OCR. The first routed
  corpus exposed a dropped `chat_template_kwargs` extension; a
  thinking-disabled soft default and same-dialect relay forwarding corrected
  it without raising the final 512-token corpus cap.
- The 1M-configured retrieval harness produced at most 825,049 API-reported
  prompt tokens, and each largest run took almost 16 minutes.
- The later matrix reached 985,107 actual prompt tokens on both checkpoints in
  TP=2, but TTFT remained 13.0-13.7 minutes. The result supersedes the earlier
  prompt-depth limit, not its offline/batch recommendation.
- TP=2 lacked P2P and used PyNCCL over the socket-backed local path after vLLM
  disabled custom allreduce.
- SGLang's first 393K launch required the explicit longer-context overwrite
  opt-in; its first WSL2 multimodal warmup failed with an invalid CUDA resource
  handle. CPU feature transport now passes bounded image/OCR and one-video
  qualification on the then-current official-FP8 profile, but it is not a blanket
  CUDA-IPC diagnosis and does not qualify 32 images.
- The SGLang image label names `c4271c3`, but its internal build-version string
  names `561c8f3`; the digest is the execution identity and the discrepancy is
  retained.
- Both SGLang candidates warned that missing FP8 KV scaling factors defaulted
  to 1.0. The bounded gates passed, but unquantized-KV equivalence is unproven.
- The RTX 5090 RadixArk run emitted the same FP8-KV scaling-factor warning.
  Its bounded gates passed, but equivalence to unquantized KV remains unproven.
- The first eight-image boundary artifact used expectations that did not exist
  in the hash-pinned images. Inspection and the model output exposed the test
  error; the corrected canonical-label corpus passed 4/4, and both artifacts
  remain published.
- The benchmark harness now records an explicit, bounded multi-video evidence
  ceiling. That flag changes corpus admission only, not engine or router
  policy.
- The generic evidence-inspector command does not recognize the multimodal
  benchmark schema even though its complete attempt records report 30/30. A
  product-gap ticket tracks that fail-closed inspection limitation.
- The first routed Responses probe supplied a chat-only thinking field that
  SGLang rejects on `/v1/responses`. The redundant router soft default was
  removed; the recipe-level default keeps thinking disabled and the Responses
  subset passed. Chat-completions retains the caller control.
- The generic evidence inspector flags absent aggregate chat timing in the
  deterministic agentic artifacts and unrelated `not_run` suites in the
  context-only artifacts. Only complete attempt/target records ground the
  published claims; aggregate quality timing is not claimed.
- The RTX 5090 DFlash2 float32 arm requires memory fraction 0.945 and Mamba
  full-memory ratio 10. At 0.895 it admitted zero requests; at 0.945 it booted
  but left only 24,347 target KV tokens and zero reported free GPU memory after
  target graph capture. Configured 262K context must not be reported as
  measured request capacity for this layout.
- BF16 state, one persistent Mamba slot, disabled radix cache, and disabled
  prefill CUDA graphs raised the safe ceiling to 70,262 KV tokens. The tuned
  arm passed a complete 49,549-token functional gate, but the eight-token
  DFlash verifier still reserved 1.12 GB of intermediate BF16 SSM state.
- No controlled throughput, quality, routed, or multimodal claim is retained.
  Logged DFlash acceptance and throughput values remain diagnostic samples.

## Dated run history

- [2026-08-21 RTX 5090 recipe research and MTP3/ReplaySSM rejection](../../findings/2026-08-21-qwen38-27b-rtx5090-recipe-research.md)
- [2026-08-21 RTX 5090 DFlash2 compatibility and capacity rejection](../../findings/2026-08-21-qwen38-27b-radixark-nvfp4-dflash2-rtx5090.md)
- [2026-08-20 RTX 5090 Sharp v22.1 chat-template A/B](../../findings/2026-08-20-qwen38-sharp-template-ab.md)
- [2026-08-17 RTX 5090 RadixArk NVFP4 128K qualification](../../findings/2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k.md)
- [2026-08-17 RTX 5090 RadixArk NVFP4 qualification](../../findings/2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090.md)
- [2026-08-16 video qualification and router expansion](../../findings/2026-08-16-qwen38-27b-video-router.md)
- [2026-08-15 agentic and SWE-bench Verified scout](../../findings/2026-08-15-qwen38-27b-agentic-swe-scout.md)
- [2026-08-15 SGLang official-FP8 single-service promotion](../../findings/2026-08-15-qwen38-27b-sglang-fp8-single-promotion.md)
- [2026-08-15 SGLang single-service consolidation A/B](../../findings/2026-08-15-qwen38-27b-sglang-consolidation-ab.md)
- [2026-08-15 SGLang MTP/multimodal qualification](../../findings/2026-08-15-qwen38-27b-sglang-mtp-multimodal-qualification.md)
- [2026-08-15 SGLang official-FP8/NVFP4 qualification](../../findings/2026-08-15-qwen38-27b-sglang-nvfp4-qualification.md)
- [2026-08-15 official-FP8 MTP-depth qualification](../../findings/2026-08-15-qwen38-27b-mtp-depth-qualification.md)
- [2026-08-15 external recipe refresh](../../findings/2026-08-15-qwen38-27b-external-recipe-refresh.md)
- [2026-08-14 split promotion](../../findings/2026-08-14-qwen38-27b-split-promotion.md)
- [2026-08-14 TP/MTP/context matrix](../../findings/2026-08-14-qwen38-27b-tp-mtp-context-matrix.md)
- [2026-08-14 official FP8 1M-context continuation](../../findings/2026-08-14-qwen38-27b-1m-context.md)
- [2026-08-14 official BF16/FP8 qualification](../../findings/2026-08-14-qwen38-27b-official-qualification.md)
