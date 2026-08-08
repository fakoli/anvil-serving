# Promotion-plan router profiles are not existence-validated at manifest load

**Status:** Open — product defect; a missing rollback profile is invisible
until a promotion is already attempted

## Problem

`[[serve]]` and `[[promotion]]` entries validate their router profiles
asymmetrically.

For a routed `dual-gpu-exclusive` `[[serve]]`,
`_normalize_mode_router_configs` resolves `router_config` and
`rollback_router_config` and then rejects the manifest when the resolved path
is not a file:

```python
if not os.path.isfile(resolved):
    raise ValueError(f"serve entry {field} does not exist: {resolved}")
```

For a `[[promotion]]` entry, `_load_promotion_plans` performs the same
`{dir}` substitution and `os.path.abspath` resolution for both fields, but
never checks that either file exists. A promotion plan that names a rollback
profile which is absent from the operator home therefore loads, and every
read-only surface — `serves status`, `serves mode status`, `serves mode
preview` — reports a healthy manifest.

The missing file first surfaces inside `_validate_promotion_topology`, which
calls `load_router_config` on both paths. That function is reached only from
`_cmd_promote_unlocked`, `resolve_recipe_activation`, and `cmd_switch` — all
promotion/switch execution paths. So the operator learns that the documented
rollback does not exist at the moment they are trying to promote, which is
exactly when a working rollback matters most.

This is the same class of false safety net recorded in
`2026-08-06-rollback-primary-image-tag-gone.md`: a rollback that cannot run is
worse than a declared absence, because the manifest asserts it is available.

## Evidence

Observed on Fakoli Dark, 2026-08-08, while bringing the promoted DeepSeek
primary back up.

- `hosts/dark/operator-home/serves.toml` declares the `agents-a1-fp8-primary`
  promotion plan with
  `rollback_router_config = "{dir}/anvil-router.qwen35-rollback.toml"`.
- That file does not exist in the operator home. The home contains only
  `anvil-router.live.toml`, `router.toml`, and the
  `anvil-router.deepseek-pi.toml` reconciled on 2026-08-08.
- `serves status`, `serves mode status`, and `serves mode preview` all
  succeeded against that manifest and reported no problem.
- By contrast, the missing `anvil-router.deepseek-pi.toml` referenced by the
  `[[serve]]` entry in `serves.tp2-campaign.toml` failed loudly and blocked
  every `serves` subcommand until it was reconciled:

  ```text
  bad manifest set for .../serves.toml: serve entry router_config does not
  exist: .../anvil-router.deepseek-pi.toml
  ```

The two references were broken by the same 2026-08-02 operator-home migration
(`migration/2026-08-02-dark-operator-home.md`, "Qualification correction").
Only one of them was detectable.

## Required behavior

1. `_load_promotion_plans` must reject a promotion entry whose resolved
   `router_config` or `rollback_router_config` is not an existing file, with
   the same message shape already used for serve entries. Manifest load is the
   correct place: it is the surface every read-only command already exercises.
2. The failure must name the offending promotion entry and the resolved
   absolute path, so a `{dir}`-relative path written for a previous
   operator-home location is diagnosable without reading the loader.
3. Regression coverage must pin both directions — a promotion plan with a
   missing `rollback_router_config` fails at load, and a plan whose profiles
   both exist still loads.

## Operator follow-up (separate from the product fix)

`anvil-router.qwen35-rollback.toml` must be reconciled into the Dark operator
home from an approved typed source, not inferred from `examples/fakoli-dark`
(see `CURRENT_STATE.md`). Its content is fully constrained by
`_validate_promotion_topology`: identical `model_routes` and identical tier ids
to `anvil-router.live.toml`, `model_identity` enabled on `primary-local`, and
`primary-local.model = "qwen35-122b-a10b-nvfp4"` — the `served_name` of the
`primary-qwen35-rollback` serve. Until it exists, the Agents-A1 promotion plan
has no usable rollback.

Note that `llm-stack`, the restore group for exclusive TP=2 mode, is separately
degraded per `2026-08-06-rollback-primary-image-tag-gone.md`. Dark currently
has two independently broken rollback paths.
