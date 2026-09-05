# Qwen3.8 27B

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** former human-approved 96 GB single-service profile;
      retained as reproducible evidence, not the current Primary or immediate
      text rollback.
    - **Selected or best-qualified configuration:** for the new PRO 6000
      throughput workload, two independent SGLang Inferact NVFP4 plus DFlash2
      K12/chunk1K TP1 replicas are the bounded winner; one TP1 is the selected
      single-card sustained-output arm, and RadixArk K8 is the lower-TTFT
      tradeoff. Historical production and RTX 5090 roles remain unchanged.
    - **Measured hardware:** one- and two-card RTX PRO 6000 lanes and a
      separate single-RTX-5090 lane; results are not interchangeable across
      those topologies.
    - **Evidence:** functional, capacity, bounded quality, performance,
      multimodal, routed-client, and rejection evidence through 2026-09-05
      UTC. The new matched sustained-output lane measured 1,401.8–1,423.4 aggregate
      tok/s for two TP1 replicas, 764.3 for one TP1, and 587.9 for TP2, with
      mean/p50/p95/p99 TTFT, prefill, decode, TPOT/ITL, and E2E retained.
      Gittensor measured 50.9 ms warm median TTFT, 79.5 tok/s decode, and
      passed a 244,002-token actual prompt. NInfer MTP3 retains 165.9 tok/s
      decode evidence. GGUF retains 253,822-token, image/OCR, agentic,
      endurance, and routed-client evidence.
    - **Decision:** retain DP2 as the bounded dual-PRO aggregate-throughput
      winner, one optimized TP1 as the single-card sustained-output selection,
      RadixArk K8 as its TTFT tradeoff, and kelnei/vLLM MTP2 as an alternate-
      runtime gain over no-spec. Reject TP2; all remain `no-promotion`.
    - **Important limitation:** TP2 failed strict JSON twice; unique-prefix
      82K/C8 remained unsuitable for interactive use; DP2 lacks a qualified
      balancing/failover/client path; and broad quality plus complete power and
      energy telemetry were not run. Gittensor's
      advertised DSpark pair failed on
      incompatible matrix shapes and its FP8 KV path used default 1.0 scales.
      It lacks routed/client, multimodal, broad agentic/SWE, endurance, and
      promotion-grade evidence. The measured hardware/engine lanes are not
      interchangeable.
    - **Review dates:** Retained evidence cutoff: 2026-09-05 UTC.
      Dossier-format review: 2026-09-04 local campaign date.

