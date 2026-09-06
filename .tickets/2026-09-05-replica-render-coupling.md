# Close the replica render and activation coupling before implementation

Status: design correction approved; implementation and activation proof pending
Priority: P1
Date: 2026-09-05
Tasks: qualified-replica-sets:T006, qualified-replica-sets:T012

## Ground truth

`anvil_serving/deploy.py::main` currently renders one model's Compose file,
appends one serve-manifest entry, and prints `render_tier_stub` for one direct
endpoint. It does not load a complete router configuration or topology.
`serves.py` separately owns router-config installation and promotion paths.
The PRD's instruction to validate replicas "during managed rendering" therefore
needs an explicit API/CLI coupling; adding an unused validator alone is not
proof that a managed mutation is guarded.

## Required resolution

Before dispatching T006, record and review the exact caller, input ownership,
return/error contract, and tests that prove topology validation precedes the
first write. Keep direct render output compatible and retain the prohibition
on automatically launching or promoting a multi-serve replica set. T012 must
call the same pure validator at every router-config activation entry point,
including rollback, and refuse single-serve lifecycle shortcuts before mutation.

The join remains offline and declaration-based: unique member resources,
existing host/runtime ownership, one shared host, non-null normalized-equal
endpoints, no DNS, no topology inference and no deployment attestation.

Independent review selected exact-byte validate-only snapshots instead of a
lossy TOML renderer. Approved T006 captures at most 1 MiB once and joins parsed
members to declared topology offline; T014 exposes the actual root command
`topology validate-router-config`. T012 refuses single-serve lifecycle paths
before mutation. T015 validates before the first status transition and passes
the captured bytes through installation without reopening or normalizing them.
T013 owns generated manifest/docs and whole-feature gates. Direct rendering
stays unchanged. Source parsing and explicit dependency checks passed; no
implementation or deployment is implied by this design approval.
