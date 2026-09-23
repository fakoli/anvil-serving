# Publication summary: Qwen3.8 Flash Next shortlist trial

<!-- benchmark-publication-summary/v1 -->

This is derivative copy. The [dated finding](../2026-09-22-model-shortlist-validation.md)
and retained native artifacts are authoritative.

## Canonical facts

- **Model identity:** `RadixArk/Qwen3.8-Flash-Next-NVFP4@7b719225242aacd3dbd3f9407468c2ee9a9d2594`.
- **Runtime identity:** SGLang `4ccff141dbe992794f9da6c3aa23535b4f72000d`; image
  `sha256:8ac9dedd8c3c98bec7b91f4c2c7b697e4f429e336428dd8167140075e295bae7`.
- **Local setup:** 2x RTX PRO 6000 Blackwell Max-Q, TP2, C4 configured admission, 262,144 configured tokens.
- **Measurement path:** direct online cold-start and serial C1 functional/agentic checks.
- **Managed recipe / reproduction:** operator-private recipe; public reconstruction in the
  [exact configuration](../2026-09-22-model-shortlist-validation.md#exact-configuration)
  and [run plan](run-plan.md#qwen-initial-recipe).
- **Headline result:** startup 65.55 GiB/rank without OOM; direct preflight groups 6/6.
- **Capability result:** Qwen scouts were 16/18 greedy and 15/18 native sampling; paired GLM was 16/18.
- **Important caveat:** native results pass their own 0.75 floor, but fixture/scorer defects block broad planning/coding comparison.
- **Decision:** retain GLM; Qwen is unqualified and `no-promotion`.
- **Canonical evidence:** [dated finding](../2026-09-22-model-shortlist-validation.md).
- **Artifact set:** [manifest](artifact-manifest.json) and [evidence index](README.md).

## Screenshot alt text

Qwen3.8 Flash Next NVFP4 running TP2 on two RTX PRO 6000 GPUs loaded
without OOM and passed six serial C1 direct checks; C4 admission was configured,
not measured. Agentic scouts scored 16 of 18 and
15 of 18, below the campaign's required perfect deterministic score; no
capacity or throughput result is claimed.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| Qwen startup and direct preflight passed | TP2, cold start, serial C1 checks; C4 configured only | [finding](../2026-09-22-model-shortlist-validation.md#results); [status](qwen-ready-status.json) |
| Agentic comparison is blocked | 18 observations per arm; campaign requires 18/18 | [finding](../2026-09-22-model-shortlist-validation.md#failures-and-caveats); [Qwen greedy](qwen-agentic-scout-native.json); [paired GLM](glm-agentic-scout-native.json) |
| No performance claim follows | Capacity and performance were not run | [finding](../2026-09-22-model-shortlist-validation.md#evidence-boundary) |

## Short post

Local Qwen3.8 NVFP4: 6/6 serial checks passed on 2x RTX PRO 6000. Harness defects block a coding comparison; GLM retained. https://fakoli.github.io/anvil-serving/findings/2026-09-22-model-shortlist-validation/

Draft copy only; the canonical site URL becomes available after publication.

## Reddit variant

**Title:** Local Qwen3.8 NVFP4 scout: basic checks pass, agentic comparison remains unresolved

**Body:**

We screened independent benchmarks before trying the pinned RadixArk NVFP4
checkpoint with SGLang TP2. It loaded at 65.55 GiB of weights per rank and
passed six serial C1 preflight groups. The Qwen greedy and paired GLM scouts
both recorded 16/18, but fixture-state drift and a planning-scorer false
negative prevent a broad coding comparison. Capacity, vision and performance
tests remain unrun. The exact GLM baseline is restored. The finding links the
native traces, runtime identity and open harness ticket.
