# MiMo v2.6 Flash campaign publication summary

<!-- benchmark-publication-summary/v1 -->

This is derivative publishing copy. The [dated finding](../2026-09-21-mimo-v26-qualification.md) and linked artifacts are authoritative.

## Canonical facts

- **Model identity:** `XiaomiMiMo/MiMo-V2.6-Flash-RL@3b38d063180c3e4aed9691fdc735f3d10b266ee4`.
- **Runtime identity:** SGLang v0.5.20 / `94602c9c2b7cbdb8efd5c52802dac6a1c180089e`; image digest `sha256:2b6c8893bb821bc9ac3fb95bc5c7ab8bddf748cf288a0ad6ad60ee96c49610ee`.
- **Local outcome:** MiMo V3 loaded with `flashinfer_mxfp4`, then failed two bounded correctness probes with zero visible content; no capacity or throughput metric is eligible.
- **Baseline result:** GLM's 32K nominal C4/16-word final completed 100/100 eligible requests; p50 visible TTFT was 21.98 s and p50 E2E was 26.03 s; exact figures are in the [derived native summary](glm-capacity-c4-16word-final-summary.json).
- **Important caveat:** MiMo correctness failed; final GLM restoration is verified. The SWE native official summary is 0/1 with infrastructure confounds; no coding pass/fail is attributed solely to GLM.
- **Context boundary:** 327,680 tokens were requested and advertised; the measured full-attention KV pool held 91,342 tokens. No long-context acceptance was run.
- **Decision:** retain GLM; `no-promotion`. No route, alias, or client change is authorized.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| GLM final C4 cell is eligible | 100 requests, nominal 32K, C4, unique prompts within cell, 16 controlled words | [native final](glm-capacity-c4-16word-final.json) · [summary](glm-capacity-c4-16word-final-summary.json) |
| MiMo parser failure | first SGLang v0.5.20 load | [log](mimo-c1-logs.log) · [status](mimo-c1-status.log) |
| MiMo OOM is startup-only evidence | corrected TP2 attempt; before weights/KV | [recovered transcript](mimo-v2-tool-transcript.txt) · [provenance](mimo-v2-capture-provenance.json) |
| MiMo V3 correctness failure | weight loading and transport health passed; temperature-zero and official sampling diagnostics | [sampling diagnostic](mimo-v3-sampling-smoke.json) · [capture](mimo-v3-final-logs.log) |
| No promotion | MiMo correctness/capacity gates fail or remain missing; final restoration verified | [finding](../2026-09-21-mimo-v26-qualification.md) · [summary](summary.json) |

[Artifact manifest](artifact-manifest.json) · [Evidence index](README.md). This copy was saved locally; no external post was sent.
