# Qwen3.8 27B official-FP8 MTP-depth qualification

**Date:** 2026-08-15

**Evidence:** local `functional`, `capacity`, and bounded deterministic
`quality` on two RTX PRO 6000 Blackwell Max-Q cards

**Decision:** retain MTP=3 as `current`; MTP=4 and MTP=5 are
`no-promotion`

**Portable recipe revision:** `d232f422`, based on public source revision
`22b91041`

**Sanitized machine-readable record:**
[summary.json](2026-08-15-qwen38-27b-mtp-depth-qualification-evidence/summary.json)

## Outcome

The community-reported MTP=5 win did not reproduce as a meaningful
end-to-end improvement on the official Qwen FP8 checkpoint at the current
393K serving shape. Both MTP=4 and MTP=5 loaded, passed the full direct
functional gate, passed repeated deterministic intelligence/session/tool
checks, and retrieved correctly at 388,979 actual prompt tokens. Neither
setting failed for capacity or correctness.

The first simultaneous run appeared to favor MTP=4 by about 6.9% in decode,
but swapping the two recipes across the equal cards reversed the result. The
faster decode followed the physical lane. After controlling for card
placement, MTP=5 was only 0.4% faster than MTP=4 on Compute A and 1.3% faster
on Compute B. Median end-to-end time was effectively unchanged and slightly
favored MTP=4 on both cards.

The previously measured MTP=3 production control on Compute B remains the
best result on that lane: 93.6 tok/s decode and 1.295 seconds median E2E,
versus 91.6 tok/s and 1.315 seconds for MTP=5, and 90.4 tok/s and 1.313 seconds
for MTP=4. The result does not justify changing the current recipe.

## Immutable identity and matched settings

- Checkpoint:
  `Qwen/Qwen3.8-27B-FP8@017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`.
- Runtime image:
  `vllm/vllm-openai@sha256:4a2f33a884222f7049b983263ad9976f89452bb81affecf5b67d89ad35c1bc31`.
- vLLM revision: `3a0914114705fa38d4c3171d0746c1a6b6f10209`.
- Hardware: two equal 96 GB NVIDIA RTX PRO 6000 Blackwell Max-Q cards in
  split mode; one TP=1 candidate per card.
- Common recipe: official FP8 weights, FP8 KV, 393,216 tokens, one admitted
  sequence, 4,096 batched tokens, chunked prefill, prefix cache disabled,
  text-only mode, thinking disabled, and GPU memory utilization 0.92.
- Only candidate variable: built-in MTP depth 4 or 5. The historical control
  used the same shape at MTP depth 3.

The official snapshots were already present from the prior artifact-safety
qualification. Cache completeness checks passed and no model file was
downloaded during this campaign. No third-party NVFP4, GGUF, DSpark, or custom
runtime artifact entered the test.

## Functional and bounded-quality gates

Both candidate settings passed:

- short coding and structured JSON;
- a 32K retrieval probe;
- 20/20 tool calls, streaming tool calls, tool-result continuation, and the
  supported Responses subset;
- repeated deterministic intelligence 6/6, session recall 3/3, and tool use
  3/3; and
- reasoning-channel prohibition while thinking was disabled.

The benchmark evidence inspector reports missing aggregate chat timing fields
for the deterministic quality artifacts. Their individual attempts and suite
statuses are complete, but this report uses them only as bounded behavioral
evidence, not as a timing or broad intelligence comparison.

## Matched 4K capacity results

Each repetition used ten requests at concurrency one, 4,096 configured input
tokens, and a 256-token output cap. Values below are the mean of each run's
median. The first placement used three repetitions per setting; the swapped
placement used two.

| Setting | Card role | Repetitions | TTFT | Prefill | Decode | E2E |
|---|---|---:|---:|---:|---:|---:|
| MTP=4 | Compute A | 3 | 0.836 s | 4,319 tok/s | 97.9 tok/s | 1.276 s |
| MTP=5 | Compute A | 2 | 0.844 s | 4,280 tok/s | **98.3 tok/s** | 1.281 s |
| MTP=4 | Compute B | 2 | 0.841 s | 4,288 tok/s | 90.4 tok/s | 1.313 s |
| MTP=5 | Compute B | 3 | 0.845 s | 4,274 tok/s | **91.6 tok/s** | 1.315 s |
| MTP=3 control | Compute B | 1 historical matched run | 0.834 s | 4,326 tok/s | **93.6 tok/s** | **1.295 s** |

The cross-card swap is essential to interpreting the run. Comparing only the
first simultaneous placement would incorrectly attribute the faster card's
roughly 7-8% decode advantage to MTP=4. On a fixed card, MTP=5's decode edge
over MTP=4 is within 1.3%, while E2E differs by less than 0.5% in the opposite
direction. This is not a practical winner.

The MTP=3 row is a dated matched control rather than a fresh bracketed run, so
the report does not claim a precise statistical margin over MTP=4/5. It is
enough to reject a promotion claim: neither deeper setting showed a durable
end-to-end improvement over the current recipe.

## Near-limit context result

Both initial lanes passed the same cold context ladder:

| Setting / placement | Actual prompt | TTFT | Effective prefill | Decode | Result |
|---|---:|---:|---:|---:|---|
| MTP=4 / Compute A | 388,979 | 247.67 s | 1,570.5 tok/s | 28.5 tok/s | pass |
| MTP=5 / Compute B | 388,979 | 250.12 s | 1,555.2 tok/s | 27.8 tok/s | pass |

Each near-limit row is one cold request, and the two rows were on different
physical cards. They prove fit and retrieval at the matched window; they do
not establish a depth-specific long-context speed difference.

## Warnings, failure record, and product gap

- vLLM retained the official-FP8 warning that absent attention q/prob scale
  values default to 1.0.
- vLLM warned that 4,096 scheduled tokens may be suboptimal under speculative
  decoding. Changing that value requires a separate one-variable A/B.
- The first restoration image check contained the required content but ended
  at the 512-token output ceiling. A 1,024-token rerun completed with `stop`
  and passed image understanding plus verbatim OCR.
- The managed serve ledger did not discover recipe-loaded containers and
  incorrectly showed both GPU roles free. A narrow read-only container-label
  inspection was required before returning to managed lifecycle commands.
  The gap is recorded in
  `.tickets/2026-08-15-recipe-loaded-container-discovery.md`.

## Restoration and decision boundary

The candidates were removed through managed recipe lifecycle commands. The
exact starting split was restored from its merged operator recipes:

- official FP8 TP=1/393K/MTP=3 text Primary; and
- official BF16 TP=1/393K/MTP=3 multimodal/OCR with a 32-image ceiling.

The FP8 service passed coding, JSON, tools, streaming tools, tool-result, and
Responses checks. The BF16 service passed coding, JSON, tools, image
understanding, and OCR with the corrected output allowance. Router
expected/observed identities matched and both tiers were readmitted. Shared
memory contained zero files and zero reclaimable bytes.

No Hermes or OpenClaw configuration was changed or exercised in this
campaign. The current MTP=3 split remains selected. MTP=4 and MTP=5 remain
portable, dormant qualification recipes; benchmark publication does not
authorize promotion.

Raw operator artifacts remain private because they include live endpoint and
operator-path data. The sanitized summary retains the measurements, protocol,
artifact filenames, sizes, and SHA-256 digests needed to identify the exact
source records.
