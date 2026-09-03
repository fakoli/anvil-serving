# Recipe feasibility: 2026-09-03-qwen38-ninfer-nvfp4-rtx5090

Required tokens: 252,928

| Candidate | Classification | Estimated policy T_max | Policy VRAM margin | Reason |
| --- | --- | ---: | ---: | --- |
| qwen38-ninfer-nvfp4-mtp3-252928-c1 | benchmark-survivor | [252,928, 1,419,004] | [0.000, 8.896] GiB | resource bounds pass; behavioral evidence remains incomplete |

## Missing evidence

- `qwen38-ninfer-nvfp4-mtp3-252928-c1`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`

## Unbounded variables

- `candidates[0].metrics.deterministic_pass_rate`
- `candidates[0].metrics.quality_score`
- `candidates[0].metrics.reference_quality_score`
- `candidates[0].metrics.warm_e2e_seconds`
- `candidates[0].metrics.no_spec_warm_e2e_seconds`
- `candidates[0].metrics.successful_tasks_per_hour`
- `candidates[0].metrics.reference_tasks_per_hour`

`benchmark-survivor` and `math-qualified` do not authorize production promotion.
