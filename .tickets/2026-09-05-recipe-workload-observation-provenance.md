# Make recipe workload provenance match actual observations

Status: producer and projection implemented; consolidated acceptance pending.

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

Producer T004.1 implementation 76a190b6 passed 89 post-commit focused tests and
Ruff, recorded as EV717FC6C9. Projection T004 implementation 7e7ab1b0 passed
113 post-commit focused tests and Ruff, recorded as EV99362599. Both are locally
integrated source candidates. These gates establish implementation evidence
only; final review and acceptance are deferred to the consolidated batch pass.

Implementation framing clarification: use direct argv, never shell strings:
`docker ps -a --no-trunc --filter label=io.anvil-serving.managed-by=models-recipes --format {{.ID}}`,
then `docker inspect --type container --format <fixed-template> <validated-ids>`.
The fixed template emits one JSON object per result, with exactly id,
managed_by, recipe_digest, created_at, status, running, started_at and
finished_at. Each value uses Docker's json template helper. Read only the two
named management/digest labels and the lifecycle fields, not the containing
Config or State object. Require requested full IDs, reject duplicate keys and
unsolicited/duplicate IDs, and retain partiality for missing results. A missing
digest (empty string or null) suppresses no configured record later.

The first 256 unique valid list IDs are inspected; malformed/overflow list data
keeps unknown omissions. Empty successful listing is complete and performs no
inspection. Fixtures use escaped JSON lines with LF/CRLF terminators, not an
unescaped field delimiter. This closes the executor's framing question without
altering the public workload schema or legacy recipe inventory.

The [Docker formatting contract](https://docs.docker.com/engine/cli/formatting/)
defines the json helper; the [inspect reference](https://docs.docker.com/reference/cli/docker/inspect/)
defines per-result templates and explicit container type. These are source
format references, not evidence of a local runtime probe.
