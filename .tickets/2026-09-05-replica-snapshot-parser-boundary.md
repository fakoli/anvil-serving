# Bound replica snapshot errors and resource ownership

Status: source correction verified; activation integration remains open.
Task: qualified-replica-sets:T006

## Reproduced review findings

An independently reviewed candidate passed 65 focused tests but allowed a
small deeply nested TOML array to escape managed snapshot validation as a
raw `RecursionError`. A 5,000-digit integer similarly escaped as `ValueError`.
Both are within the 1 MiB byte limit; limiting file size alone does not bound
parser behavior or normalize failures. Synthetic local-file probes reproduced
both without touching topology or live services.

The same review found that two replica tiers could reuse the same declared
topology resources. Per-tier uniqueness was insufficient to establish one
replica admission owner per resource. Separate tier-owned counters could then
represent one physical endpoint as independent capacity twice.

## Correction and verification

The managed snapshot boundary maps ordinary parser exceptions to fixed
`router_config_invalid` outside the exception handler, retaining neither
raw exception text nor cause/context. Lower-level path/bytes parser behavior
remains compatible. Resource ownership is checked across all replica tiers;
member IDs remain tier-local, but a repeated resource is rejected as
`replica_resource_reused`. Validation stays offline and declaration-only.

Regressions exercise both malformed files through the actual snapshot loader
and a two-tier reused-resource configuration. Independent corrective review
reran the original probes: all produce the expected fixed context-free code.
The config/snapshot suite now passes 68 tests; Ruff and diff checks pass.

This ticket does not claim CLI validation, installation of captured bytes,
live served identity, qualification, or deployment. Those remain separate
tasks. Exact-byte capture, bounded reading, safe projections and topology
joins must be preserved by their callers.
