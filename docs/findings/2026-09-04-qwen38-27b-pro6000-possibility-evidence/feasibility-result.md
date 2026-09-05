# Recipe feasibility: qwen38-27b-pro6000-c8-82k

Required tokens: 90,112

| Candidate | Classification | Estimated policy T_max | Policy VRAM margin | Reason |
| --- | --- | ---: | ---: | --- |
| radixark-nvfp4-nospec-fp8kv | benchmark-survivor | [206,380, 253,896] | [29.806, 39.986] GiB | resource bounds pass; behavioral evidence remains incomplete |
| radixark-nvfp4-dflash2-fp8-draft-kv | benchmark-survivor | [99,644, 129,648] | [3.971, 15.685] GiB | resource bounds pass; behavioral evidence remains incomplete |
| radixark-nvfp4-dflash2-bf16-draft-kv | unresolved | [71,965, 93,635] | [-10.467, 1.935] GiB | context or VRAM bounds overlap or remain unbounded |
| official-fp8-dflash2-fp8-draft-kv | unresolved | [78,646, 108,650] | [-4.776, 7.355] GiB | context or VRAM bounds overlap or remain unbounded |

## Missing evidence

- `radixark-nvfp4-nospec-fp8kv`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `radixark-nvfp4-dflash2-fp8-draft-kv`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `radixark-nvfp4-dflash2-bf16-draft-kv`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `official-fp8-dflash2-fp8-draft-kv`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`

## Unbounded variables

- `tracked_variables.actual_runtime_fixed_bytes`
- `tracked_variables.actual_target_kv_pool_tokens`
- `tracked_variables.actual_active_request_ceiling`

`benchmark-survivor` and `math-qualified` do not authorize production promotion.
