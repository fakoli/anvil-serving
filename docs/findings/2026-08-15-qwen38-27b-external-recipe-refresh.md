# Qwen3.8 27B external recipe refresh

**Date:** 2026-08-15

**Campaign type:** discovery and decision support only

**Target:** one or two RTX PRO 6000 Blackwell Max-Q cards (`sm_120`), with the
current split deployment as the control

**Machine-readable record:**
[candidates.json](2026-08-15-qwen38-27b-external-recipe-refresh-evidence/candidates.json)

## Outcome

Two current recipe ideas merit local work, but neither changes the promoted
configuration today:

1. **Benchmark MTP=4 and MTP=5 on the official FP8 TP=1 lane.** A fresh
   same-card community sweep reports its best decode result at MTP=5. This is
   the smallest and most decision-relevant experiment because the qualified
   Anvil recipe already has an otherwise-matched MTP=3 control. Dormant,
   digest-pinned MTP=4 and MTP=5 recipes are now recorded under `configs/`.
2. **Run an SGLang compatibility/performance spike with official FP8 and BF16
   weights.** SGLang's day-zero cookbook includes RTX PRO 6000 cells and useful
   SM120-specific GDN-state and chunked-prefill controls. The published
   200+ tok/s headline, however, uses third-party NVFP4 and DSpark artifacts,
   so it is not evidence for the official checkpoints or our deployment.

The official Qwen Hugging Face organization still exposes the same BF16 and
FP8 revisions already pinned locally. Its API listings contain Safetensors
weight shards and no tracked Python files. That reduces the executable-code
surface but is not a blanket safety guarantee. No new official NVFP4 checkpoint
was found; all NVFP4, GGUF, custom `.ninfer`, and AutoRound candidates remain
outside the official-only policy.

No model or container was downloaded, no serve was loaded or stopped, no GPU
ownership or route changed, and no benchmark ran during this refresh.

## Shortlist

| Priority | Candidate | What changes | Evidence | Decision action |
|---:|---|---|---|---|
| 1 | vLLM official FP8 MTP depth | MTP=3 to MTP=4 and MTP=5; all other qualified TP=1/393K settings fixed | Same-model, same-GPU community self-report plus current official vLLM recipe | **Benchmark A/B** |
| 2 | SGLang official FP8 | Engine/runtime; start without speculation, then add in-checkpoint EAGLE/MTP | SGLang commit-pinned cookbook with an RTX PRO 6000 cell | **Compatibility spike**, then matched benchmark |
| 3 | SGLang official BF16 multimodal | Engine/runtime and GDN cache sizing; retain image/video gates | Same cookbook; no local media result | **Compatibility and media-quality spike** |
| 4 | vLLM TP=2 vision encoder data parallelism | Add `--mm-encoder-tp-mode data` only in a deliberate TP=2 BF16 experiment | Official vLLM recipe; no RTX PRO measurement | **Watch**, low priority |

“Candidate” means test-next or watch-next. It does not mean recommended,
qualified, routed, or promoted.

## 1. MTP=4 and MTP=5 are the best immediate test

