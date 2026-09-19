# Candidate intake

Observed 2026-09-19. The producer's model card and build manifest are external
priors, not local qualification.

- Model: `lyf/Qwen3.8-27B-Huihui-Abliterated-NInfer-NVFP4`
- Revision: `181446902fc777c479749e98cf2abf2250263a8d`
- Source revision: `d42ca8978c5a66e92c3446d46e8adfe03ef692ff`
- NInfer: `a99407c63fc5bbd25d9fb597cbb8ab352bdb01ef`
- Artifact bytes: 21,492,695,040; identical size to prior NInfer artifact.
- Artifact SHA256: `f21f308d3b23ccd627071cd015e413db08deee4356643900518e2b251750fdc2`
- Producer observed date: 2026-08-19 (31 days old).
- Hardware relevance: exact RTX 5090 product; different host and driver.
- Decision impact: bounded 8K scout before capability/context expansion.
- Producer's 204,800 context claim covers startup/API smoke, not long-context quality.

[Pinned source](https://huggingface.co/lyf/Qwen3.8-27B-Huihui-Abliterated-NInfer-NVFP4/tree/181446902fc777c479749e98cf2abf2250263a8d)

The September 3 local NInfer campaign supplies a historical comparison, not a
matched current baseline. It used another artifact/runtime and 252,928 context.
Fresh matched cells are required before claiming memory or speed improvement.

The deployed infrastructure already has registry-based benchmark import and
Workbench card generation; an older local checkout still has a hard-coded
allowlist. Use the deployed source when preparing the repeatable update.
