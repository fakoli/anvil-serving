# Recipe feasibility: glm53-cardillo-adaptive-mtp-dual-sm120-wsl2

Required tokens: 258,192

| Candidate | Classification | Estimated policy T_max | Policy VRAM margin | Reason |
| --- | --- | ---: | ---: | --- |
| cardillo-exact-vision-adaptive-mtp5-replayssm10-262k | benchmark-survivor | [272,606, 1,090,609] | [0.160, 4.631] GiB | resource bounds pass; behavioral evidence remains incomplete |
| tpurtell-text-only-adaptive-mtp5-replayssm10-262k | benchmark-survivor | [366,970, 1,468,128] | [1.210, 6.731] GiB | resource bounds pass; behavioral evidence remains incomplete |

## Missing evidence

- `cardillo-exact-vision-adaptive-mtp5-replayssm10-262k`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `tpurtell-text-only-adaptive-mtp5-replayssm10-262k`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`

## Unbounded variables

- `tracked_variables.wsl2_runtime_compatibility`
- `tracked_variables.system_ram_peak_bytes`
- `candidates[0].metrics.deterministic_pass_rate`
- `candidates[0].metrics.quality_score`
- `candidates[0].metrics.reference_quality_score`
- `candidates[0].metrics.warm_e2e_seconds`
- `candidates[0].metrics.no_spec_warm_e2e_seconds`
- `candidates[0].metrics.successful_tasks_per_hour`
- `candidates[0].metrics.reference_tasks_per_hour`

`benchmark-survivor` and `math-qualified` do not authorize production promotion.
