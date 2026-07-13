# ThinkingCap Qwen3.6-27B FP8 Heavy promotion

**Point-in-time record, 2026-07-12.** ThinkingCap Qwen3.6-27B FP8 was promoted
from resident Heavy challenger to the routed `heavy-local` default on Fakoli
Dark after a repaired, model-aware preflight. GPT-OSS-120B was stopped and
retained as the complete serve-and-router rollback state. Fast and Voice were
not changed.

## Deployed recipe

| Field | Promoted value |
|---|---|
| Served model | `thinkingcap-qwen36-27b-fp8` |
| Checkpoint | `bottlecapai/ThinkingCap-Qwen3.6-27B-FP8` revision `e48255afd77b403446332be0f595868337b36591` |
| Host / accelerator | Fakoli Dark / one RTX PRO 6000 Blackwell 96 GB |
| Engine / quantization | pinned vLLM image digest `sha256:907377dd...5319ff3e`; FP8 weights and FP8 KV |
| Speculative decoding | Qwen3 MTP, 3 speculative tokens |
| Context / admission | serve 262,144 tokens and five sequences; router advertises a conservative 131,072-token Heavy window |
| Heavy default | thinking enabled through `extra_body_defaults` |
| Completion allocation | 256 visible-answer tokens plus 4,096 reasoning-headroom tokens, sent as a 4,352-token cap |

The selected 4K operating point comes from the repeated protocol-v2 result:
ThinkingCap retained 15/15 ARC attempts and reached 9/10 stable MMLU-Pro items
with 27/30 passing attempts. The broader comparison, five-session capacity
result, recipe caveats, and source lineage remain in the
[Qwen3.6 protocol-v2 finding](2026-07-12-qwen36-protocol-v2-comparison.md).

## Promotion gates

The functional gate explicitly disabled thinking and used a 256-token cap. It
passed short coding, structured JSON, a 131,072-token needle, and 20/20
shared-prefix tool calls. Every response ended with `stop` or `tool_calls`, and
no parsed reasoning characters or reasoning-token usage were observed. See
[functional-preflight.json](2026-07-12-thinkingcap-heavy-promotion-evidence/functional-preflight.json).

The separate quality smoke explicitly enabled thinking with 256 visible tokens
plus 4,096 reasoning headroom. Coding and JSON both ended with `stop`, produced
visible answers, and exposed 1,246 and 639 parsed reasoning characters. See
[thinking-preflight-4k.json](2026-07-12-thinkingcap-heavy-promotion-evidence/thinking-preflight-4k.json).

Both the forward and rollback profile/config pairs passed the rebuilt deployed
router image's loader before the model swap. After profile/config replacement,
the router reloaded successfully and the actual Tailnet health endpoint
`http://100.87.34.66:8000/healthz` returned HTTP 200. The direct Heavy health
endpoint at `http://127.0.0.1:30002/health` also returned HTTP 200. The complete
guarded operation finished in 307.1 seconds.

## Safety and caveats

Preflight now records full visible output, `finish_reason`, parsed reasoning
field/length/excerpt, reasoning-token usage when exposed, and indexed tool-call
validation. It rejects disallowed finish reasons and can require or forbid
effective reasoning evidence. The promotion plan requires both gates before
router mutation and automatically restores GPT-OSS on failure; its cold-start
rollback timeout is 1,200 seconds because the earlier 600-second value was not
safe on this host.

This promotion does not establish five simultaneous 262K sessions or a
one-million-token operating window. Five-session evidence used independent 8K
contexts, and the routed Heavy context remains 131K pending retained long-window
validation. GPT-OSS remains the rollback model, not the Heavy default.

Raw artifact hashes are recorded in
[SHA256SUMS](2026-07-12-thinkingcap-heavy-promotion-evidence/SHA256SUMS).
