# Qwen3.8 Flash Next NVFP4 262K qualification and promotion

- **Date:** 2026-08-26
- **Decision:** human-authorized reference promotion
- **Measured hardware:** 2× NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation
  Edition, exclusive TP=2 over PCIe without NVLink under WSL2
- **Evidence:** [sanitized summary](2026-08-26-qwen38-flash-next-promotion-evidence/summary.json)

## Outcome

`RadixArk/Qwen3.8-Flash-Next-NVFP4` revision
`7b719225242aacd3dbd3f9407468c2ee9a9d2594` is the human-authorized text
Primary reference at 262,144 served tokens, concurrency one, and an 8,192-token
client output reserve. The endpoint is served as
`qwen38-flash-next-radixark-nvfp4-tp2-262k` through digest-pinned SGLang
`sha256:59f06adce6f91401adf443bd168d45fdb2044d77671fd591c7c57a29d851cbae`.
The engine revision is `d91c3682b0b429e4c70df63cd57f819588ce29b0`.

Authenticated routed coding, structured JSON, 253,325-prompt-token retrieval,
tools 20/20, streaming tools, tool-result continuation, and the Responses API
passed. Real OpenClaw, Hermes, and Pi turns selected `llm.primary`, completed
through the local route, and did not fall back. DeepSeek is no longer the
reference text Primary. Active host assignments and raw client evidence remain
private operator state.

## Exact recipe

The public [262K recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-flash-next-radixark-nvfp4-sglang-tp2-262k-recipe.toml)
and conservative [32K first-load recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-flash-next-radixark-nvfp4-sglang-tp2-32k-recipe.toml)
pin the model revision and container digest. The promoted lane uses:

- two equal 96 GB cards as one exclusive TP=2 owner;
- NVIDIA ModelOpt NVFP4 W4A4 routed experts, with BF16 attention, GDN, QSA,
  shared experts, vision, and MTP tensors;
- 262,144 context, 253,952 prompt budget plus 8,192 output reserve, one running
  request, page size 64, 4,096-token chunked prefill, and 0.80 static memory;
- no MTP/speculative decode, no automatic truncation, and thinking disabled for
  the qualified Chat Completions contract; and
- a fail-closed NCCL logits fallback plus SGLang's device-agnostic QSA sparse
  decoder for this SM120/WSL2 compatibility lane.

The engine reported a 416,064-token KV pool, or 1.587 full configured windows.
Router admission remains one full-window request. The 192 GB figure is
aggregate sharded VRAM, not unified memory.

## Qualification results

### Functional and context

| Gate | Result |
|---|---|
| Direct 262K client contract | exact retrieval at 253,325 prompt tokens in 17.606 s with 8,192 output-token budget and zero reasoning tokens |
| Direct long tool use | valid tool call after 105,756 measured prompt tokens in 4.664 s |
| Routed Primary | coding, JSON, 253,325-token retrieval in 28.38 s, tools 20/20, streaming tools, and tool-result continuation all passed |
| Responses API | completed with exact `READY`; default request emitted no reasoning |
| Real clients | OpenClaw, Hermes, and Pi passed exact route/model acceptance without fallback |
| Native boundary | 262,137 prompt tokens plus one output accepted; a forced-output probe reached 262,142 visible sequence tokens; 262,138 prompt tokens was the first rejected size |

The client contract deliberately reserves output space instead of advertising
the engine's last prompt position as usable client context.

### Repeated behavior and capacity

The thinking-disabled protocol-v3 gate passed intelligence 6/6, session recall
3/3, and typed tools 3/3. This is a bounded behavioral gate, not a general
intelligence ranking or a claim that NVFP4 is lossless.

| Workload | Completion | TTFT | E2E | Decode |
|---|---:|---:|---:|---:|
| 4K, c1, five requests | 5/5 | 214.612 ms p50 | 2.627 s p50 | 12.801 tok/s p50 |
| 4K, c2 queue diagnostic, four requests | 4/4 | 2.822 s p50 | 5.290 s p50 | 12.445 tok/s p50 |
| 128K, c1, one request | 1/1 | 11.961 s | 13.795 s | 12.544 tok/s |

The c2 diagnostic completed but queued behind the one-running-request
scheduler. It supports the concurrency-one admission decision; it is not a
qualified c2 service contract. Decode is materially constrained by the
portable sparse-attention compatibility path and no speculative decoder was
enabled.

## Fix-forward record

The first boots and promotion probes exposed real failures; none were hidden or
converted into a pass:

1. SM120/WSL2 symmetric-memory logits initialization failed. The managed
   recipe now selects the existing NCCL fallback through an exact, fail-closed
   source anchor.
2. The pinned FA4 CuTe sparse decoder failed MLIR compilation on SM120. The
   qualified recipe selects SGLang's device-agnostic QSA sparse decoder while
   leaving the weights and prefill path unchanged.
3. The original long-tool generator did not produce 100K measured tokens. A
   calibrated 125K-target rerun passed at 105,756 measured tokens.
4. Router admission rejected an exact upstream context contract and the
   Responses adapter rejected bounded thinking control. Both product defects
   were fixed with regression coverage before the final routed rerun.
5. Hermes had credential drift and Pi lacked a usable Anvil provider reference.
   The managed client synchronizer now seeds Pi from authenticated router
   metadata, uses environment-key interpolation, and is idempotent. Fresh real
   Hermes, Pi, and OpenClaw requests then passed.

A separate failed final rerun using a stale operator-shell credential is
retained privately as authentication evidence. The successful public claim is
grounded only in the authenticated rerun and sanitized summary.

## Boundaries

- Multimodal weights are present, but this promotion qualified the text Primary
  contract; it does not promote a new image, OCR, or video route.
- The bounded repeated suite passed, but no broad model-quality benchmark or
  same-prompt BF16/FP8/NVFP4 comparison was performed.
- The recipe is pinned to this checkpoint, engine revision, image digest,
  SM120/WSL2 topology, TP size, and compatibility patches. Do not transfer the
  result to another Qwen revision, runtime, GPU, quantization, or speculation
  setting.
- Raw operator artifacts, addresses, credentials, GPU UUIDs, and client
  configuration remain outside the public repository. The linked summary is
  the bounded public evidence for the claims above.
