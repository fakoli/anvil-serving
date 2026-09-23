# Runtime alternatives after local failure

Observed 2026-09-21 Pacific; all alternatives are source research, not local measurements.

Official vLLM MiMo v2.6 image: vllm/vllm-openai:mimo-v26@sha256:c202953213b0d8eef6b0e2fc721cfb1ce4d33c3d6ba1c34c54108d55cb21f6e0; amd64 child sha256:5d8a6c5708f5673602b3c7eba0c3c00bd3678e655951a5b8a93c45e10780c42b; source eb87980585c10f7e5ee8f498fd3830898101d208. Independent recipe research inspected TP4-checkpoint to TP2 fused-QKV resharding support and MXFP4 support. The official reference is TP4/H200 orGB300, minimum208 decimalGB, text tasks. Its inspected native MiMo implementation has no vision/audio path. Thus it does not establish a text+vision replacement recipe. The published minimum is a reference envelope, not an unconditional physical theorem ruling out allTP2 recipes.

No current public v2.6 TP2/SM120 result was found in local-inference-lab, VerdictAI or net-snix during this bounded search. Earlier v2.5 recipes are leads only. DFlash would add draft state; it is not a demonstrated remedy for initialization OOM. Language-only would remove required capability and remains an unmeasured diagnostic, not a replacement qualification.

Sources:
- https://github.com/vllm-project/recipes/blob/main/models/XiaomiMiMo/MiMo-V2.6-Flash-RL.yaml
- https://github.com/vllm-project/vllm/tree/eb87980585c10f7e5ee8f498fd3830898101d208
- https://github.com/vllm-project/vllm/blob/eb87980585c10f7e5ee8f498fd3830898101d208/vllm/model_executor/models/mimo_v2.py
- https://github.com/vllm-project/recipes/pull/1008

Disposition: do not spend another live outage on an unsupported backend permutation. Retain GLM; next candidate needs documented memory/load support preserving text+vision, then fresh preflight and matched benchmark cells.
