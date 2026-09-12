# Recipe feasibility: 2026-09-12-qwen38-efficient-variants-rtx5090

Required tokens: 65,536

| Candidate | Classification | Estimated policy T_max | Policy VRAM margin | Reason |
| --- | --- | ---: | ---: | --- |
| incumbent-q4xl-mtp3 | unresolved | unknown | unknown | context or VRAM bounds overlap or remain unbounded |
| signal-q6k-mtp3 | benchmark-survivor | [88,301, 197,988] | [1.042, 4.042] GiB | resource bounds pass; behavioral evidence remains incomplete |
| swift-q6k-mtp3 | benchmark-survivor | [79,076, 184,151] | [0.620, 3.620] GiB | resource bounds pass; behavioral evidence remains incomplete |
| qwopus-flash-q6k-nospec | benchmark-survivor | [88,301, 197,988] | [1.042, 4.042] GiB | resource bounds pass; behavioral evidence remains incomplete |
| minitron-20b-q6k-nospec | benchmark-survivor | [233,379, 592,600] | [7.683, 11.058] GiB | resource bounds pass; behavioral evidence remains incomplete |

## Missing evidence

- `incumbent-q4xl-mtp3`: `deterministic_pass_rate`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `signal-q6k-mtp3`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `swift-q6k-mtp3`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `qwopus-flash-q6k-nospec`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `minitron-20b-q6k-nospec`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`

## Load-bearing unknowns

- `incumbent-q4xl-mtp3`: `candidates[0].per_token_components.unseparated_live_kv_slope`

## Unbounded variables

- `candidates[0].per_token_components.unseparated_live_kv_slope`
- `candidates[1].measured_max_stable_context_tokens`
- `candidates[2].measured_max_stable_context_tokens`
- `candidates[3].measured_max_stable_context_tokens`
- `candidates[4].measured_max_stable_context_tokens`

`benchmark-survivor` and `math-qualified` do not authorize production promotion.
