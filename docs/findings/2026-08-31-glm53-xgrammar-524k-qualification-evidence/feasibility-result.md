# Recipe feasibility: glm53-k3-xgrammar-524k-dual-pro-c2

Required tokens: 258,192

| Candidate | Classification | Estimated policy T_max | Policy VRAM margin | Reason |
| --- | --- | ---: | ---: | --- |
| glm53-k3-524k-nospec-xgrammar-fixed | benchmark-survivor | [1,154,604, 1,542,488] | [21.706, 28.706] GiB | resource bounds pass; behavioral evidence remains incomplete |
| glm53-k3-524k-dflash2-k5-xgrammar-fixed | benchmark-survivor | [1,154,604, 1,542,488] | [21.706, 28.706] GiB | resource bounds pass; behavioral evidence remains incomplete |

## Missing evidence

- `glm53-k3-524k-nospec-xgrammar-fixed`: `deterministic_pass_rate`, `measured_max_stable_context_tokens`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`
- `glm53-k3-524k-dflash2-k5-xgrammar-fixed`: `deterministic_pass_rate`, `no_spec_warm_e2e_seconds`, `quality_score`, `reference_quality_score`, `reference_tasks_per_hour`, `successful_tasks_per_hour`, `warm_e2e_seconds`

## Unbounded variables

- `tracked_variables.runtime_patch_source_hashes`

`benchmark-survivor` and `math-qualified` do not authorize production promotion.
