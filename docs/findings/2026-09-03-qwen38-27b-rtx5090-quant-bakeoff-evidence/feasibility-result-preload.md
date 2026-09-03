# Recipe feasibility: qwen38-27b-rtx5090-quant-bakeoff-preload

Required tokens: 65,536

| Candidate | Classification | Estimated policy T_max | Policy VRAM margin | Reason |
| --- | --- | ---: | ---: | --- |
| unsloth-dynamic-v3-native-mtp3 | unresolved | unknown | unknown | context or VRAM bounds overlap or remain unbounded |
| neroued-ninfer-nospec-mtp3 | unresolved | unknown | unknown | context or VRAM bounds overlap or remain unbounded |
| gittensor-sglang-nospec | unresolved | unknown | unknown | context or VRAM bounds overlap or remain unbounded |
| gittensor-sglang-dspark | unresolved | unknown | unknown | context or VRAM bounds overlap or remain unbounded |
| quasar-vllm-mtp2 | unresolved | unknown | unknown | context or VRAM bounds overlap or remain unbounded |
| redhat-vllm-mtp4 | unresolved | unknown | unknown | context or VRAM bounds overlap or remain unbounded |
| cometkim-ninfer-mtp3 | unresolved | unknown | unknown | context or VRAM bounds overlap or remain unbounded |
| telperion-autoround-vllm-mtp2 | unresolved | unknown | unknown | context or VRAM bounds overlap or remain unbounded |
| cdiamond-imatrix-gguf-mtp | unresolved | unknown | unknown | context or VRAM bounds overlap or remain unbounded |

## Missing evidence

- `unsloth-dynamic-v3-native-mtp3`: `deterministic_pass_rate`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `neroued-ninfer-nospec-mtp3`: `deterministic_pass_rate`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `gittensor-sglang-nospec`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `gittensor-sglang-dspark`: `deterministic_pass_rate`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `quasar-vllm-mtp2`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `redhat-vllm-mtp4`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `cometkim-ninfer-mtp3`: `deterministic_pass_rate`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `telperion-autoround-vllm-mtp2`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `cdiamond-imatrix-gguf-mtp`: `deterministic_pass_rate`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`

## Load-bearing unknowns

- `unsloth-dynamic-v3-native-mtp3`: `candidates[0].resident_components.runtime_fixed`, `candidates[0].per_token_components.kv`
- `neroued-ninfer-nospec-mtp3`: `candidates[1].resident_components.runtime_fixed`, `candidates[1].per_token_components.kv`
- `gittensor-sglang-nospec`: `candidates[2].measured_max_stable_context_tokens`, `candidates[2].resident_components.runtime_fixed`, `candidates[2].per_token_components.target_kv`
- `gittensor-sglang-dspark`: `candidates[3].resident_components.runtime_fixed`, `candidates[3].per_token_components.target_and_draft_kv`
- `quasar-vllm-mtp2`: `candidates[4].measured_max_stable_context_tokens`, `candidates[4].resident_components.runtime_fixed`, `candidates[4].per_token_components.kv`
- `redhat-vllm-mtp4`: `candidates[5].measured_max_stable_context_tokens`, `candidates[5].resident_components.runtime_fixed`, `candidates[5].per_token_components.kv`
- `cometkim-ninfer-mtp3`: `candidates[6].resident_components.runtime_fixed`, `candidates[6].per_token_components.kv`
- `telperion-autoround-vllm-mtp2`: `candidates[7].measured_max_stable_context_tokens`, `candidates[7].resident_components.runtime_fixed`, `candidates[7].per_token_components.kv`
- `cdiamond-imatrix-gguf-mtp`: `candidates[8].resident_components.runtime_fixed`, `candidates[8].per_token_components.target_and_draft_kv`

## Unbounded variables

- `tracked_variables.runtime_fixed_allocation`
- `tracked_variables.kv_bytes_per_token`
- `tracked_variables.concurrency_capacity`
- `candidates[0].resident_components.runtime_fixed`
- `candidates[0].per_token_components.kv`
- `candidates[1].resident_components.runtime_fixed`
- `candidates[1].per_token_components.kv`
- `candidates[2].measured_max_stable_context_tokens`
- `candidates[2].resident_components.runtime_fixed`
- `candidates[2].per_token_components.target_kv`
- `candidates[3].resident_components.runtime_fixed`
- `candidates[3].per_token_components.target_and_draft_kv`
- `candidates[4].measured_max_stable_context_tokens`
- `candidates[4].resident_components.runtime_fixed`
- `candidates[4].per_token_components.kv`
- `candidates[5].measured_max_stable_context_tokens`
- `candidates[5].resident_components.runtime_fixed`
- `candidates[5].per_token_components.kv`
- `candidates[6].resident_components.runtime_fixed`
- `candidates[6].per_token_components.kv`
- `candidates[7].measured_max_stable_context_tokens`
- `candidates[7].resident_components.runtime_fixed`
- `candidates[7].per_token_components.kv`
- `candidates[8].resident_components.runtime_fixed`
- `candidates[8].per_token_components.target_and_draft_kv`

`benchmark-survivor` and `math-qualified` do not authorize production promotion.
