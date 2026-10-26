<!-- benchmark-publication-summary/v1 -->
# Publication summary

## Canonical facts

Local RTX5090 functional MCP smoke:864×480→1728×960,124frames/24fps.
Sampled generation GPU peak31301MiB of32607; final cold-upscale peak30133MiB.
Native final upscale174.973s; one sample, not a speed comparison.
Recipe and measurement path:installed stdio MCP,authenticated gateway,managed local ComfyUI;
[finding](../2026-10-26-h3-comfyui-mcp.md), [contract](../../H3-COMFYUI-MCP.md).

## Short post

Local RTX5090: H3 MCP generation→2× upscale passed full decode and scoped cleanup. Short-clip smoke; cast continuity untested. https://fakoli.github.io/anvil-serving/findings/2026-10-26-h3-comfyui-mcp/

## Reddit title

Local H3 generation and guided2× upscale through MCP: RTX5090 functional smoke

## Reddit body

One RTX5090 completed a124-frame cafe shot and guided upscale through the installed MCP.
We retained exact assets, raw timing/memory, errors and teardown receipts. The native final
upscale took174.973seconds; samples/cache states differ, so this is not a speedup study.
Recorded-master lip sync and three-host identity still need testing. Generated media is withheld
pending output-rights clearance. The public finding includes the bounded recipe and evidence.

## Screenshot alt text

No generated-media screenshot released. The public result table shows native operation time,
sampled whole-GPU peak, container current/lifetime memory and available Windows memory.

## Claim ledger

| Claim | Authoritative retained evidence |
| --- | --- |
| Output dimensions,frames,FPS,full decode and stream-copy identity | video-verification.json; three native ffprobe/decode captures |
| GPU/RAM/time values | measurements.json and native phase collector rows; recheck-comfy-history.json |
| Replay,cancel,teardown | mcp-replay-inspect.json,mcp-cancel-pending.json,mcp-teardown.json,restoration.json |
| Pinned reconstruction | runtime-identity.json,asset-lock.json and public H3 contract |
| Companion candidates | research/brief.md,89-claim verification and independent adversarial review |

This copy is prepared for publication with the repository documentation; no social post was sent.
