# Publication summary: ThinkingCap Qwen3.8 27B, RTX 5090 and 128K context

<!-- benchmark-publication-summary/v1 -->

## Canonical facts

- **Model:** `bottlecapai/ThinkingCap-Qwen3.8-27B-NVFP4A4-AWQ@f8fe157f207a13f977bc3d620ce10a3e9ba5ab11`.
- **Runtime:** vLLM 0.29.0, image digest `082ca6f035279109041ffd3fe0695cb568b29bc580b35c4f297a66a08b216c1b`, engine revision `98dff2a81d747d1dba01a47f939f48c3526d4206`.
- **Recipe:** [pinned MTP3/UVA3 configuration](configurations/thinkingcap-awq-vllm029-5090-128k-mtp3-bf16-uva3.toml), 131,072 total tokens, C1, Triton target/draft, BF16 KV, model-default FP32 recurrent state, graphs and memory fraction .93. Complete local weights include vision and MTP.
- **Memory:** 19.11 GiB GPU weights, 3.02 GiB generic UVA offload including 0.86 GiB visual, 9.7 GiB KV cache, and 138,519 reported cache tokens at startup.
- **Measurement path:** direct managed endpoint on one local RTX 5090 under Windows/Docker Desktop/WSL2; warm process, prefix cache disabled.
- **Capacity:** 100/100 strict requests, nominal 4K/C1, actual 3,609-3,687 prompt tokens, 45 output tokens, 512-token cap, unique prompts and canaries, thinking off. Median TTFT 3,003.0448 ms, decode 51.885477 tok/s, E2E 3,852.9263 ms; aggregate 11.678388 output tok/s.
- **Comparison:** the earlier 32K recipe measured 455.4421 ms median TTFT and 158.355179 tok/s median decode on the same short-output contract. Context, placement and memory fraction change together; no isolated causal claim follows.
- **Completed behavior gates:** 27/27 functional, 9/9 thinking-enabled observations, 18/18 small-image attempts, 30/30 on ten quality questions repeated three times. The MTP/UVA3 context scout passed at actual 32,658 and 125,838 input tokens with a 4,096-token completion allowance.
- **Qualification status:** full context passed 60/60, all completed and normal stop; synthetic agentic passed 2/2 with six turns and four tool calls. The qualified direct candidate remains running with final health 200 and exact pinned identity. No route/client promotion. Separate text-context and small-image tests do not establish combined near-limit vision behavior. No C2, video, SWE, or production soak claim.
- **Evidence:** [finding](../2026-09-24-thinkingcap-context-5090.md), [evidence index](README.md), [manifest](artifact-manifest.json), [capacity comparison](capacity-summary.json), [quality diagnostic](quality-summary.json).

## X / short post

```text
Local RTX 5090: ThinkingCap 27B AWQ runs at 128K/C1 with vision and RAM offload. Short-output decode: 51.9 tok/s (n=100). No promotion. https://fakoli.github.io/anvil-serving/findings/2026-09-24-thinkingcap-context-5090/
```

## Reddit

```text
ThinkingCap 27B on one RTX 5090: the speed cost of 128K context with vision
```

```markdown
On one local RTX 5090, the pinned ThinkingCap Qwen3.8 27B AWQ checkpoint fits a 131,072-token, single-request configuration using MTP3, BF16 KV and 3.02 GiB of weights in system RAM. Vision remains enabled and the complete checkpoint is cached locally.

The 100-request strict short-output test measured 51.9 tok/s median decode and 3.00 seconds median first-token latency. The earlier 32K recipe measured 158.4 tok/s and 0.46 seconds on the same workload. These are whole-recipe results; several settings changed together.

Small-image tests and a limited ten-question repeated diagnostic passed. All 60 full context cases passed, including thirty with 125,838–125,869 actual prompt tokens and a 4,096-token output allowance. Both synthetic agentic cases passed. Separate text and image tests do not prove near-limit combined vision behavior; no route or promotion changed.
```

## Screenshot alt text

Four charts compare complete 32K and 128K recipes on one RTX 5090 using 100 strict short-output requests each. Median decode is 158.4 versus 51.9 tokens per second; median first-token latency is 0.46 versus 3.00 seconds. The 128K recipe offloads 3.02 GiB of weights to system RAM. The charts do not show long-context or image latency.

## Claim ledger

| Claim | Conditions | Evidence |
|---|---|---|
| 128K configured with local weights and vision | 131,072 total prompt plus completion tokens; C1; pinned complete checkpoint | [identity](identity-mtp-uva3.json), [startup](native/mtp-uva3/startup.log), [health](native/mtp-uva3/health.json) |
| Memory placement | Generic UVA, measured startup values | [calibration](calibration-mtp-uva3.json), [engine log](native/mtp-uva3/startup.log) |
| Short-output speed | Strict n=100 per recipe; actual 3,609-3,687 input and 45 output tokens | [current native](native/mtp-uva3/capacity.json), [sealed baseline copy](native/baseline32k/capacity.json), [graph data](benchmark-graph-data.json) |
| Vision and quality | Six small-image cases x3; ten questions x3, not broad accuracy | [vision](native/mtp-uva3/vision.json), [quality](native/mtp-uva3/quality.json) |
| Full context | 60/60, all completed and stop; actual upper inputs 125,838–125,869 and 4,096-token completion allowance | [context summary](context-full-summary.json), [native context](native/mtp-uva3-context-full/artifact.json) |
| Synthetic agentic | 2/2, six turns and four tool calls; not SWE | [agentic summary](agentic-summary.json), [native result](native/mtp-uva3-agentic/artifact.json) |
| No promotion | Direct candidate operation only | [decision](summary.json), [ending state](restoration.json) |

This is prepared publication copy, not a posted message or independent evidence.
