# ERNIE 5.5 Flash Preview

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** measured long-document candidate; not selected for the retained primary route.
    - **Measured hardware:** two NVIDIA RTX PRO 6000 Blackwell Max-Q GPUs, TP2.
    - **Decision:** `no-promotion`; retained primary route restored.
    - **Review date:** 2026-11-26.

## Immutable identity

ERNIE 5.5 Flash Preview. Exact deployment identity is private; the dated finding retains the public measurement boundary.

## Tested hardware and topology

Two NVIDIA RTX PRO 6000 Blackwell Max-Q GPUs in TP2; direct local text requests.

## Engine, quantization, KV, context, and concurrency recipe

Managed TP2 runtime; C1 long-context and bounded C8/C16 cells. Exact private recipe details are not published.

## Evidence by measurement class

The matched 60-case text/reasoning set scored ERNIE 46/60 and AZ-Focus 49/60.
Forty-four cases were correct for both; ERNIE alone answered two and AZ-Focus
alone five. ERNIE summed 447.929 sequential request seconds and 95,688
completion tokens, against 215.195 seconds and 36,662 tokens for AZ-Focus. On
the 44 shared-correct cases, the sums were 128.202 and 107.965 seconds. This is
a fixed local set, not a general quality or speed ranking.

An independent offline reconstruction verified all 180 stored HTTP request payload hashes. The frozen
legacy scorer is retained unchanged; a visible-answer-only sensitivity scorer
returns AZ-Focus/ERNIE/Inkling 50/46/46 rather than 49/46/44. It is a scorer
sensitivity result, not a revision of the historical scores.

One request each at 32,185 and 63,150 actual input tokens produced first visible
output at 4.25 and 7.03 seconds. A single-target needle passed at 999,990 actual
input tokens: first model token at 201.111 seconds and correct completion at
202.005 seconds. The 39,082- and 55,092-target fixtures reported 60,807 and
85,713 actual prompt tokens, respectively, and both passed. These are C1
observations with one target at one position, not broad context reliability.

Bounded C8/C16 fixed-output cells measured 616/815 tokens/s from first output
to last completion and 353/359 tokens/s over each entire batch. The C8/C16
natural cells passed 8/8 and 16/16 semantic markers. A C1 length-limited
16,384-token coding response measured 223.0 end-to-end tokens/s; a fixed
8,192-token response measured 223.8. These output populations differ and none
is a general service-speed or saturation claim.

The independent coding/protocol checks passed: agentic 3/3 and Aider exercises
2/2. The five-issue SWE scout resolved one issue, left two unresolved, and had
two call-budget exhaustion outcomes before grading; it is not a full-suite
score. The retained AZ-Focus direct smoke, JSON, tools, tool-result, streaming
tools, and image checks all passed after restoration; ERNIE itself is text-only.

## Published Local Inference Lab reference

Local Inference Lab [reported](https://huggingface.co/local-inference-lab/ERNIE-5.5-Flash-Preview-FLARE/blob/235cd708d006dbea3fc39fe05c26e774e1c4ce8a/README.md) approximately 919 tokens/s at C8 and 1,340
tokens/s at C16 for a pinned public package. Its prompt/completion workload and
decode timing boundary differ. The local configuration also used a different GPU
product, host-cache size, and driver setting; its shared KV pool was close in
size but not an equivalence proof. Treat the figures as an external
hardware/runtime reference, not a reproduction or causal comparison.

## Decision and promotion state

Retain AZ-Focus as the primary route. ERNIE's bounded long-document behavior
may justify a separately selected text-only service, but the fixed-set result,
SWE scout, and mismatched external comparison do not justify promotion.

Evidence is retained in the [dated finding](../../findings/2026-11-26-ernie-inkling-runtime-evaluation.md).

## Failures and gotchas

The fixed set favors AZ-Focus; ERNIE is text-only, and external LIL figures are not comparable.

## Dated run history

- 2026-11-26 — bounded evaluation, `no-promotion`.
