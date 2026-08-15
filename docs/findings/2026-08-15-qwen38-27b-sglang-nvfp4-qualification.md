# Qwen3.8 27B SGLang official-FP8 and NVFP4 qualification

**Date:** 2026-08-15

**Evidence:** local `functional`, `capacity`, and bounded deterministic
`quality` on two RTX PRO 6000 Blackwell Max-Q cards

**Decision:** official-FP8 SGLang is a qualified no-speculation control;
Inferact NVFP4 is a faster text `challenger`; both remain `no-promotion`

**Source base revision:**
`6f3af00659441371b266b27bd910d932f428f277`

**Sanitized machine-readable record:**
[summary.json](2026-08-15-qwen38-27b-sglang-nvfp4-qualification-evidence/summary.json)

## Outcome

The matched two-lane SGLang test succeeded. Official Qwen FP8 and Inferact
NVFP4 each served at TP=1 with a 393,216-token configured window on one RTX
PRO 6000, then swapped physical cards. Both placements passed coding,
structured JSON, 128K retrieval, 20/20 tools, streaming tools, tool-result
recovery, the Responses subset, and zero reasoning leakage with thinking
disabled. Both also passed repeated deterministic intelligence 6/6, session
3/3, and tool 3/3 checks plus one cold 388,979-prompt-token request.

NVFP4 was consistently faster on both cards. Across five 10-request 4K runs
per model, its mean-of-run-medians result was 0.429 seconds TTFT, 8,409
effective prefill tok/s, 57.9 decode tok/s, and 1.244 seconds E2E. Official
FP8 measured 0.554 seconds, 6,512 tok/s, 48.0 tok/s, and 1.451 seconds.
NVFP4 therefore reduced TTFT 22.6% and E2E 14.2% while raising effective
prefill 29.1% and decode 20.6%.

At 388,979 actual prompt tokens the difference narrowed: NVFP4 returned its
first token in 248.75 seconds at 1,564 effective prefill tok/s, versus 258.13
seconds and 1,507 tok/s for official FP8. Both returned the correct retrieval
answer. These are one cold request per model, not p50/p95 statistics.

The result does not displace the current vLLM MTP=3 text service. Its prior
matched 4K result on the production split was 93.6 decode tok/s, substantially
above the 57.9 tok/s NVFP4 SGLang control. This campaign isolated quantization
under SGLang without speculation; it did not test SGLang MTP/DSpark or prove
broad quality equivalence.

## Immutable identity and translated recipe

- Official checkpoint:
  `Qwen/Qwen3.8-27B-FP8@017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`.
- NVFP4 checkpoint:
  `Inferact/Qwen3.8-27B-NVFP4@6128240ebaf4eaa7bad2b3d1c72c37d677c5f462`.
- Runtime image:
  `lmsysorg/sglang@sha256:506525a5907ea22c9d445afb7c03603959b912de034d86915cf17da814f1a124`.
- Image-label SGLang revision:
  `c4271c3fe1262fc2adbd162c33b25de5255251c5`.
- Upstream cookbook/config revision:
  `dd458f3212dd4ddf0e1a7907bbf539b660e70d21`.
- Hardware: two equal 96 GB NVIDIA RTX PRO 6000 Blackwell Max-Q cards in
  split mode, one independent TP=1 candidate per card, followed by a card
  swap.

The common recipe used 393,216 tokens, one running request, FP8 E4M3 KV,
FlashInfer attention, 2,048-token prefill chunks, memory fraction 0.85,
prefix caching disabled, one GDN state slot, Qwen reasoning/tool parsers,
thinking disabled by default, and no speculative decoding. Both candidates
used text-only mode for a matched test.

Portable recipes:

- [official FP8 SGLang control](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-27b-official-fp8-sglang-tp1-393k-control-recipe.toml)
- [Inferact NVFP4 SGLang control](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-27b-inferact-nvfp4-sglang-tp1-393k-control-recipe.toml)

