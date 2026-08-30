# Recipe feasibility: glm53-k3-dflash2-dual-pro6000-wsl2-default

Required tokens: 258,192

| Candidate | Classification | Estimated policy T_max | Policy VRAM margin | Reason |
| --- | --- | ---: | ---: | --- |
| purtell-k3-dflash2-k5-fp8-1m-vision | benchmark-survivor | [1,154,604, 1,542,488] | [21.706, 28.706] GiB | resource bounds pass; behavioral evidence remains incomplete |
| brandon-v84-k4-dflash2-vision-98k | proven-infeasible | unknown | unknown | runtime context limit is below the required token budget |
| cardillo-k4-adaptive-mtp-replayssm-262k | empirically-disqualified | unknown | unknown | Local repeated structured-tool gate passed only 12/20 and produced degenerate repeated output. |
| 0xsero-glm53-flash-exl3-3bpw | empirically-disqualified | unknown | unknown | Publisher-held-out quality evaluation rejected the 3.0 bpw quant; no complete supported dual-GPU server path was published. |

## Missing evidence

- `purtell-k3-dflash2-k5-fp8-1m-vision`: `deterministic_pass_rate`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `brandon-v84-k4-dflash2-vision-98k`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `cardillo-k4-adaptive-mtp-replayssm-262k`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `0xsero-glm53-flash-exl3-3bpw`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`

## Unbounded variables

- `tracked_variables.wsl2_collective_compatibility`
- `candidates[0].metrics.deterministic_pass_rate`
- `candidates[0].metrics.quality_score`
- `candidates[0].metrics.reference_quality_score`
- `candidates[0].metrics.warm_e2e_seconds`
- `candidates[0].metrics.no_spec_warm_e2e_seconds`
- `candidates[0].metrics.successful_tasks_per_hour`
- `candidates[0].metrics.reference_tasks_per_hour`
- `candidates[1].per_token_components.unmodeled_kv`
- `candidates[2].per_token_components.unmodeled_kv`
- `candidates[3].runtime_context_limit_tokens`
- `candidates[3].per_token_components.unmodeled_kv`

`benchmark-survivor` and `math-qualified` do not authorize production promotion.
