# RTX PRO 6000 Heavy evaluation protocol v2

**Point-in-time record, 2026-07-12.** This round repaired the cross-model
reasoning evaluation protocol, reran Mistral Small 4 and Nemotron 3 Super on
the same RTX PRO 6000, and tested Poolside Laguna XS 2.1 NVFP4 through both
vLLM and SGLang. Nemotron 3 Super remains the selected resident Heavy
experiment. No router profile or production tier was promoted.

Raw protocol-v2 artifacts are under
[2026-07-12-rtx-pro-6000-heavy-eval-v2-evidence/](2026-07-12-rtx-pro-6000-heavy-eval-v2-evidence/).

## What changed in the evaluation protocol

The earlier fixed-total-token comparison could confuse a model that spent its
budget in a reasoning channel with a model that answered incorrectly. Protocol
v2 records an equal visible-answer allocation plus explicit reasoning
headroom, selects the model family's real control (`reasoning_effort` or
`enable_thinking`), repeats each item three times, and retains the full visible
answer, reasoning metadata, finish reason, per-attempt budget, and failure
class. A question passes when at least two of three attempts pass. The API
still receives one combined completion cap; the two allocations are evidence
intent, not a hard server-side partition.

The deterministic multiple-choice validator now accepts whitespace-tolerant
final markers such as `FINAL = D`. This fixed a real false negative in the
first diagnostic ARC run without introducing a model judge. The published runs
used the preserved unanchored validator specs. An independent offline check
confirmed that every passing output ended with its expected marker, so the
later anchored-validator repair does not change these scores. The exact
executed specs and their hashes are bound in
[run-lineage.json](2026-07-12-rtx-pro-6000-heavy-eval-v2-evidence/run-lineage.json).

## Common local topology

| Field | Tested value |
|---|---|
| Host / GPU | Fakoli Dark; one RTX PRO 6000 Blackwell 96 GB, sm_120 |
| Resident engines | vLLM nightly `0.23.1rc1.dev531+ga65f93fb2` |
| Served context / admission | 131,072 tokens; five sequences |
| Prefix cache | disabled for independent-prompt comparison |
| Repetitions / stable threshold | three per item; pass rate >= 0.66 |
| Visible answer allocation | 256 tokens |

The prior Heavy challenger run already established that both models complete
5/5 independent requests at concurrency five and pass their model-appropriate
preflight. This round concentrates on repaired quality evidence rather than
repeating that capacity result.

## ARC-Challenge repeated sanity

This is a pinned five-row ARC-Challenge slice. It is a small reasoning-budget
sanity check, not a capability leaderboard.

| Model / control | Reasoning headroom | Stable items | Passing attempts | Wall time |
|---|---:|---:|---:|---:|
| Mistral Small 4, effort `none` | 0 | 2/5 | 6/15 | 27.91 s |
| Mistral Small 4, effort `high` | 1,024 | 4/5 | 9/15 | 95.40 s |
| Mistral Small 4, effort `high` | 2,048 | 5/5 | 15/15 | 97.46 s |
| Nemotron 3 Super, thinking off | 0 | 2/5 | 7/15 | 39.68 s |
| Nemotron 3 Super, thinking on | 1,024 | 5/5 | 15/15 | 69.91 s |

Both models need reasoning headroom for this slice. Nemotron reached 15/15
with half the headroom Mistral needed and completed the comparable perfect run
27.55 seconds sooner.

## MMLU-Pro repeated multidomain sanity

