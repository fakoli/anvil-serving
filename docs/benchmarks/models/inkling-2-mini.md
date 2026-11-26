# Inkling 2 Mini

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** bounded RTX 5090 candidate; not selected.
    - **Measured hardware:** NVIDIA GeForce RTX 5090, C1 original mixed-precision runtime.
    - **Decision:** `no-promotion`; original split-mode baseline restored.
    - **Review date:** 2026-11-26.

## Immutable identity

[Inkling 2 Mini](https://huggingface.co/thinkingmachines/Inkling-2-Mini) original checkpoint; Q8 is a separate fallback lane.

## Tested hardware and topology

One NVIDIA GeForce RTX 5090; direct C1 bounded requests.

## Engine, quantization, KV, context, and concurrency recipe

Original mixed-precision runtime at C1/24K with a 16,384-token maximum output; Q8 results are separate.

## Evidence by measurement class

At the matched 16,384-token maximum output, the [original Inkling checkpoint](https://huggingface.co/thinkingmachines/Inkling-2-Mini) runtime transported
all 60 fixed-set requests; 44/60 were correct and complete-correct, with four
length stops and a 4.274-second median for complete-correct responses. The
earlier reduced 4,096-token cap result was 42/60 scored correct and 41/60
complete-correct, with 13 length stops. These are bounded local corpus results,
not a quality comparison with another model.

Direct coding, chart, JSON, tool, tool-result, streaming, and image/protocol
checks passed in their stated bounded scopes. The distinct Q8 lane passed short
coding, structured JSON, and 20/20 shared-prefix tools, but its quality run
ended OOM and it supplies no fixed-set or throughput result.

## Controls and limits

A fresh same-hardware Swift control was refused before container creation after
cache reclaim (41.3 GiB available versus 58.0 GiB required); C2 was likewise
refused (38.6 versus 45.1 GiB). No matched Swift comparison was fabricated.
The original split-mode baseline was restored with no active Swift or media
owner and 22 MiB observed GPU use.

## Decision and promotion state

`no-promotion`; no cutover. See the [dated finding](../../findings/2026-11-26-ernie-inkling-runtime-evaluation.md) and the [historical same-hardware Swift finding](../../findings/2026-09-25-swift15-ninfer-5090-promotion.md).

## Failures and gotchas

Fresh Swift controls were refused by host-memory admission; Q8 quality ended OOM.

## Dated run history

- 2026-11-26 — bounded original and Q8 lanes, `no-promotion`.
