# Make recipe workload provenance match actual observations

Status: open design clarification for workload-visibility:T004.

The existing recipe inventory's served_identity comes from configured container
arguments, not a live exact-model response. Inventory has no source observation
timestamp. A workload projection must not upgrade that label or collection time
into healthy-identity or fresh runtime evidence.

Before implementing T004, close the producer contract for bounded timestamped
status snapshots, strict state types, configured-versus-observed reconciliation,
and stable opaque owner identity. Reuse managed status operations; do not add a
raw Docker fallback. Bound registry and status output before materialization,
not only after parsing a potentially unbounded response. If no authoritative
runtime identity exists, retain observed-running and explicitly document that
this source cannot emit healthy-identity. Add synthetic provenance, stale,
malformed, overflow and privacy regressions without reading real operator data.

Source anchors: serve_recipes.py load_registry, _project_recipe_container and
discover_recipe_containers; tests/test_recipe_container_discovery.py; canonical
observability/workloads.py validation and select_records. This is a known source
contract gap, not an observed live outage or evidence of model qualification.
