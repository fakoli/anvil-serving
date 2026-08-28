# ComfyUI compatibility must not require the global node document

**Observed:** 2026-08-28

## Problem

The pinned ComfyUI runtime and curated node bundle became healthy, but both
managed workflow validations failed closed because the global `/object_info`
response exceeded the adapter's two-MiB metadata limit. Raising the limit would
make validation depend on every installed node's schema even though each
workflow declares a small exact required-node set.

The candidate descriptors also used model-family names as feature flags.
ComfyUI's `/features` contract reports API/runtime capabilities; model and
workflow compatibility are already checked independently through required
nodes and exact model inventories.

## Resolution

- Query the bounded `/object_info/{node_class}` endpoint for each explicitly
  required node and fail closed if any class is absent.
- Require the runtime's real `supports_model_type_tags` feature instead of
  synthetic model-family feature names.
- Preserve the existing global item-count and per-response byte bounds.

## Verification

- Twelve focused adapter, packaging, and workflow tests passed.
- Both live workflows reported ready with no missing features, nodes, or
  models; normal availability remained false for the declared policy gates.
- Both qualification-only compatibility checks passed before execution.
- The full repository gate remains part of the enclosing change.
