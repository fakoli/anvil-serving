# Recipe feasibility: GLM-5.3-Flash-FP4-Local TP2 matched A/B

Required tokens: 327,680

| Candidate | Classification | Estimated policy T_max | Policy VRAM margin | Reason |
| --- | --- | ---: | ---: | --- |
| nvidia-glm53-fp4-local-tp2-dcp2-327k-mtp3-r1 | unresolved | unknown | unknown | context or VRAM bounds overlap or remain unbounded |

The configured context and one-request budget are both 327,680 tokens: a
318,464-token input plus 9,216 output/reasoning reserve. No local
stable-context result exists. The requested FP8 KV pool is known, while runtime
residency, DCP2 token density, per-sequence state, host embedding and workspace
remain unbounded. Checkpoint download size and NVIDIA's approximate VRAM figure
are not used as runtime-allocation estimates.

The campaign owner provisionally waives the default 4 GiB-per-GPU headroom only
for NVIDIA's published 0.985 model-only startup profile. This is not a safety,
workload, stability, or qualification claim; startup and workload telemetry are
mandatory. C4 timing is limited to 8K and 32K inputs, and says nothing about
four concurrent 327K requests.

The next defensible step is a bounded managed startup probe, then allocation
telemetry and independent deterministic checks before C4 or long-context
qualification. This result grants no promotion authority.
