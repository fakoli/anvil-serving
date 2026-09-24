# Publication summary: ThinkingCap Qwen3.8 27B AWQ 32K BF16 qualification

<!-- benchmark-publication-summary/v1 -->

## Canonical facts

- **Model identity:** `bottlecapai/ThinkingCap-Qwen3.8-27B-NVFP4A4-AWQ@f8fe157f207a13f977bc3d620ce10a3e9ba5ab11`.
- **Runtime identity:** vLLM 0.29.0; Triton attention, MTP3, BF16 KV.
- **Recipe:** [32K BF16 snapshot](configurations/thinkingcap-awq-vllm029-5090-32k-mtp3-triton-bf16.toml).
- **Measurement path:** direct managed C1 endpoint.
- **Capacity:** median 455.4421 ms TTFT and 158.355179 tok/s decode; 61.14585 tok/s aggregate, n=100 strict. Warm process, unique prompts, prefix cache off, nominal 4K input, 45 output tokens, thinking off. Whole-recipe result; no isolated MTP speedup claim.
- **Qualification:** 27 functional, 8 thinking, 18/18 vision, 30/30 limited MMLU, context 60/60, agentic 2/2.
- **Boundary:** two images about 1 MP; zero video; no C2, SWE, soak, maximum-context, route, or promotion claim.
- **Evidence:** [finding](../2026-09-23-thinkingcap-5090.md), [context](context-result-summary.json), [agentic](agentic-result-summary.json).

## X / short post

```text
Local RTX 5090: ThinkingCap Qwen3.8 27B AWQ qualified a bounded 32K/C1 Triton MTP3 BF16 direct lane: 158.355 tok/s median decode, 60/60 context, 2/2 agentic. No promotion: docs/findings/2026-09-23-thinkingcap-5090.md
```

## Reddit

```text
ThinkingCap Qwen3.8 27B: bounded 32K RTX 5090 direct qualification
```

```markdown
One RTX 5090, direct 32K/C1 Triton + MTP3 BF16 KV: 100 strict short-output requests measured 455.4421 ms median TTFT, 158.355179 tok/s median decode, and 61.14585 tok/s aggregate throughput. Standard functional, vision, limited quality, 60/60 context, and 2/2 agentic gates passed. No route or promotion changed.
```

## Screenshot alt text

A 32K/C1 Triton + MTP3 BF16 KV local result on one RTX 5090: 158.355179 tok/s median decode, 60/60 context, and 2/2 agentic. The scope excludes C2, video, SWE, soak, and promotion.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| 32K capacity | n=100 strict, nominal 4K/C1 | [native](native/candidate/triton-bf16-32k/triton-bf16-32k-short32-n100-r1.json) |
| Context | 60/60, retained actual-token ranges | [context](context-result-summary.json) |
| Agentic | 2/2, six turns, four tools | [agentic](agentic-result-summary.json) |
| Final health | pinned 32K managed state, direct retention authorized | [ending state](restoration.json), [HTTP](native/final/http.json), [managed status](native/final/status.json) |
| No promotion | no route/client alias/deployed-topology change | [finding](../2026-09-23-thinkingcap-5090.md) |
