# Recipe feasibility: media-gateway-comfyui-rtx5090-first-workflows

Required tokens: 0

| Candidate | Classification | Estimated policy T_max | Policy VRAM margin | Reason |
| --- | --- | ---: | ---: | --- |
| image-flux2-klein-4b-fp8-v1 | benchmark-survivor | [11,434,221,345, 15,900,628,604] | [10.649, 14.809] GiB | resource bounds pass; behavioral evidence remains incomplete |
| video-wan2.2-ti2v-5b-v1 | benchmark-survivor | [5,171,757,616, 10,207,479,759] | [4.817, 9.506] GiB | resource bounds pass; behavioral evidence remains incomplete |

## Missing evidence

- `image-flux2-klein-4b-fp8-v1`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `video-wan2.2-ti2v-5b-v1`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`

## Unbounded variables

- `tracked_variables.system_ram_peak_bytes`
- `candidates[0].tracked_variables.peak_vram_bytes`
- `candidates[1].tracked_variables.peak_vram_bytes`

`benchmark-survivor` and `math-qualified` do not authorize production promotion.