[Open the exact retained container configurations](../configurations.md#qwen38-27b-official-fp8)
or jump to the [decision](#decision-and-promotion-state),
[known limitations](#failures-and-gotchas), or
[dated evidence](#dated-run-history).

### Review narrative

The dated notes below preserve the reasoning behind each decision. The later
measurement sections retain the full metrics, controls, and caveats.

#### 2026-08-14–15 — Official checkpoints, topology, and promotion

The initial review covered official BF16 and FP8 qualification, the 1M-context
continuation, the matched TP/MTP topology matrix, MTP=4/5, official FP8 versus
Inferact NVFP4, the matched BF16 consolidation A/B, and the guarded live
promotion. Official FP8 on SGLang with EAGLE MTP `3/1/4` became the
human-approved single service for Primary, image, and OCR. The deeper MTP
settings did not improve end-to-end performance.

#### 2026-08-16 — Video and router admission

The same official-FP8 service completed direct and routed video qualification.
The router added fail-closed admission for one video without changing the
model recipe. The service was superseded as text Primary by DeepSeek Infernal
Invocation r15, but the Qwen configuration and evidence remain retained.

#### 2026-08-17 — RadixArk NVFP4 on RTX 5090

A separate 32 GB lane qualified the RadixArk NVFP4 checkpoint first at 65,536
and then at 131,072 tokens. It covered native video, mixed media, and
eight-image/two-video boundaries. This is a measured direct challenger only;
no route or promotion changed.

#### 2026-08-20 — Stock versus Sharp v22.1

The matched chat-template A/B held the 128K RTX 5090 profile constant. Sharp
passed the functional gate but did not improve the bounded thinking-enabled
diagnostic. The stock template remains selected, with no route or promotion
change.

#### 2026-08-21 — DFlash2 capacity rejection

The exact official RTX 5090 NVFP4 DFlash2 high-throughput/float32 arm booted at
memory fraction 0.945 but exposed only 24,347 KV tokens and failed the retained
128K capacity gate. A bounded BF16/single-slot tuning ladder raised the safe
ceiling to 70,262 KV tokens and passed a 49,549-token retrieval plus tools
20/20, but still could not satisfy the route contract. Both arms are rejected
as replacements; the stock 128K recipe remains selected.

#### 2026-08-21 — MTP3 and ReplaySSM

A dated external-source registry informed a matched local MTP3/ReplaySSM A/B.
ReplaySSM made recurrent-state replay negligible, and decode rose 80.5% at 4K
and 67.9% at 64K. However, SGLang's separately loaded 5.73 GB MTP draft left
only 70,231 KV tokens, and median 64K end-to-end latency was 1.9% slower. The
candidate is rejected as a 128K replacement.

At that date, EXL3, NInfer, vLLM TurboQuant, and alternative NVFP4 recipes were
research leads rather than local deployment claims. The September 3 NInfer
qualification below supersedes only that candidate's unmeasured status.

#### 2026-08-21 — GGUF Q4_0 and matching MTP head

The managed llama.cpp campaign qualified Unsloth GGUF Q4_0 with its matching
Q4_0 MTP head at 262,144 tokens on RTX 5090. It passed exact retrieval at
253,822 actual prompt tokens with an 8,192-token output reserve, long tools
after 110K, agentic 16/18, neutral 101-turn endurance 3/3, and images 18/18.
MTP raised matched short decode from 69.1 to 104.1 tok/s.

This is the preferred RTX 5090 `FAST-TIER` challenger, but the independent SWE
gate is incomplete and no route or promotion changed. Conventional Q6_K with
the same MTP head was disqualified by the conservative 32 GB capacity screen.

#### 2026-08-22 — Routed client follow-up

The routed follow-up passed real OpenClaw and Hermes identity plus
shell-tool/result-continuation smokes. It did not clear the 250K route gate:
the bounded test route still declared the earlier 131,072-token SGLang/NVFP4
compatibility fingerprint and video capability. Promotion remains closed until
the router and client catalogs truthfully describe the 262K llama.cpp
image-only recipe and routed acceptance passes with a 250,000-token minimum.

#### 2026-09-03 — NInfer NVFP4 and integrated MTP3

The exact current `.ninfer` artifact and NInfer runtime revision completed a
managed matched no-speculation/MTP3 qualification on one RTX 5090. At 4K/C1,
MTP3 raised median decode from 75.3 to 165.9 tok/s and reduced median E2E from
1.085 to 0.720 seconds while TTFT changed from 0.421 to 0.430 seconds. It
returned the exact marker from a 201,746-token API-reported prompt while
accepting an 8,192-token completion cap. Bounded smoke, JSON, C1/streaming/
continuation tools, coding, triage, and repeated tool gates passed.

It remains a direct performance challenger only. Both 20-way shared-prefix
tool bursts finished 17/20 under the C1 scheduler, MTP3 left 2,354 MiB free
against the ordinary 3 GiB reserve, and the runtime intake still resolves
Ubuntu packages without immutable package versions. The GGUF incumbent was
restored; no route or client catalog changed.

#### 2026-09-03 — RTX 5090 quant and speculation bakeoff

A later same-day managed comparison loaded fresh Gittensor, cdiamond, QUASAR,
CometKim, Red Hat, and Telperion recipes after measuring a process-free idle
GPU baseline. Gittensor's target-only NVFP4/SGLang arm won the declared
primary metric with 50.9 ms warm median TTFT, passed a 244,002-token actual
prompt, completed C2, and passed repeated bounded coding, triage, tools, and
8K/32K context checks. It supersedes NInfer only for the TTFT-first direct
challenger role.

The matching advertised DSpark arm failed CUDA-graph capture on incompatible
target/draft matrix shapes, and the target-only FP8 KV path used default 1.0
scales because calibrated scales were absent. CometKim MTP3 won decode at
228.0 tok/s but failed strict tools 0/3. cdiamond MTP8 is the balanced fresh
full-context fallback at 223.1 ms TTFT and 96.0 tok/s decode. The exact GGUF
incumbent was restored; no promotion or route changed. See the
[dedicated comparison](../qwen38-27b-rtx5090-quant-comparison.md).

The final source refresh added Unsloth Dynamic V3.0 NVFP4 at exact revision
`57926bac`. Its MTP3 arm passed tools 20/20, measured 388.7 ms TTFT and 137.7
tok/s warm decode, retained 127.5 tok/s at a 53,706-token prompt, and left
3,198 MiB free. It is the strongest clean 64K speculative arm in this matrix,
but does not displace the Gittensor TTFT or cdiamond full-context roles.

#### 2026-09-04 — RTX PRO 6000 current-runtime DFlash2 matrix

The Helix same-card report seeded a complete local optimization funnel rather
than a one-number reproduction. After target-only/DFlash2 controls, K12 beat
K4/K8/K16, 1K chunks beat 2K/8K, compile regressed, and ordinary
`extra_buffer` with 96 state slots became the finalist. Its matched sustained-
output result was 764.3 aggregate tok/s at 4K/C8 with 100/100 unique canaries.

Topology was decisive: TP2 reached only 587.9 tok/s and failed strict JSON in
the full preflight and isolated repeat; two synchronized TP1 replicas reached
1,401.8–1,423.4 tok/s at aggregate C16 with 100/100 canaries and near-TP1 per-request
latency. RadixArk K8 traded 46.9% lower median TTFT for lower decode and 6.8%
higher median E2E. kelnei/vLLM MTP2 improved 59.7% over its exact no-spec
control but did not beat SGLang. Unique 82K/C8 remained non-interactive across
all arms. The exact GLM service and authenticated route were restored; no
promotion occurred.

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
- Current PRO 6000 DFlash2 runtime:
  `lmsysorg/sglang@sha256:616a3e97f45191af975896cfa644279096cb31bd408a071c2e99ca7209c3cafe`;
  image-label source revision
  `5f55db35e926d50676f75b812640ea2410b0fe0e`.
- Current PRO 6000 RadixArk FP4-LM-head target:
  `RadixArk/Qwen3.8-27B-NVFP4@319f741cce68d7914884900c138a1fbb70a42f30`;
  measured with target-only, DFlash2 K8, and DFlash2 K12/chunk1K arms.
- Current PRO 6000 kelnei/vLLM target:
  `kelnei/Qwen3.8-27B-NVFP4@29099dc7004e5731173af5c5fb5253466aee219c`;
  vLLM 0.27.1 digest
  `sha256:c2f3b1b964e47809b722b5e75b61b1e7b39a50f70388cf2bf2418f16a9f31da2`.
- DFlash2 runtime image:
  `lmsysorg/sglang:dev@sha256:8acc563e39f4e79118cc3c11cb5a8893ca8da140b2280cdd24a9f3bfe38835a0`;
  image-label source revision
  `f825d729363136a2d4a4b330fa694d0b37a878fa`.
- RTX 5090 GGUF Q4_0 qualification revision:
  `unsloth/Qwen3.8-27B-GGUF@4ca720788d1e01f1bff70c033e0d0028fd02e502`;
  matching Q4_0 MTP head and F16 vision projector from the same revision.
- Conventional Q6_K screen identity:
  `bartowski/Qwen3.8-27B-GGUF@f0eec4a4bb4975114a030d048952d83c0a53c034`.
- llama.cpp qualification runtime: b10548, commit
  `a298422da78eb75e440a7de0ca408af64d323d93`, image digest
  `sha256:cf2e30bc855cf58cdbdc65d05b5b5e02afa95fb788343a5334d704367ac5c9ac`.
- RTX 5090 NInfer NVFP4 qualification artifact:
  `neroued/Qwen3.8-27B-nvfp4-NInfer@204e3d92c30d9d05f3300d2f52e443ad1edf6ddf`,
  `qwen3_8_27b_nvfp4.ninfer`, SHA-256
  `bb3360522a06e136e0367f5703414d26272b7285c8a6ab6194135c17dbd81b32`.
- NInfer qualification runtime revision:
  `Neroued/ninfer@e3aeaf8c0b6f83ae8f051780f0ad0d995d5a7bef`; digest-pinned
  CUDA 13.1.2 development base
  `sha256:b9f64abf7226fdb3463ca202bc99878ec847171e6c5f77bd34c8d1403fbf1eca`.
- RTX 5090 TTFT bakeoff winner:
  `gittensor-model-hub/Qwen3.8-27B-NVFP4-RTX5090@b8ca3826548c9a7735642feb05c3c473f1fede1f`;
  SGLang 0.5.18 image digest
  `sha256:bde16a8447b19e89056b9eea06c72be6c02801dc89d528c9ea90c53368fd74bf`.
- Full-context balanced fallback:
  `cdiamond/Qwen3.8-27B-iMatrix-NVFP4-MTP-GGUF@ac343e8f44caef0896f79d372ecc07ef7ab34ec8`.
- Additional current bakeoff revisions: QUASAR
  `d8e6fbfa3e3a78899b440222b827430045a05b44`, CometKim
  `4f302e0c324771bbd48c419a8d0319e39334ba23`, Red Hat
  `285eba88b22cc7664d2e120eca75ddb7c7dfd6b7`, and Telperion
  `4e81b8843cac2a7f053eda6dfd56d11be3dbafe7`.
- Unsloth Dynamic V3.0 NVFP4 revision:
  `unsloth/Qwen3.8-27B-NVFP4@57926baca9a82b4d6906b43f2750d55315f5b10f`;
  separate 849 MB MTP head; pinned vLLM 0.27.1 image digest
  `sha256:c2f3b1b964e47809b722b5e75b61b1e7b39a50f70388cf2bf2418f16a9f31da2`.

## Tested hardware and topology

The former profile used one TP=1 serve on one of two equal 96 GB RTX PRO 6000
Blackwell Max-Q cards and left the other card empty. Prior tests include one
TP=1 serve per card in split mode and exclusive TP=2 at 393K, 600K, and 1.01M.
The cards are independent PCIe devices; aggregate VRAM is not unified memory,
and the TP=2 runtime could not enable GPU P2P.
The 2026-08-17 and 2026-09-03 challengers used one 32 GB RTX 5090 at TP=1.
They are distinct hardware, engine, quantization, and context lanes and are not
directly comparable to the 96 GB-card 393K production history.
The 2026-09-04 campaign measured both equal 96 GB cards: one-card TP1 arms,
one TP2 service across both cards, and two synchronized independent TP1
replicas. The DP2 measurement is direct-to-replica evidence, not a qualified
load balancer. The existing TP2 baseline was intentionally interrupted and
then restored.

## Engine, quantization, KV, context, and concurrency recipe

The dossier summarizes the configuration families below. For reusable TOML,
container field mapping, and standalone Docker reconstruction guidance, open
the [container configuration page](../configurations.md#qwen38-27b-official-fp8).

### Official vLLM baseline and controls

BF16 multimodal used BF16 weights, FP8 KV, 262,144 native context, and two
sequences. Official FP8 text used FP8 weights and KV, the same native context,
and five sequences. Both used vLLM V1 with chunked prefill, no prefix caching,
and no speculative decoding for the control. MTP=3, prefix caching, and
unquantized KV were isolated one-variable arms.

### Long-context and topology matrix

The extended-context arm kept official FP8 weights, FP8 KV, TP=1, chunked
prefill, no prefix caching, and no MTP, but configured 1,010,000 tokens with
one admitted sequence and the official nested `text_config` override.

The matched TP/MTP matrix fixed one admitted sequence and 4,096 batched tokens
for both checkpoints. Split TP=1 used 393,216 tokens. Exclusive TP=2 used
393,216, 600,000, and 1,010,000 tokens. Every point had an otherwise identical
no-MTP control and `method=mtp,num_speculative_tokens=3` arm.

### Former 96 GB SGLang service

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

### RTX 5090 RadixArk SGLang lane

The retained RTX 5090 RadixArk arm uses SGLang at the same pinned image digest,
TP=1, 131,072 tokens, one running request, FP8 E4M3 KV, FlashInfer attention,
2,048-token chunks, disabled radix cache, one Mamba/GDN state slot, CPU
multimodal feature transport, no MTP, thinking disabled, and explicit ceilings
of eight images and two videos. The managed recipe is
`configs/qwen38-27b-radixark-nvfp4-sglang-rtx5090-128k-mm-recipe.toml`; the
otherwise matched 64K recipe is retained as rollback.

### RTX 5090 GGUF lane

The later GGUF arm uses llama.cpp at 262,144 tokens and concurrency one with
Q4_0 K/V, full layer and projector offload, Flash Attention, batch 2,048,
microbatch 512, Jinja templating, and reasoning disabled. The preferred arm
adds the same-revision Q4_0 MTP head with three maximum draft tokens and Q4_0
draft K/V. The no-spec arm is the matched control. Startup VRAM was 22,254 MiB
without speculation and 25,408 MiB with MTP.

### RTX 5090 NInfer NVFP4 lane

The NInfer lane uses the exact revision and artifact SHA listed above at
252,928 tokens, concurrency one, INT8 KV, and thinking disabled. The selected
arm uses the artifact's integrated MTP proposal path with three draft tokens
and the lm-head draft; the matched control disables speculation. Startup used
28,542 MiB without speculation and 29,834 MiB with MTP3. The managed intake
recipe is `configs/qwen38-27b-ninfer-nvfp4-rtx5090-252k-recipes.toml`.
It builds the exact NInfer source revision on a digest-pinned CUDA base and
verifies the model artifact SHA, but apt package resolution is not immutable;
the recipe is not promotion-grade runtime provenance.

### RTX PRO 6000 current-runtime DFlash2 lane

The campaign used Inferact NVFP4 at revision `6128240e`, current SGLang source
`5f55db35`, 262,144 configured tokens, FP8 target KV, BF16 Mamba state,
thinking disabled, and C8. The first 16-slot target-only profile admitted only
C3 because each request consumed five state slots. The optimization funnel
selected DFlash2 K12, 1K chunks, ordinary `extra_buffer`, and 96 slots after
K4/K8/K12/K16, 1K/2K/8K chunks, compile, lazy state, and 40/96-slot screens.

The topology round compared that exact TP1 arm with a TP2 arm using
conservative WSL2 NCCL controls and two independent TP1 replicas. The RadixArk
target and kelnei/vLLM 0.27.1 MTP2/no-spec arms then used the same headline
workload. Exact recipes are linked from the dated finding.

The public managed registry is
`configs/qwen38-27b-inferact-nvfp4-sglang-pro6000-dflash2-matrix-recipes.toml`.
The graph renderer in the benchmark-doc skill consumes a manifest of exact
native artifacts and reproduces the dashboard and provenance JSON without
plotting dependencies.

### Rejected DFlash2 diagnostic lanes

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

Each subsection answers a different reuse question. Performance numbers are
valid only for the named checkpoint, runtime, hardware, topology, context, and
concurrency; a passing container or health check is not benchmark evidence.

### RTX PRO 6000 current-runtime DFlash2 qualification

**Status:** bounded throughput winner, TP2 rejected, no promotion.

**Measured:** the optimized Inferact K12/chunk1K TP1 arm reached 764.3
aggregate tok/s on the 100-request sustained-output C8 workload. Its
mean/p50/p95/p99 TTFT was 2.416/2.331/5.339/5.513 seconds, decode
201.5/182.5/298.4/306.0 tok/s, TPOT/mean-ITL
5.49/5.43/8.17/8.33 ms/token, and E2E 5.223/4.856/9.579/9.758 seconds.
Two independent TP1 replicas reached 1,401.8–1,423.4 aggregate tok/s at C16 with
100/100 canaries. Matched TP2 reached 587.9 tok/s, 23.1% below TP1, and failed
strict JSON twice.

RadixArk K8 reached 746.7 tok/s while cutting median TTFT 46.9% against
Inferact, but lower decode raised median E2E 6.8%. kelnei/vLLM MTP2 reached
503.4 tok/s versus 315.2 no-spec (+59.7%); active counters reported 40,608 of
43,244 drafted tokens accepted. Unique 82K/C8 completed across the finalists
but median E2E remained 93.7-137.6 seconds.

**Limits:** DP2 has no qualified balancing/failover/client path. TP2 used a
patched conservative WSL2 NCCL path on PCIe without NVLink. Broad quality,
agentic/SWE, multimodal, routed/client, and complete power/energy telemetry
were not run. Natural-completion screens are not numerically comparable with
the forced sustained-output headline workload.

**Evidence:** see the
[dated finding and raw artifact map](../../findings/2026-09-04-qwen38-27b-pro6000-possibility-plan.md).

### RTX 5090 GGUF qualification

On the RTX 5090 GGUF campaign, the no-spec and MTP arms both passed retrieval
at 32K, 131K, 200K, 250K, and a 253,822-actual-token envelope. The latter kept
8,192 output tokens in reserve. MTP measured 104.1 tok/s short decode versus
69.1 for the control, with 0.74 versus 0.91 seconds E2E. The preferred arm also
passed tools 20/20, a schema-valid tool call after 110,875 actual prompt tokens,
agentic 16/18, neutral 101-request endurance 3/3, and images 18/18. Its 91.57%
cumulative MTP acceptance is repetition-biased; the earlier mixed workload was
75.2%. SWE-bench is incomplete because the required isolated-worker controller
transport was unavailable, and native video is unsupported by this recipe.
The subsequent real-client smoke passed OpenClaw and Hermes exact identity,
no-fallback, and shell-tool/result continuation. The visible-answer negative
control also proved that a wrong Hermes provider selector can silently select
a fallback model, so client usage identity is a required gate. The 250K routed
gate remains closed on stale 131,072-token compatibility metadata.
See the [250K qualification](../../findings/2026-08-21-qwen38-27b-gguf-250k-rtx5090.md).

### RTX 5090 NInfer NVFP4 qualification

The matched five-request 4K/C1 run measured 0.430-second median TTFT,
165.9 tok/s median decode, and 0.720-second median E2E with MTP3, versus
0.421 seconds, 75.3 tok/s, and 1.085 seconds without speculation. The immediate
repeat retained the direction at 184.2 versus 75.3 tok/s decode. The harness
reported zero cached prompt tokens in all four capacity artifacts.

Both arms passed smoke, structured JSON, single-request tools, streaming
tools, tool-result continuation, nominal 244,480 retrieval with 201,746
API-reported prompt tokens, and repeated coding/triage/tools 3/3. The selected
arm also passed that prompt with an 8,192-token completion cap in 70.4 seconds.
Each 20-way shared-prefix burst completed only 17/20 because three requests
received explicit `429 server_overloaded` admissions. MTP3 left 2,354 MiB free:
above this experiment's explicit 1 GiB floor, below the ordinary 3 GiB reserve.
See the [NInfer qualification](../../findings/2026-09-03-qwen38-ninfer-nvfp4-rtx5090.md).

### Official BF16/FP8 baseline

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

### Long-context and topology matrix

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

### Speculative depth and SGLang comparisons

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

### Former promotion and video expansion

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

### RTX 5090 RadixArk 128K qualification

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

### Agentic, SWE, and template evidence

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

### DFlash2 and ReplaySSM rejections

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

!!! warning "Promotion remains human-gated"

    These findings preserve reproducible options and explicit rejection
    reasons. They do not authorize a route, client-catalog, or live deployment
    change.

### Retained

- **RTX PRO 6000 aggregate-throughput winner:** two independent current-
  SGLang Inferact NVFP4 plus DFlash2 K12/chunk1K TP1 replicas are retained at
  1,401.8–1,423.4 aggregate tok/s for the matched sustained-output C16 workload. One
  TP1 is the 764.3 tok/s single-card selection; RadixArk K8 is the lower-TTFT
  tradeoff. All remain `no-promotion` pending broader quality and a qualified
  balancing/client path.
- **RTX PRO 6000 alternate runtime:** kelnei/vLLM 0.27.1 MTP2 is retained as a
  verified +59.7% gain over its exact no-spec control, not as the SGLang or
  topology winner.
- **Former 96 GB service:** official FP8 on SGLang with MTP `3/1/4` served
  Primary, general vision, OCR, and video at 393,216 tokens on one card while
  the other card was dormant. Video passed 14/14 direct and 28/28 through the
  live admitted corpus; Hermes and OpenClaw passed without fallback.
- **Managed rollback evidence:** the SGLang profile and former vLLM FP8/BF16
  split remain reproducible, but neither is the immediate text rollback.
  DeepSeek r33 393K now fills that role.
- **RTX 5090 native-video challenger:** RadixArk NVFP4 remains
  `no-promotion`. Its 131,072-token window is a separate lane from the former
  393K service.
- **RTX 5090 direct TTFT challenger:** Gittensor target-only NVFP4/SGLang is
  preferred on the measured TTFT-first direct surface at 50.9 ms median and a
  244,002-token actual prompt. It remains `no-promotion`; compatible
  speculation, calibrated FP8 KV validation, routed/client, broad
  agentic/SWE, multimodal, and endurance gates are open.
- **RTX 5090 direct decode challenger:** NInfer NVFP4 MTP3 retains its
  165.9 tok/s decode and 201,746-token direct evidence. It is no longer the
  TTFT-first selection and retains its admission, reserve, and runtime-image
  limitations.
- **RTX 5090 clean 64K speculative challenger:** Unsloth Dynamic V3.0 MTP3
  passed 20/20 tools and the 53,706-token prompt at 137.7/127.5 tok/s short/
  long decode. It remains bounded to the tested 64K pinned-runtime profile.
- **RTX 5090 broader-capability incumbent:** Unsloth GGUF Q4_0/MTP3 retains
  its deeper 253,822-token, tools 20/20, image/OCR, agentic, endurance, and
  routed-client evidence. The exact serve was restored after NInfer testing.
- **Offline capacity experiments:** TP=2 at 600K and 1.01M remains batch-like,
  not an interactive route recommendation.

### Rejected for the retained 128K route

- **Sharp v22.1:** rejected for this exact RadixArk recipe; stock remains the
  qualified chat template.
- **DFlash2 float32:** exposed too little KV for the 128K service. The best
  BF16/no-radix/single-slot diagnostic is a short-context research lead, not a
  route replacement.
- **MTP3 plus ReplaySSM:** removed the recurrent-state multiplier, but the
  separate 5.73 GB draft still capped KV at 70,231 tokens.

Any distinct short-context deployment needs its own truthful served name,
capability contract, benchmark, restoration proof, and human promotion gate.
External full-context candidates remain dormant until one proves lower local
end-to-end latency without losing quality, tool, multimodal, or restoration
contracts.

### External recipe watch and local follow-up

External results are recipe leads only. They become local evidence only after
an otherwise matched, hardware-specific qualification.

#### MTP=4/5 controls

The 2026-08-15 external refresh added two dormant, official-weight vLLM
qualification recipes. They change only the speculative depth from the
qualified TP=1/393K MTP=3 recipe:
`configs/qwen38-27b-fp8-tp1-393k-mtp4-recipe.toml` and
`configs/qwen38-27b-fp8-tp1-393k-mtp5-recipe.toml`.
A same-product community sweep reports the best decode at depth 5, but its
prompts, concurrency, runtime details, and quality method differ from the local
campaign. The local two-lane and cross-card follow-up found no meaningful E2E
win for depth 4 or 5. MTP=3 remains the selected Qwen depth, and both deeper
recipes remain dormant `no-promotion` controls.

#### SGLang quantized controls

The SGLang cookbook follow-up is complete for no-speculation and in-checkpoint
MTP text serving. Digest-pinned executable recipes retain official FP8 and the
explicitly approved Inferact NVFP4 checkpoint. The NVFP4 snapshot passed full
Safetensors structure and immutable LFS SHA-256 verification before load.
Bounded image/OCR and a repeated six-case corpus pass on BF16 and both
quantized checkpoints when CPU feature transport is forced. Two-image ordering
is covered. The former official-FP8 profile subsequently passed one-video
qualification; NVFP4 video, the 32-image ceiling, concurrency, broad vision
quality, and host-memory-pressure remain open. The default CUDA-IPC path still
fails in this exact WSL2/Docker/runtime combination. The widely shared 200+
tok/s result also adds a DSpark draft and remains an `external-prior`, not a
local result.

#### Excluded or dormant artifacts

The current Gittensor, cdiamond, QUASAR, CometKim, Red Hat, and Telperion
artifacts have now crossed the local startup/performance intake boundary, but
none entered the active production queue. Gittensor target-only is the direct
TTFT challenger; cdiamond MTP8 is the fresh balanced full-context fallback;
CometKim MTP3 is decode-only research because strict tools failed. Inferact
NVFP4 remains a `no-promotion` control. RadixArk NVFP4 is the qualified RTX
5090 native-video challenger, while the official-FP8 SGLang arm remains the
retained former 96 GB-card Qwen profile. Unsloth GGUF remains the broader-
capability RTX 5090 incumbent.

## Failures and gotchas

### Evaluation and benchmark interpretation

- **Sharp artifact scope:** the v22.1 A/B produced complete attempt records, but the generic
  inspector flags unrelated built-in suites as `not_run`, missing aggregate
  chat timing, and thinking control as `requested_unverified` in the quality
  lanes. Those artifacts are bounded diagnostics, not promotion-grade evidence.
- **Shared completion limit:** both stock and Sharp exhausted the 2,048-token completion cap on the same two
  MMLU-Pro items. Sharp also failed the narrow ambiguous-request punctuation
  contract despite safely declining to guess and requesting the missing input.
- **FP8 scaling:** official FP8 startup warned that absent attention q/prob scaling factors
  defaulted to 1.0. No independent quality result proves equivalence to
  unquantized KV.
- **MTP batching:** vLLM warned that 4,096 batched tokens may be suboptimal with MTP=3. A later
  tune must be a matched one-variable A/B. The same warning appeared for
  MTP=4 and MTP=5.
- **Placement variance:** MTP=4/5 short decode differed by roughly 7-8% between equal card roles.
  Cross-card placement is therefore required before attributing a small
  speculative-depth delta to the recipe.
- **MTP timing scope:** the MTP=4/5 deterministic quality artifacts contain complete suite attempts
  but no aggregate chat timing fields; they are bounded behavioral evidence,
  not timing comparisons.
- **Agentic sample size:** the durable separate-worker campaign subsequently completed through the
  approved router alias. It exposed a repeated debug-loop weakness and a wide
  19-57 model-request range across the five resolved SWE tasks; neither the
  16/18 agentic result nor 5/5 fixed SWE sample should be generalized to a full
  benchmark score.

### Serving behavior and long-context topology

- **Vision verbosity:** general-vision output is materially more verbose than OCR. The first routed
  corpus exposed a dropped `chat_template_kwargs` extension; a
  thinking-disabled soft default and same-dialect relay forwarding corrected
  it without raising the final 512-token corpus cap.
- **1M continuation:** the 1M-configured retrieval harness produced at most 825,049 API-reported
  prompt tokens, and each largest run took almost 16 minutes.
- **Near-1M matrix:** the later matrix reached 985,107 actual prompt tokens on both checkpoints in
  TP=2, but TTFT remained 13.0-13.7 minutes. The result supersedes the earlier
  prompt-depth limit, not its offline/batch recommendation.
- **TP=2 transport:** TP=2 lacked P2P and used PyNCCL over the socket-backed local path after vLLM
  disabled custom allreduce.

### RTX 5090 NInfer limits

- **C1 admission:** each simultaneous 20-way shared-prefix tool burst completed
  17/20; three requests received explicit `429 server_overloaded`. The selected
  recipe is qualified only at concurrency one.
- **Reserve policy:** MTP3 left 2,354 MiB free, which passes the preregistered
  1 GiB model-only floor but not the ordinary 3 GiB reserve.
- **Runtime provenance:** NInfer source and the CUDA base are pinned, and the
  model SHA is verified, but Ubuntu package resolution is not immutable.
- **Coverage:** 201,746 API-reported prompt tokens is the deepest measured
  NInfer request; the nominal 244,480 filler estimate is not an actual-token
  claim. Routed/client, thinking-enabled, multimodal, broad agentic/SWE, and
  sustained thermal behavior remain unqualified.

### SGLang, media, and router integration

- **WSL2 media transport:** SGLang's first 393K launch required the explicit longer-context overwrite
  opt-in; its first WSL2 multimodal warmup failed with an invalid CUDA resource
  handle. CPU feature transport now passes bounded image/OCR and one-video
  qualification on the then-current official-FP8 profile, but it is not a blanket
  CUDA-IPC diagnosis and does not qualify 32 images.
- **Runtime identity:** the SGLang image label names `c4271c3`, but its internal build-version string
  names `561c8f3`; the digest is the execution identity and the discrepancy is
  retained.
- **SGLang KV scaling:** both SGLang candidates warned that missing FP8 KV scaling factors defaulted
  to 1.0. The bounded gates passed, but unquantized-KV equivalence is unproven.
- **RadixArk KV scaling:** the RTX 5090 run emitted the same FP8-KV scaling-factor warning.
  Its bounded gates passed, but equivalence to unquantized KV remains unproven.
- **Corrected image fixture:** the first eight-image boundary artifact used expectations that did not exist
  in the hash-pinned images. Inspection and the model output exposed the test
  error; the corrected canonical-label corpus passed 4/4, and both artifacts
  remain published.
- **Multi-video ceiling:** the benchmark harness now records an explicit, bounded multi-video evidence
  ceiling. That flag changes corpus admission only, not engine or router
  policy.
- **Inspector schema gap:** the generic evidence-inspector command does not recognize the multimodal
  benchmark schema even though its complete attempt records report 30/30. A
  product-gap ticket tracks that fail-closed inspection limitation.
- **Responses compatibility:** the first routed Responses probe supplied a chat-only thinking field that
  SGLang rejects on `/v1/responses`. The redundant router soft default was
  removed; the recipe-level default keeps thinking disabled and the Responses
  subset passed. Chat-completions retains the caller control.
- **Aggregate timing scope:** the generic evidence inspector flags absent aggregate chat timing in the
  deterministic agentic artifacts and unrelated `not_run` suites in the
  context-only artifacts. Only complete attempt/target records ground the
  published claims; aggregate quality timing is not claimed.

### RTX 5090 DFlash2 limits

- **Float32 arm:** the RTX 5090 DFlash2 arm requires memory fraction 0.945 and Mamba
  full-memory ratio 10. At 0.895 it admitted zero requests; at 0.945 it booted
  but left only 24,347 target KV tokens and zero reported free GPU memory after
  target graph capture. Configured 262K context must not be reported as
  measured request capacity for this layout.
- **Best diagnostic arm:** BF16 state, one persistent Mamba slot, disabled radix cache, and disabled
  prefill CUDA graphs raised the safe ceiling to 70,262 KV tokens. The tuned
  arm passed a complete 49,549-token functional gate, but the eight-token
  DFlash verifier still reserved 1.12 GB of intermediate BF16 SSM state.
- **Evidence limit:** no controlled throughput, quality, routed, or multimodal claim is retained.
  Logged DFlash acceptance and throughput values remain diagnostic samples.

### RTX PRO 6000 current-runtime DFlash2 limits

- **State-slot admission:** 16 configured state slots admitted only three
  simultaneous requests because each request consumed five. The Mamba96 recipe
  is required for the measured C8 result.
- **Unique long concurrency:** unique-prefix 82K/C8 finished but had 79.1-second
  DFlash2 median E2E and severe per-request spread; it is negative capacity
  evidence, not an interactive C8 contract.
- **TP2 correctness:** full preflight and an isolated repeat emitted duplicate
  structured JSON around a literal closing think delimiter. TP2 is rejected.
- **Isolation coverage:** headline performance passed 100/100 unique request
  canaries. That is response-marker isolation, not broad quality or a full
  mixed-schema concurrency suite.
- **Topology scope:** DP2 directly addressed two replicas; no load balancer,
  health-aware failover, coordinated admission, routed alias, or client path
  was qualified. TP2 used PCIe without NVLink and conservative patched WSL2
  NCCL, so the result does not predict native Linux.
- **Scope:** draft depth, chunk size, compile, Mamba allocation, RadixArk, TP2,
  DP2, and kelnei/vLLM MTP2/no-spec were measured. Broad quality, multimodal,
  agentic/SWE, endurance, routing/clients, and complete power/energy telemetry
  remain open.
- **Restoration:** a preserved-container restart failed a non-idempotent startup
  patch checksum, and an unauthenticated router readmission failed 401. Both are
  retained; exact authenticated mode entry ultimately restored service, route,
  GPU ownership, and clean shared memory.

## Dated run history

- [2026-09-04 RTX PRO 6000 current-runtime DFlash2 matrix](../../findings/2026-09-04-qwen38-27b-pro6000-possibility-plan.md)
- [2026-09-03 RTX 5090 quant/speculation bakeoff](../../findings/2026-09-03-qwen38-27b-rtx5090-quant-bakeoff.md)
- [2026-09-03 RTX 5090 NInfer NVFP4 no-spec/MTP3 qualification](../../findings/2026-09-03-qwen38-ninfer-nvfp4-rtx5090.md)
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
