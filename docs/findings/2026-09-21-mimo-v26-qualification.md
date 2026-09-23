# MiMo v2.6 Flash replacement qualification

**Date:** 2026-09-21. **Decision:** retain GLM, `no-promotion`.

<!-- benchmark-result-card/v1 -->
## Result card

> The GLM control completed the bounded 32K/C4 final cell. MiMo V3 loaded with the memory-scale remedy but failed both bounded correctness probes with zero visible content.

| Setup | Retained value |
|---|---|
| Baseline | GLM-5.3-Flash EXL3 r10 APC, TP2/C4, nominal 32K unique-prompt cell |
| Candidate | MiMo v2.6 Flash RL, pinned SGLang TP2/C1 reconstruction |
| Hardware | Two RTX PRO 6000 Blackwell Max-Q cards, native Linux |
| Candidate runtime | SGLang v0.5.20; `--moe-runner-backend flashinfer_mxfp4`; official mixed MXFP4/FP8, no speculation |
| Candidate context | 327,680 requested/advertised; 91,342 full-attention KV tokens allocated, C1 |
| Measurement | Direct baseline and bounded MiMo negative correctness probes |
| Decision | Retain GLM; no promotion or route change |

| Headline measurement | Local result | Conditions |
|---|---:|---|
| GLM final C4 | 100/100 eligible; p50 TTFT 21.98 s, E2E 26.03 s | nominal 32K, 16 controlled words, C4; short-answer service cell |
| MiMo valid functional responses | 0 | V3 weight loading and transport health passed; both probes exhausted 9,216 reasoning tokens with zero visible content |

**Important caveat:** the 128-word GLM scout failed strict output and is not pooled with the 16-word cells. The final C4 p99 is one N=100 order statistic, not a stable service-level tail. Final restoration is verified; SWE grading is complete but confounded.

[Evidence index](2026-09-21-mimo-v26-qualification-evidence/README.md) · [Artifact manifest](2026-09-21-mimo-v26-qualification-evidence/artifact-manifest.json) · [publication summary](2026-09-21-mimo-v26-qualification-evidence/publication-summary.md).

## Outcome and method

The initial SGLang flag was ambiguous. V2 then ended with CUDA OOM in `AudioProjection`. V3 changed only to SM120 `flashinfer_mxfp4`, reached health, and then failed both bounded correctness probes. The corruption cause is unknown: TP4-to-TP2 fused-QKV handling and manually selected FlashInfer are investigation surfaces only. Template/parsers match the card, explicit official sampling also failed, and all 48 QKV-scale tensors resolved on both ranks. This is configuration-specific evidence, not proof that every MiMo runtime or recipe is infeasible.

GLM's direct six-check preflight and the final 100-request C4 cell are retained baseline evidence. The final cell follows an eight-request scout with the same canary seed, so its first eight requests may be warm; engine cache-hit fields were unavailable. No MiMo performance bar or comparative graph is valid.

The official vLLM 208 GB reference is text-only deployment guidance, not a physical proof that all TP2 recipes fail. No hardware-matched externally validated MiMo v2.6 recipe was found.

## Evidence boundary

The public bundle preserves the [run plan](2026-09-21-mimo-v26-qualification-evidence/run-plan.md), [workload manifest](2026-09-21-mimo-v26-qualification-evidence/workload-manifest.json), and [friction log](2026-09-21-mimo-v26-qualification-evidence/friction-log.md). MiMo V3 has bounded negative correctness evidence but no capacity, performance, vision, or coding result. The native SWE official summary is 0/1 with explicit infrastructure confounds. Final [GLM restoration](2026-09-21-mimo-v26-qualification-evidence/restoration.json) is verified, including exact identity/config-hash parity, direct six-check preflight, and authenticated routed smoke.