The second benchmark is a pinned ten-row validation slice from
[`TIGER-Lab/MMLU-Pro`](https://huggingface.co/datasets/TIGER-Lab/MMLU-Pro),
covering ten categories and ten answer choices. It is deliberately short and
must not be reported as a full MMLU-Pro score.

| Model / control | Reasoning headroom | Stable items | Passing attempts | Median attempt | Wall time |
|---|---:|---:|---:|---:|---:|
| Mistral Small 4, effort `high` | 2,048 | 5/10 | 14/30 | 10.41 s | 384.74 s |
| Nemotron 3 Super, thinking on | 1,024 | 8/10 | 23/30 | 7.27 s | 260.91 s |
| Nemotron 3 Super, thinking on | 2,048 | 8/10 | 23/30 | 6.67 s | 317.88 s |

Nemotron's 1,024-headroom recipe is the useful operating point. Doubling its
headroom did not improve a single item or attempt and added 56.97 seconds of
wall time. Its two unstable items were computer science and engineering; all
six attempts exhausted the reasoning budget without a visible answer. This is
evidence for a larger budget follow-up on those tasks, not evidence that the
answers would necessarily be correct.

## Laguna XS 2.1 NVFP4 engine A/B

Laguna was selected as a current official challenger because its model card
advertises 33B total / 3B active parameters, a native 262,144-token window,
NVFP4 weights, and launch-day vLLM and SGLang support. The checkpoint revision
was pinned to `07133fb3df1cc3111478e24ee71a823a598c8c2f`.

It did **not** produce trustworthy output on this RTX PRO 6000. The healthy-run
quality claims, the 262K skip-layer stall, and the `trtllm_mha` rejection are
operator-observed but incomplete: their response bodies or full logs were lost
when containers were recreated. They are not reproducible quality scores.
Retained logs independently substantiate only the 131K startup stall and the
forced-runner assertion:

- vLLM 0.23 nightly loaded at 262K with FP8 KV and reported capacity for 11.81
  full windows, but preflight exposed corrupted text and 0/20 tool calls.
- Disabling FP8 KV on all 40 layers changed vLLM to FlashAttention. The retained
  131K log stalled during cache profiling; the same 262K behavior is an
  operator-observed incomplete result.
- Poolside's Laguna-specific SGLang CUDA 13 image loaded the model and
  initialized `SWARadixCache`, but both the default and explicit checkpoint
  chat templates produced repetitive/off-topic output; preflight passed only
  short coding and structured JSON, while the 131K needle was empty and tool
  fan-out was 0/20.
- SGLang's documented Blackwell `trtllm_mha` attention backend rejected sm_120
  because that build supports it only on sm_100. Forcing the TRT-LLM MoE runner
  then crashed during warmup on `routing_method_type is not None`.

The machine-readable failure summary is
[laguna-xs-21-nvfp4-sm120.failure.json](2026-07-12-rtx-pro-6000-heavy-eval-v2-evidence/laguna-xs-21-nvfp4-sm120.failure.json).
See also the [reproduction record](2026-07-12-rtx-pro-6000-heavy-eval-v2-evidence/laguna-reproduction.md),
[bounded retained log excerpts](2026-07-12-rtx-pro-6000-heavy-eval-v2-evidence/laguna-runtime-excerpts.log),
and [image digests](2026-07-12-rtx-pro-6000-heavy-eval-v2-evidence/image-digests.json).
This rejects the tested recipes, not the model on other Blackwell products.
RadixAttention successfully initialized but cannot repair incorrect model
execution; it only reuses compatible prefix state after correctness exists.

## Source age and decision use

| Source | Observed / source date | Age class | Evidence | RTX PRO 6000 relevance | Decision impact |
|---|---|---|---|---|---|
| Poolside Laguna NVFP4 model card | observed 2026-07-12; model updated 2026-07-09 | current | official primary | Blackwell NVFP4 recipe, not workstation-specific | selected candidate and controls |
| vLLM Laguna recipe | observed 2026-07-12; updated 2026-07-02 | current | official primary | says NVFP4 is Blackwell-only and vLLM >=0.22 | established expected vLLM path |
| SGLang Laguna cookbook | observed 2026-07-12 | current | official primary | verified H200/B300/GB300, not sm_120 | supplied engine/image and exposed hardware gap |
| ARC-Challenge dataset | 2023-12-21 revision | historical stable dataset | official dataset | hardware-independent quality prior | supplied the five-item local sanity slice |
| MMLU-Pro dataset | observed 2026-07-12; updated 2026-05-02 | aging | official dataset | hardware-independent quality prior | supplied the ten-item local sanity slice |

URLs and revisions are preserved in
[source-registry.json](2026-07-12-rtx-pro-6000-heavy-eval-v2-evidence/source-registry.json).

## Decision boundary

Nemotron 3 Super is the best currently measured Heavy experiment on this RTX
PRO 6000: it is stable at five admitted sessions, passes the Heavy correctness
gate, wins both repaired quality slices, and does not benefit from more than
1,024 reasoning-headroom tokens on this sample. It is restored healthy at
`:39033`. The recommendation remains human-gated and does not validate the
model's advertised one-million-token maximum.
