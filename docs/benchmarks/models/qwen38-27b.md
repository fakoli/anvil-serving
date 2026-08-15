# Qwen3.8 27B

## Current status and review date

Human-approved `current` split: official FP8 text Primary and official BF16
multimodal/OCR, both TP=1 at 393,216 tokens with MTP=3. Review date:
2026-08-15. The current review includes the local MTP=4/5 depth qualification.

## Immutable identity

- Official BF16 revision:
  `Qwen/Qwen3.8-27B@1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`.
- Official FP8 revision:
  `Qwen/Qwen3.8-27B-FP8@017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`.
- Runtime image digest:
  `sha256:4a2f33a884222f7049b983263ad9976f89452bb81affecf5b67d89ad35c1bc31`;
  vLLM revision `3a0914114705fa38d4c3171d0746c1a6b6f10209`.

## Tested hardware and topology

One TP=1 serve on each of two equal 96 GB RTX PRO 6000 Blackwell Max-Q cards
in split mode, plus exclusive TP=2 on both cards at 393K, 600K, and 1.01M.
The cards are independent PCIe devices; aggregate VRAM is not unified memory,
and the TP=2 runtime could not enable GPU P2P.

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

The promoted split selects the 393,216-token TP=1 MTP=3 arm for both models.
Official FP8 is text-only. BF16 admits text, image, and video, including up to
32 images in one request. Both default to thinking disabled while retaining a
caller override on chat-completions requests.

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

Evidence classes are `functional`, `capacity`, bounded `quality`, and
multimodal. The deterministic API checks are not SWE-bench evidence.

## Decision and promotion state

Human-approved `current` split. Official FP8 plus MTP=3 is the text Primary;
BF16 plus MTP=3 backs explicit general-vision and OCR capabilities. Both use
the same 393,216-token limit. The final routed media corpus passed 30/30, the
32-image request passed 1/1, and Hermes/OpenClaw client-path checks completed
without fallback. TP=2 at 600K and 1.01M remains an offline/batch experiment.

## External recipe watch and local follow-up

The 2026-08-15 external refresh added two dormant, official-weight vLLM
qualification recipes. They change only the speculative depth from the
qualified TP=1/393K MTP=3 recipe:
`configs/qwen38-27b-fp8-tp1-393k-mtp4-recipe.toml` and
`configs/qwen38-27b-fp8-tp1-393k-mtp5-recipe.toml`.
A same-product community sweep reports the best decode at depth 5, but its
prompts, concurrency, runtime details, and quality method differ from the local
campaign. The local two-lane and cross-card follow-up found no meaningful E2E
win for depth 4 or 5, so MTP=3 remains current and both deeper recipes remain
dormant `no-promotion` controls.

SGLang's day-zero cookbook is the second-priority challenger. Its RTX PRO 6000
cells use official BF16 or FP8 weights, FlashInfer attention,
`--mem-fraction-static 0.85`, and 2,048-token prefill chunks, with separate GDN
state-cache sizing. The widely shared 200+ tok/s result changes to third-party
NVFP4 weights and a separate DSpark draft, so it is not evidence for the
official checkpoints. No executable SGLang recipe is recorded until its image
is digest-pinned and mapped to an exact source revision.

All NVFP4, GGUF, AutoRound, and custom `.ninfer` artifacts remain excluded from
the active queue under the official-only checkpoint policy. These statements
are `external-prior`, not local measurements.

## Failures and gotchas

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
- The durable separate-worker context/agentic/SWE campaign was not submitted
  because no candidate router alias was approved. Direct API checks are not
  SWE-bench evidence.
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

## Dated run history

- [2026-08-15 official-FP8 MTP-depth qualification](../../findings/2026-08-15-qwen38-27b-mtp-depth-qualification.md)
- [2026-08-15 external recipe refresh](../../findings/2026-08-15-qwen38-27b-external-recipe-refresh.md)
- [2026-08-14 split promotion](../../findings/2026-08-14-qwen38-27b-split-promotion.md)
- [2026-08-14 TP/MTP/context matrix](../../findings/2026-08-14-qwen38-27b-tp-mtp-context-matrix.md)
- [2026-08-14 official FP8 1M-context continuation](../../findings/2026-08-14-qwen38-27b-1m-context.md)
- [2026-08-14 official BF16/FP8 qualification](../../findings/2026-08-14-qwen38-27b-official-qualification.md)