The translation starts from SGLang's
[Qwen3.8 27B cookbook](https://github.com/sgl-project/sglang/blob/dd458f3212dd4ddf0e1a7907bbf539b660e70d21/docs/cookbook/autoregressive/Qwen/Qwen3.8-27B.mdx)
and
[RTX PRO 6000 configuration](https://github.com/sgl-project/sglang/blob/dd458f3212dd4ddf0e1a7907bbf539b660e70d21/docs/src/snippets/configs/Qwen/qwen3.8-27b.jsx).
The local recipes add the explicit 393K/c1/no-spec control shape and omit
remote-code permission.

## NVFP4 artifact-safety intake

The third-party checkpoint was not loaded until its exact pinned snapshot
passed a file-level intake:

- only documentation, tokenizer/config/template data, and seven Safetensors
  shards were present;
- no tracked Python, native executable, `auto_map`, pickle, PyTorch `.bin`,
  or remote-code hook was present;
- all seven Safetensors headers, tensor ranges, and declared file endings were
  valid;
- all 2,111 tensors were accounted for; and
- independently computed local SHA-256 values matched all seven immutable
  Hugging Face LFS object identities.

The repository's `crc32.txt` was stale and its Safetensors MD5 list was empty,
so neither was treated as authoritative. The immutable revision, LFS object
identities, and local SHA-256 match are the provenance anchors. The sanitized
summary records every shard digest.

Anvil could prove an exact complete cached snapshot but could not independently
hash cached files against Hub object identities. One narrow read-only,
network-disabled container mounted the model cache read-only to close that
specific gap. The missing managed surface is recorded in
[the cache-verification ticket](https://github.com/fakoli/anvil-serving/blob/main/.tickets/2026-08-15-model-cache-snapshot-hash-verification.md).

## Startup fit and engine accounting

| Candidate | Cached snapshot | Loaded weight memory | Reported KV capacity | Result |
|---|---:|---:|---:|---|
| Official FP8 | 30.89 GB | 28.47 GB | 1,665,740 tokens | ready |
| Inferact NVFP4 | 26.40 GB | 24.21 GB | 1,805,068 tokens | ready |

The observed NVFP4 weight-memory saving was 4.26 GB, smaller than the upstream
RadixArk estimate because this is a different ModelOpt conversion. It still
provided about 139K more reported KV-cache tokens at the same static memory
fraction. Both candidates retained roughly 11 GB of SGLang-reported available
memory after cache allocation.

## Matched 4K performance

Each repetition sent ten requests at concurrency one with 4,096 configured
input tokens and a 256-token output cap. The first placement used three
repetitions per model; the swapped placement used two. Values are the mean of
each run's median.

| Candidate | Card placement | Runs | TTFT | Effective prefill | Decode | E2E | Aggregate output |
|---|---|---:|---:|---:|---:|---:|---:|
| Official FP8 | first | 3 | 0.553 s | 6,529 tok/s | 48.1 tok/s | 1.448 s | 30.3 tok/s |
| Official FP8 | swapped | 2 | 0.556 s | 6,488 tok/s | 47.9 tok/s | 1.455 s | 30.1 tok/s |
| Inferact NVFP4 | first | 3 | 0.430 s | 8,396 tok/s | 57.8 tok/s | 1.248 s | 38.3 tok/s |
| Inferact NVFP4 | swapped | 2 | 0.428 s | 8,429 tok/s | 58.1 tok/s | 1.239 s | 38.6 tok/s |

The card swap changed each model by less than 0.5% on decode and did not
reverse any conclusion. The speed advantage therefore followed the
quantization/model, not a physical GPU lane.

## Functional, quality, and long-context gates

Both models passed the full functional gate on both placements:

- short coding and structured JSON;
- a 131,072-token retrieval probe;
- 20/20 shared-prefix tool calls;
- streaming tool calls and tool-result continuation;
- the supported Responses subset; and
- zero reasoning characters while thinking was disabled.

Both passed the same repeated deterministic quality slice with no failures:
intelligence 6/6, session 3/3, and tools 3/3. These are bounded API behavior
checks, not SWE-bench or a broad intelligence leaderboard.

The evidence inspector reports validation warnings for fields outside the
selected workloads: missing aggregate chat timing on deterministic agentic
artifacts, and empty `not_run` intelligence/session/tool sections on
context-only artifacts. The individual agentic attempts and context targets
are complete and carry explicit pass statuses; this report uses only those
completed records. The generic validation mismatch is tracked in
[the context-only evidence ticket](https://github.com/fakoli/anvil-serving/blob/main/.tickets/2026-08-15-context-only-quality-evidence-validation.md).

| Candidate | Actual prompt | TTFT | Effective prefill | Decode | Result |
|---|---:|---:|---:|---:|---|
| Official FP8 | 388,979 | 258.13 s | 1,507 tok/s | 33.2 tok/s | pass |
| Inferact NVFP4 | 388,979 | 248.75 s | 1,564 tok/s | 37.0 tok/s | pass |

## Retained failures and caveats

- The first launch failed closed because 393,216 exceeds the checkpoint's
  advertised 262,144-token context. The final recipes use SGLang's explicit
  longer-context overwrite opt-in; the 388,979-token requests are the local
  evidence for that override.
- SGLang's default multimodal CUDA-IPC warmup failed on WSL2 with
  `CUDA error: invalid resource handle`. The matched recipes use
  `--language-only`. This campaign does not qualify SGLang image/video/OCR;
  a separate CPU-feature-transport recipe is required.
- The first otherwise-passing functional run exposed reasoning text through
  the Responses adapter even when each request disabled thinking. Adding a
  server-side thinking-disabled default removed the leakage; the final gate
  recorded zero reasoning characters on both APIs and both placements.
- Both lanes warned that FP8 KV scaling factors were absent and defaulted to
  1.0. The bounded quality and context checks passed, but they do not prove
  equivalence to unquantized KV.
- The immutable image label identifies engine revision `c4271c3`, while an
  internal build-version string names `561c8f3`. The image digest is the
  execution identity; the revision discrepancy remains a provenance caveat.
- The routed restore check initially combined explicit chat-template kwargs
  with `/v1/responses`, which that route correctly rejects as unsupported.
  Chat/tools passed, and a separate Responses request using the promoted
  server default passed with zero reasoning characters.
- `eval benchmark evidence show` flags unselected fields in context-only and
  deterministic-quality artifacts. Claims here are limited to the complete
  target and attempt records; aggregate quality timing is not claimed.

## Restoration and decision boundary

Both SGLang candidates were removed through managed recipe lifecycle commands.
The exact pre-test split was restored on its original cards:

- official FP8 TP=1/393K/MTP=3 text Primary; and
- official BF16 TP=1/393K/MTP=3 multimodal/OCR with the 32-image ceiling.

The restored FP8 direct gate passed coding, JSON, 20/20 tools, streaming tools,
tool-result recovery, and Responses. BF16 passed the same text contract plus
image understanding and verbatim OCR. Router expected and observed identities
matched; Primary, general vision, and explicit OCR routed checks passed; all
tiers were readmitted. Shared memory reported zero files and zero reclaimable
bytes.

No Hermes or OpenClaw configuration was changed or exercised. No route,
promotion, context limit, or current recommendation changed. Official FP8
SGLang and Inferact NVFP4 remain portable text qualification recipes; using
either as a live service requires a separate human promotion gate.

Raw operator artifacts remain private because they include live endpoints,
GPU UUIDs, and operator paths. The public summary retains exact identities,
protocols, metrics, safety digests, failures, and restoration outcomes without
publishing private topology.