The official vLLM recipe still recommends the model's in-checkpoint MTP head at
three speculative tokens. A 2026-08-14
[RTX PRO 6000 community sweep](https://www.reddit.com/r/LocalLLaMA/comments/1vodweq/qwen_36_vs_38_mtp_sweep_comparison_27bfp8/)
instead varied the depth while keeping official FP8 weights, FP8 KV, a 262,144
window, `--max-num-seqs 4`, `--gpu-memory-utilization 0.92`, and FlashInfer.
The author reported:

| MTP depth | Decode | TTFT | Acceptance |
|---:|---:|---:|---:|
| 2 | 90.8 tok/s | 95.5 ms | 81.1% |
| 3 | 98.3 tok/s | 97.6 ms | 65.0% |
| 4 | 107.1 tok/s | 100.1 ms | 61.2% |
| 5 | **115.0 tok/s** | 102.7 ms | 56.3% |
| 6 | 109.5 tok/s | 105.5 ms | 48.8% |

These are unreplicated community measurements. The post's four-prompt quality
check is not an independent qualification, and its TTFT is not comparable with
our 4K-prompt test. Even so, the curve is a good hypothesis: depth 5 may trade
lower acceptance for more accepted tokens per verification step, while depth
6 has already passed the apparent optimum.

The new `configs/qwen38-27b-fp8-tp1-393k-mtp4-recipe.toml` and
`configs/qwen38-27b-fp8-tp1-393k-mtp5-recipe.toml` recipes retain our official
FP8 revision, digest-pinned vLLM image, TP=1, 393,216 context, FP8 KV, maxseq1,
batch 4,096, cache policy, and text-only mode. A later test
should bracket them with the existing MTP=3 recipe and record acceptance,
TTFT, prefill, decode, VRAM, visible-answer quality, tool recovery, and
post-stress health. A concurrency follow-up belongs after the c1 A/B.

## 2. SGLang is a real challenger, but the headline is not our recipe

SGLang added a commit-pinned
[Qwen3.8-27B cookbook](https://github.com/sgl-project/sglang/blob/70e291b70f5a2833291fff517a00b2f3ff559463/docs/cookbook/autoregressive/Qwen/Qwen3.8-27B.mdx)
on 2026-08-14. Its
[configuration source](https://github.com/sgl-project/sglang/blob/70e291b70f5a2833291fff517a00b2f3ff559463/docs/src/snippets/configs/Qwen/qwen3.8-27b.jsx)
marks RTX PRO 6000 BF16 and official-FP8 cells verified and gives both the same
base settings:

- single GPU, `--mem-fraction-static 0.85`;
- FlashInfer attention for SM120;
- `--chunked-prefill-size 2048` to reduce decode stalls behind long prefill
  chunks;
- Qwen3 reasoning and Qwen3-Coder tool parsing;
- no speculative decode in the balanced control.

The important model-specific difference from vLLM is GDN recurrent-state
allocation. SGLang exposes `--mamba-full-memory-ratio` or an explicit
`--max-mamba-cache-size`, plus cache strategies such as
`extra_buffer_lazy`. Those values must be sized to the target context and
concurrency; copying a default can silently reduce admission. The cookbook's
in-checkpoint speculative arm uses `EAGLE`, three steps, top-k 1, and four draft
tokens. That is the SGLang expression of the model's built-in MTP head, not a
separate draft checkpoint.

The [SGLang X announcement](https://x.com/sgl_project/status/2088281320422322413)
and corresponding
[Reddit post](https://www.reddit.com/r/LocalLLaMA/comments/1voearc/sglang_support_for_qwen3827b_200_toks_on_5090_38/)
advertise more than 200 tok/s on RTX 5090 and RTX PRO 6000. The fast cells use
`RadixArk/Qwen3.8-27B-NVFP4` and a separate
`RadixArk/Qwen3.8-27B-DSpark` draft, not the official Qwen checkpoints. The
number is therefore an external upper bound for the engine, not a comparison
with our 93.6 tok/s official-FP8 MTP=3 result.

Before an executable Anvil recipe is added, the SGLang image must be digest
pinned and its source revision mapped to that digest. The first local lane
should use the official FP8 revision, no speculative decoding, one sequence,
and the current 393K control shape. Only after functional/tool/context gates
pass should it add the built-in EAGLE/MTP arm. BF16 needs the full existing
image/video/OCR corpus and 32-image boundary, not just a text health check.

## 3. Official vLLM recipe changes that do not need a campaign

The current
[official vLLM recipe](https://github.com/vllm-project/recipes/blob/002576894984c12e203bb25421635fbb3f408e9d/models/Qwen/Qwen3.8-27B.yaml)
was added and revised on 2026-08-14. It confirms the facts already translated
into the Anvil recipes: 262,144 native context, the nested `text_config`
override for 1M, `--language-model-only` for the FP8 text lane, Qwen3/Qwen3-Coder
parsers, FP8 KV, and explicit MTP depth. Its only untested setting of possible
interest is `--mm-encoder-tp-mode data`, which avoids tensor-parallel
communication for the small vision encoder. That flag has no effect on the
current TP=1 BF16 vision lane and is only worth measuring if we intentionally
revisit TP=2 multimodal serving.

The recipe's only verified hardware is GB300. Its NVFP4 entry points to
Inferact, explicitly limits supported hardware to B200/B300/GB200/GB300, and
is not an official Qwen artifact. It is excluded here. The recipe also notes
that speculative GDN correctness needs vLLM fixes
[#51674](https://github.com/vllm-project/vllm/pull/51674) and
[#51812](https://github.com/vllm-project/vllm/pull/51812). Both are merged,
but the recipe says no released tag carried them when it was published; our
qualified digest already uses the newer pinned revision that passed MTP=3.

## 4. Community leads retained as watch or reject

- The [Hermes release megathread](https://www.reddit.com/r/hermesagent/comments/1voapha/qwen_38_release_megathread/)
  reports roughly 70 tok/s for a third-party Q5 GGUF with MTP=3 on an RTX 5090.
  It is slower and less provenance-aligned than our official FP8 lane, but its
  long-session reports reinforce testing reasoning-budget and raw-XML tool-call
  recovery.
- A [llama.cpp KV-quant context study](https://www.reddit.com/r/LocalLLaMA/comments/1vp4cey/benchmark_context_length_vs_kv_cache_quants_5090/)
  reports about 105K to 170K stable context across Q8/Q5/Q4 KV on a 5090. Our
  official FP8 lane already qualifies 393K on one card, so this is not a useful
  capacity direction.
- [NInfer's day-zero post](https://www.reddit.com/r/LocalLLaMA/comments/1vod417/ninfer_day0_support_for_qwen38_27b_200_toks/)
  claims about 200 tok/s with a custom 18.2 GB `.ninfer` artifact, INT8 KV, and
  MTP=3. The proprietary format, third-party quantization, uncertain
  multimodal path, and absent independent quality evidence make it watch-only.
- Third-party NVFP4, DSpark, GGUF, and AutoRound releases are excluded from the
  active queue. Their performance reports can shape tests, but not artifacts
  or promotion decisions under the official-only policy.

Several Reddit discussions mention long-session overthinking, raw XML tool
calls, premature stops, and template-dependent regressions. These are useful
failure-corpus additions. They are not evidence that a particular runtime or
quantization causes the behavior.

## Decision sequence for a later qualification

1. Run the pinned vLLM FP8 MTP=3/4/5 c1 A/B at 393K with matched 4K and
   repeated-quality workloads. Stop if MTP=4 fails quality or stability; do not
   assume MTP=5 is better from throughput alone.
2. If depth 4 or 5 wins, repeat the winning arm at practical concurrency and
   at a long prompt. Record acceptance and KV-capacity cost as well as decode.
3. Separately pin an SGLang image digest and qualify official FP8 with no
   speculation. Treat parser, thinking-control, tool recovery, long context,
   WSL2 stability, and GDN cache allocation as compatibility gates.
4. Add SGLang's in-checkpoint EAGLE/MTP only against that same-image control.
5. Test official BF16 media only if the FP8 engine lane is stable. Preserve the
   existing 30-case media corpus, 32-image boundary, and matched 393K window.
6. Keep `--mm-encoder-tp-mode data` as a TP=2-only optional arm. The current
   two-independent-model split remains the everyday topology unless a complete
   result justifies another human-gated decision.

## Scope and evidence boundaries

- Official docs, commit-pinned source, Hugging Face repository metadata,
  Reddit, X, and community release discussions were reviewed. Social posts
  were used for discovery or self-reported measurements, never as local proof.
- External values are not directly comparable with Anvil results when prompts,
  output lengths, concurrency, runtime revisions, cache state, and measurement
  definitions differ.
- No third-party checkpoint is approved for download. The only executable
  candidates added here reuse the exact official FP8 snapshot and runtime
  digest already qualified locally.
- This finding does not authorize loading, benchmarking, routing, deployment,
  or promotion. All such actions remain managed and separately human-gated.
