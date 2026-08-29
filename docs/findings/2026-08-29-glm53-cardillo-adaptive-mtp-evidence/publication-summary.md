# Publication summary: GLM-5.3-Flash and bounded dual-PRO qualification

<!-- benchmark-publication-summary/v1 -->

This file is derivative publishing copy. The linked dated finding and raw
artifacts are authoritative.

## Canonical facts

- **Model identity:** `brandonmusic/GLM-5.3-Flash-tr3-4bpw@5ab363a8dcf6405955fd5f99671e01a1c9fb124b`; preferred served name `glm53-flash-tr3-4bpw-tp2-262k-fixed-mtp5-vision`
- **Runtime identity:** `vLLM 0.1.dev20051+g487ecf187`; pinned Purtell image digest `sha256:da5cec95778bf6996660b52e28a6e51737fec69cfc3d508bf298c8a89f273ac5`
- **Local setup:** 2x RTX PRO 6000 Blackwell Max-Q 96 GB, TP=2 over PCIe, Docker Desktop/WSL2, vision fixed K5, 262K, c1 performance
- **Recipe:** [managed vision fixed-K5 262K recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-fixed-mtp5-vision-sm120-tp2-262k-wsl2-v2-no-owner-exchange-recipe.toml)
- **Measurement path:** warm direct OpenAI-compatible API; no router or client path
- **Headline result:** vision fixed K5 decoded at 72.8 tok/s at 4K and 55.7 tok/s at 128K, each from three c1 low-reasoning repetitions
- **Capability result:** image understanding, verbatim OCR, tools 20/20, bounded high-reasoning coding 15/15, and exact retrieval at a 250K target / 206,296 actual prompt tokens passed; the no-spec companion recovered an exact needle at 495,045 prompt tokens
- **Important caveat:** video is disabled, only one-image behavior is proven, adaptive K1-K5 plus ReplaySSM corrupted repeated tools, and 0xSero 3.0-bpw has no complete serve path and failed its publisher's held-out quality gate
- **Decision:** `challenger`, `no-promotion`; no route, client, or promotion state changed
- **Canonical evidence:** https://github.com/fakoli/anvil-serving/blob/main/docs/findings/2026-08-29-glm53-cardillo-purtell-qualification.md

## X / short post

```text
Dual-PRO GLM-5.3-Flash: vision/OCR, tools 20/20, 262K context, 72.8 tok/s. 3-bpw watch-only. No promotion. https://github.com/fakoli/anvil-serving/blob/main/docs/findings/2026-08-29-glm53-cardillo-purtell-qualification.md
```

## Reddit

```text
Dual RTX PRO 6000 GLM-5.3-Flash 4 bpw: vision/OCR and 262K serving qualified
```

```markdown
I reproduced the Cardillo/Purtell GLM-5.3-Flash recipe on two RTX PRO
6000 Blackwell Max-Q cards under WSL2. Vision fixed K5 passed semantic image
understanding, verbatim OCR, 20/20 tools, exact retrieval at a 250K target /
206,296 actual prompt tokens,
and a bounded 15/15 high-reasoning coding suite. It reached 72.8 tok/s at 4K
and 55.7 at 128K. Video remains disabled. The no-spec 524K profile recovered
an exact needle at 495,045 prompt tokens and retained 1,603,111 reported KV
tokens. Adaptive MTP corrupted repeated tool output. The 0xSero 3.0-bpw lead
was not downloaded because its own release ledger lacks a complete server and
records a failed held-out quality gate. GLM remains unpromoted pending hands-on
and routed client acceptance. Full immutable identities, recipes, failures,
raw artifacts, and runtime-state proof are in the canonical finding:
https://github.com/fakoli/anvil-serving/blob/main/docs/findings/2026-08-29-glm53-cardillo-purtell-qualification.md

What matched or differed on your hardware?
```

## Screenshot alt text

