# Publication summary: Qwen3.8 27B quant bakeoff on RTX 5090

<!-- benchmark-publication-summary/v1 -->

This is derivative publishing copy. The
[dated finding](../2026-09-03-qwen38-27b-rtx5090-quant-bakeoff.md) and linked
raw artifacts are authoritative.

## Canonical facts

- **Hardware:** one RTX 5090, 32,607 MiB, isolated direct managed recipe lane
- **Reserve:** zero added reserve only after a process-free idle baseline;
  this was a campaign exception, not a policy change
- **Primary metric winner:** Gittensor target-only NVFP4/SGLang, 50.9 ms warm
  median TTFT and 79.5 tok/s median decode at a 3,613-token prompt
- **Capacity:** exact retrieval passed with 244,002 API-reported prompt tokens
- **Decode winner:** CometKim NInfer MTP3 at 228.0 tok/s, disqualified as a
  general-purpose winner by a 0/3 strict tool-schema result
- **Balanced full-context fallback:** cdiamond iMatrix GGUF MTP8, 223.1 ms
  TTFT and 96.0 tok/s decode
- **Newest Unsloth arm:** Dynamic V3.0 NVFP4 MTP3, 388.7 ms TTFT, 137.7
  tok/s short decode, 127.5 tok/s at 53,706 prompt tokens, tools 20/20
- **Speculation failure:** Gittensor's advertised DSpark pair failed CUDA-
  graph capture on incompatible matrix shapes, not memory
- **Quality caveat:** Gittensor SGLang used default 1.0 FP8 KV scales because
  calibrated scales were absent
- **Restoration:** exact Unsloth GGUF incumbent restored; smoke, JSON, and
  20/20 shared-prefix tools passed
- **Decision:** direct TTFT challenger, `no-promotion`; no route or client
  configuration changed

## Short post

```text
Qwen3.8 27B on one RTX 5090: Gittensor target-only NVFP4/SGLang won TTFT at 50.9 ms median and passed a 244,002-token prompt; CometKim MTP3 won decode at 228 tok/s but failed strict tools 0/3. Incumbent restored, no promotion.
```

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| 50.9 ms median TTFT and 79.5 tok/s decode | five warm C1 requests, 3,613 prompt tokens, thinking disabled | [`gittensor-nospec-timing-4k-c1-warm.json`](gittensor-nospec-timing-4k-c1-warm.json) |
| 244,002 actual-prompt retrieval pass | 252,928 planned context fixture, exact marker | [`gittensor-nospec-capacity-252928-c1.json`](gittensor-nospec-capacity-252928-c1.json) |
| Gittensor bounded coding/triage/tools passed | three attempts per deterministic check plus 8K/32K context | [`gittensor-nospec-quality.json`](gittensor-nospec-quality.json) |
| CometKim MTP3 strict tools failed 0/3 | expected function selected; required `zip` string absent | [`cometkim-mtp3-quality.json`](cometkim-mtp3-quality.json) |
| Unsloth Dynamic V3 MTP3 reached 137.7 tok/s | five warm C1 requests, pinned stock vLLM, 64K profile | [`unsloth-nvfp4-mtp3-timing-4k-c1-warm.json`](unsloth-nvfp4-mtp3-timing-4k-c1-warm.json) |
| Unsloth tools 20/20 and 53,706-token prompt passed | thinking disabled, MTP3, FP8 KV | [`unsloth-nvfp4-mtp3-preflight.json`](unsloth-nvfp4-mtp3-preflight.json) · [`unsloth-nvfp4-mtp3-capacity-57344-c1.json`](unsloth-nvfp4-mtp3-capacity-57344-c1.json) |
| DSpark failed on matrix shapes | exact publisher target/draft/runtime profile | [`gittensor-dspark-load-failure.json`](gittensor-dspark-load-failure.json) |
| Dedicated idle baseline had no GPU process | incumbent unloaded before reserve decision | [`gpu-idle-baseline.json`](gpu-idle-baseline.json) |
| Exact incumbent restored | identity, managed health, and fresh functional checks | [`restoration.json`](restoration.json) · [`restoration-preflight.json`](restoration-preflight.json) |
