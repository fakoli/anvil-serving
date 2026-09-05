# Make recipe workload provenance match actual observations

Status: contract closed; T004.1 producer and T004 projection implementation pending.

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

Source anchors: serve_recipes.py load_registry, _recipe_container_record and
discover_recipe_containers; tests/test_recipe_container_discovery.py; canonical
observability/workloads.py validation and select_records. This is a known source
contract gap, not an observed live outage or evidence of model qualification.

The closed contract adds a bounded immutable configuration/runtime snapshot
before projection. Registry bytes and fixed metadata-only Docker capture are
capped before parsing; each successful component receives its own observation
time. Registry mtime remains configuration evidence, Docker lifecycle times
remain lifecycle evidence, and neither is relabeled as source collection time.
Exact semantic recipe digests reconcile configuration with observations;
validated full container IDs distinguish multiple observed containers. This
source emits at most observed-running and never healthy-identity.

Implementation remains open under T004.1 and T004. Per the current delivery
instruction, their focused gates establish implementation evidence only; final
adversarial review and acceptance are deferred to the consolidated batch pass.
