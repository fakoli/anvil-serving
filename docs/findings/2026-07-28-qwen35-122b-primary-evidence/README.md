# Qwen3.5 122B Primary qualification evidence

This directory contains the raw, machine-readable qualification artifacts for
the pinned `nvidia/Qwen3.5-122B-A10B-NVFP4` Primary candidate. See the dated
finding in the parent directory for interpretation, provenance, and caveats.

The `multimodal-*` artifacts record the follow-up with the vision tower
resident: image understanding, OCR, default and explicit thinking, 240K
retrieval, and tool regressions.

Production verification artifacts:

- [Default-thinking image and OCR](production-multimodal-default-thinking.json)
- [Thinking-disabled image and OCR](production-multimodal-thinking-disabled.json)
- [Routed `llm.primary` image and OCR](router-multimodal-default-thinking.json)

The direct Primary artifact retains vLLM's parsed reasoning field. The router
artifact proves image forwarding and output through `llm.primary`; the router
response adapter does not expose the upstream reasoning field, so
reasoning-evidence assertions belong at the direct serve boundary.
