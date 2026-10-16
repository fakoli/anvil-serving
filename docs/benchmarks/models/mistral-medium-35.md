# Mistral Medium 3.5

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** unqualified replacement candidate.
    - **Selected or best-qualified configuration:** none.
    - **Measured hardware:** two RTX PRO 6000 Blackwell Max-Q GPUs in TP2.
    - **Evidence:** bounded protocol and native reliability gates; the dated finding holds the sole metric table.
    - **Decision:** `no-promotion`; no route or performance ranking follows.
    - **Important limitation:** configured context is not full-context or concurrent-capacity evidence.
    - **Review dates:** 2026-10-15.

### Review narrative

r2 passed protocol but failed the campaign resource policy. r3 cleared the
generic native floor but missed the frozen campaign reliability requirement.
These are final gate outcomes and neither qualifies a replacement; exact GLM
was retained. See the
[replacement evaluation](../../findings/2026-10-15-replacement-evaluation.md).

## Immutable identity

- `mistralai/Mistral-Medium-3.5-128B@22b2b868a15677cfa6061277ed2f653d1349a9ab`
- r3 served identity: `mistral35-fp8kv-tp2-c4-262k-r3`
- image: `vllm/vllm-openai@sha256:cb9bc64f0f038c2c2ebf13b31248fb87aca40c356c3d20f363be087cfcff1fcd`

## Tested hardware and topology

Two RTX PRO 6000 Blackwell Max-Q GPUs in direct TP2 qualification. This is
measured hardware for the r2/r3 gates, not a claim about a live deployment.

## Engine, quantization, KV, context, and concurrency recipe

vLLM with FP8 KV; r3 declares 262,144 configured tokens, C4 admission, and
up to eight images. It is not measured full-context, C4, multimodal, or
performance evidence.

## Evidence by measurement class

### Resource-policy gate

r2 was rejected by resource policy despite protocol completion.

### Native reliability gate

r3 missed the frozen campaign reliability gate. The [dated finding](../../findings/2026-10-15-replacement-evaluation.md)
contains the authoritative measured values.

## Decision and promotion state

`no-promotion`. No configuration, load, health check, or benchmark result
changes a serve, route, or client catalog.

## Failures and gotchas

A generic native-floor pass does not override the campaign reliability gate.
Configured context and C4 do not prove capacity.

## Dated run history

- 2026-10-15 — [replacement evaluation](../../findings/2026-10-15-replacement-evaluation.md): r2 resource-policy and r3 reliability outcomes.
