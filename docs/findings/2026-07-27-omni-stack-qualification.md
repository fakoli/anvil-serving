# RTX 5090 Omni stack qualification

**Point-in-time record, 2026-07-27.** The Fakoli Dark RTX 5090 topology now
uses one exclusive Nemotron 3 Nano Omni tier for auxiliary text, general image
understanding, and OCR. This replaces three independently resident model
containers; it does not replace embeddings, reranking, voice audio, or ComfyUI.
Those remain separate, mutually exclusive stacks on the same GPU.

## Qualified identity

| Field | Value |
|---|---|
| Checkpoint | `nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4` |
| Revision | `dc5f0b0bfddf8b6e0f5891475be9af05b80126fe` |
| Served name | `nemotron3-omni-30b-a3b-nvfp4` |
| Engine | vLLM `0.23.1rc1.dev531+ga65f93fb2` |
| Image | `vllm/vllm-openai:nightly@sha256:907377dddef392f6b679d9c071e1c33c3935b4dc993b61d0352e391a5319ff3e` |
| Managed serve | `omni`, container `vllm-nemotron3-omni`, port `30003` |
| Hardware | NVIDIA GeForce RTX 5090, 32,607 MiB |
| Context / concurrency | 65,536 tokens / 2 sequences |
| Router aliases | `llm.voice`, `vision.general`, `vision.ocr` -> `omni-local` |

The controller-managed production container reached `/health` with HTTP 200.
Observed GPU use was 27,706 MiB after startup. The reservation owns the full
27,999 MiB usable budget (32,607 MiB capacity less the 4,608 MiB system/audio
reserve), so the stack is intentionally exclusive.

## Gates

The text gate ran with thinking disabled and passed smoke, JSON, 4K retrieval,
and three tool calls. The new multimodal preflight checks used the bounded,
content-addressed PNG input already published with the vision evidence.
General image understanding found all of `RTX 5090`, `Error 503`, and `Retry`;
the OCR check reproduced all of `Anvil Serving Dashboard`,
`VRAM used: 30057 MiB`, `No available tier`, and `planning`.

The bounded capacity run completed 6/6 requests at concurrency two with a
2,048-token prompt plan and 128-token output cap. TTFT p50/p95 was
122/164 ms, end-to-end p50/p95 was 423/515 ms, and aggregate output throughput
was 224.08 tok/s. This is a small operational capacity probe, not a model-quality
comparison.

Raw evidence:

- [Multimodal preflight](2026-07-27-omni-stack-evidence/omni-multimodal-preflight.json)
- [Capacity probe](2026-07-27-omni-stack-evidence/omni-capacity.json)
- [Routed auxiliary text](2026-07-27-omni-stack-evidence/router-llm-voice.json)
- [Routed general vision](2026-07-27-omni-stack-evidence/router-vision-general.json)
- [Routed OCR](2026-07-27-omni-stack-evidence/router-vision-ocr.json)

## Operational result and caveats

`omni-stack` and `llm-stack` can start Omni; `auxiliary-stack` starts only
embeddings and reranking. ComfyUI retains its independent project and can use
the guarded `--evict` flow to quiesce and drain `omni-local` before taking its
on-demand reservation. Voice remains a separate lifecycle and is covered by
the 4,608 MiB non-ledger reserve.

The engine is pinned to a nightly digest because this architecture is not
supported by the older stable/NGC images tested previously. Do not replace the
image tag, digest, or checkpoint revision without rerunning text, tools, image,
OCR, context, and capacity gates. Audio input was not tested here; “Omni”
qualification in this record means auxiliary text plus image/OCR only.

The deployed router began with the legacy four-tier ID set. The guarded
`router install-config` verb quiesced and drained that current set, used the
existing atomic volume-write/restart/rollback implementation, then verified
that only ready `primary-local` and `omni-local` tiers remained.
