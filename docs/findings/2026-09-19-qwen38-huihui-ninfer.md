# Huihui Qwen3.8 27B NInfer NVFP4 scout on RTX 5090

**Date:** 2026-09-19

**Scope:** one RTX 5090 in an isolated Windows/WSL2 direct 8K/C1 scout; no router, alias, or serve-promotion change.

**Decision:** `rejected`, `no-promotion`. The tested profile is not qualified; managed restoration was verified.

<!-- benchmark-result-card/v1 -->
## Result card

| Setup | Result |
|---|---|
| Candidate | `lyf/Qwen3.8-27B-Huihui-Abliterated-NInfer-NVFP4@181446902fc777c479749e98cf2abf2250263a8d` |
| Runtime | NInfer `a99407c63fc5bbd25d9fb597cbb8ab352bdb01ef`; CUDA 13.1.2 digest retained in evidence |
| Profile | NVFP4, no speculation, 8K/C1; direct endpoint; `chat_template_kwargs` unsupported, server `--no-thinking` used |
| Functional | smoke/JSON, tools, streaming, tool-result continuation, Responses subset, and 4K needle passed |
| Quality | default: diff 0/3, triage 3/3, tools 0/3; low-effort diagnostic: diff 3/3, triage 3/3, tools 0/3 |
| Vision | initial 0/12 was `vision_disabled`; corrected `--vision` image-only rerun passed 12/12 |
| Decision | retain the GGUF incumbent; no performance, footprint, long-context, or promotion claim |

**Why it matters:** repeated strict tool failures rule this candidate out before expensive expansion.

**Important caveat:** no matched performance or footprint comparison exists, and the incumbent uses a different context profile.

Artifact manifest: [role ledger](2026-09-19-qwen38-huihui-ninfer-evidence/artifact-manifest.json) · Evidence index: [bundle README](2026-09-19-qwen38-huihui-ninfer-evidence/README.md) · Publication summary: [summary](2026-09-19-qwen38-huihui-ninfer-evidence/publication-summary.md)

## Outcome and decision

The server-control preflight passed smoke and structured JSON; the protocol surface also passed tools, streaming, continuation, Responses, and a 4K needle. An earlier request with unsupported `chat_template_kwargs.enable_thinking` returned HTTP 400 and remains retained as a protocol/configuration failure.

Default repeated quality failed unified diff 0/3 and strict tools 0/3. The low-`reasoning_effort` diagnostic repaired diff to 3/3, while tools remained 0/3. The incumbent control passed diff, triage, and tools 3/3 each. These results halt MTP, context, speed, memory, Workbench, and Grafana expansion.

The first image-only artifact retained twelve HTTP 400 `vision_disabled` failures. Reloading the same pinned candidate with `--vision` produced a separate 12/12 image-only pass; both artifacts are retained.

## Exact configuration

The candidate artifact is `qwen3_8_27b_nvfp4.ninfer`, 21,492,695,040 bytes, SHA-256 `f21f308d3b23ccd627071cd015e413db08deee4356643900518e2b251750fdc2`. The measured device was one NVIDIA GeForce RTX 5090. Candidate source and producer/runtime claims are advisory provenance, not promotion-quality evidence. Native artifacts leave `engine_build_ref` null; see the retained [configuration](2026-09-19-qwen38-huihui-ninfer-evidence/configuration.json). The source-build cache is not a clean immutable runtime recreation.

## Results

- [Server-control preflight](2026-09-19-qwen38-huihui-ninfer-evidence/preflight-nospec-server-control.json): 2/2 smoke and JSON pass.
- [Protocol preflight](2026-09-19-qwen38-huihui-ninfer-evidence/preflight-nospec-protocol.json): tools, streaming, continuation, Responses, and 4K needle pass.
- [Default quality](2026-09-19-qwen38-huihui-ninfer-evidence/quality-nospec-8k.json): diff 0/3, triage 3/3, tools 0/3.
- [Low-effort diagnostic](2026-09-19-qwen38-huihui-ninfer-evidence/quality-nospec-reasoning-low.json): diff 3/3, triage 3/3, tools 0/3.
- [Vision-enabled image rerun](2026-09-19-qwen38-huihui-ninfer-evidence/multimodal-nospec-8k-vision.json): 12/12 pass.
- [Incumbent control](2026-09-19-qwen38-huihui-ninfer-evidence/quality-incumbent-control.json): diff, triage, and tools 3/3 each; [preflight](2026-09-19-qwen38-huihui-ninfer-evidence/preflight-incumbent.json) 2/2.

## Failures and caveats

- **Strict tools:** 0/3 under both default and low-effort settings. Malformed arguments were discarded, so parser, template, and model cause remain unconfirmed.
- **Default coding:** unified diff was 0/3; low effort is diagnostic, not a pass for the original arm.
- **Vision configuration:** initial 0/12 `vision_disabled`, followed by separate 12/12 `--vision` rerun.
- **Comparison gap:** no controlled-output performance, matched footprint, or comparable-context cell.
- **Repeatability:** immutable runtime recreation is unproven.

## Restoration

The temporary comparison containers were removed from the managed recipe inventory; the original stopped incumbent was preserved. The prior `qwen-local` manifest remained absent as at capture. Managed media worker and media MCP groups returned healthy HTTP 200, mounts were writable, all three original live manifest SHA hashes were unchanged, and post-run GPU use was 686 MiB versus 702 MiB at capture. See [restoration evidence](2026-09-19-qwen38-huihui-ninfer-evidence/restoration.json).

## Evidence boundary

This is a local 8K/C1 scout only. It does not authorize a serve, direct-alias, route, client, or promotion change. `promoted=false`; no human-approved serve-promotion result exists.