Benchmark result card for GLM-5.3-Flash TR3/EXL3 4 bpw on two 96 GB RTX PRO
6000 Blackwell Max-Q GPUs. Vision fixed five-token MTP passed image, OCR,
tools, bounded coding, and a 250K-target / 206,296-actual retrieval while reaching about
73 output tokens per second at 4K. A no-speculation profile passed exact
retrieval near 495K prompt tokens with three full windows of reported KV
capacity. Adaptive MTP is marked rejected for tool corruption, 0xSero 3.0-bpw
is watch-only, and the overall model remains no-promotion.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| semantic image and verbatim OCR pass | exact vision recipe; one image; video disabled | [matched local results](../2026-08-29-glm53-cardillo-purtell-qualification.md#matched-local-results); [`vision-fixed-mtp5-preflight-all-low.json`](vision-fixed-mtp5-preflight-all-low.json) |
| vision tools 20/20 and coding 15/15 | direct API; high control recorded for coding but not independently verified | [matched local results](../2026-08-29-glm53-cardillo-purtell-qualification.md#matched-local-results); [`vision-fixed-mtp5-preflight-all-low.json`](vision-fixed-mtp5-preflight-all-low.json) and [`vision-fixed-mtp5-coding-agent-v2-high-r3.json`](vision-fixed-mtp5-coding-agent-v2-high-r3.json) |
| 250K-target / 206,296-actual exact retrieval | one calibrated request | [maximum context](../2026-08-29-glm53-cardillo-purtell-qualification.md#maximum-context-and-concurrency-results); [`vision-fixed-mtp5-needle-250k-low.json`](vision-fixed-mtp5-needle-250k-low.json) |
| vision fixed K5 72.8/55.7 tok/s | c1 low reasoning, three repetitions | [matched local results](../2026-08-29-glm53-cardillo-purtell-qualification.md#matched-local-results); [`vision-fixed-mtp5-capacity-4k-c1-low-r3.json`](vision-fixed-mtp5-capacity-4k-c1-low-r3.json) and [`vision-fixed-mtp5-capacity-128k-c1-low-r3.json`](vision-fixed-mtp5-capacity-128k-c1-low-r3.json) |
| fixed K5 tools 20/20 | exact model/image, direct API | [matched local results](../2026-08-29-glm53-cardillo-purtell-qualification.md#matched-local-results); [`fixed-mtp5-tools-low-r1.json`](fixed-mtp5-tools-low-r1.json) and [`fixed-mtp5-524k-tools-high-r1.json`](fixed-mtp5-524k-tools-high-r1.json) |
| high-reasoning coding 15/15 | bounded five-item diagnostic; request control recorded but not independently verified | [matched local results](../2026-08-29-glm53-cardillo-purtell-qualification.md#matched-local-results); [`fixed-mtp5-524k-coding-agent-v2-high-r3.json`](fixed-mtp5-524k-coding-agent-v2-high-r3.json) |
| 495,045-token exact retrieval | one calibrated request per profile | [maximum context](../2026-08-29-glm53-cardillo-purtell-qualification.md#maximum-context-and-concurrency-results); [`nospec-524k-needle-500k-actual-low.json`](nospec-524k-needle-500k-actual-low.json) and [`fixed-mtp5-524k-needle-500k-actual-low.json`](fixed-mtp5-524k-needle-500k-actual-low.json) |
| 497,976-token valid tool call | one calibrated request per profile | [maximum context](../2026-08-29-glm53-cardillo-purtell-qualification.md#maximum-context-and-concurrency-results); [`nospec-524k-long-tool-500k-actual-low.json`](nospec-524k-long-tool-500k-actual-low.json) and [`fixed-mtp5-524k-long-tool-500k-actual-low.json`](fixed-mtp5-524k-long-tool-500k-actual-low.json) |
| fixed K5 69.8/61.9 tok/s | c1 low reasoning, three repetitions | [matched local results](../2026-08-29-glm53-cardillo-purtell-qualification.md#matched-local-results); [`fixed-mtp5-capacity-4k-c1-low-r3.json`](fixed-mtp5-capacity-4k-c1-low-r3.json) and [`fixed-mtp5-capacity-128k-c1-low-r3.json`](fixed-mtp5-capacity-128k-c1-low-r3.json) |
| adaptive MTP rejected | repeated tool corruption and 13/15 coding attempts | [matched local results](../2026-08-29-glm53-cardillo-purtell-qualification.md#matched-local-results); [`preflight-tools-repeat01.json`](preflight-tools-repeat01.json) and [`adaptive-coding-agent-v2-low-r3.json`](adaptive-coding-agent-v2-low-r3.json) |
