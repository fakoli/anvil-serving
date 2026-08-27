# Recipe feasibility: qwen38-flash-next-inferact-vllm-tp2-262k-nospec-preload

Required tokens: 262,144

| Candidate | Classification | Estimated policy T_max | Policy VRAM margin | Reason |
| --- | --- | ---: | ---: | --- |
| inferact-nvfp4-vllm-tp2-262k-nospec-gmu090 | unresolved | unknown | unknown | context or VRAM bounds overlap or remain unbounded |

## Missing evidence

- `inferact-nvfp4-vllm-tp2-262k-nospec-gmu090`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`

## Load-bearing unknowns

- `inferact-nvfp4-vllm-tp2-262k-nospec-gmu090`: `candidates[0].measured_max_stable_context_tokens`, `candidates[0].resident_components.runtime_allocation`, `candidates[0].per_sequence_components.scheduler_and_recurrent_state`, `candidates[0].per_token_components.target_kv`

## Unbounded variables

- `candidates[0].measured_max_stable_context_tokens`
- `candidates[0].resident_components.runtime_allocation`
- `candidates[0].per_sequence_components.scheduler_and_recurrent_state`
- `candidates[0].per_token_components.target_kv`
- `candidates[0].metrics.deterministic_pass_rate`
- `candidates[0].metrics.quality_score`
- `candidates[0].metrics.successful_tasks_per_hour`
- `candidates[0].metrics.reference_tasks_per_hour`

`benchmark-survivor` and `math-qualified` do not authorize production promotion.

This unresolved pre-load result is retained because the operator explicitly
authorized a bounded compatibility spike. The spike may replace unknowns with
managed startup and benchmark evidence; it must not be treated as a fit or
promotion claim.
