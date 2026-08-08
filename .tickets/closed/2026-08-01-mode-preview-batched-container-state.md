# Batch container-state discovery for operating-mode transitions

**Observed:** 2026-08-01

## Problem

`serves mode preview` took 96.2 seconds against the 50-entry Fakoli Dark
manifest set. The mode planner queried `docker inspect` once per declared GPU
workload, and the guarded `cmd_up` path repeated the same serial pattern. This
makes a read-only safety preview look hung and compounds the outage window of
an exclusive-mode transaction.

## Resolution

Discover all declared container states with one bounded `docker ps -a` query,
map missing names to `absent`, and fail every requested state closed when the
Docker query or JSON response is not authoritative. Reuse that snapshot in
mode planning and reservation admission, refreshing it after an eviction.

## Acceptance

- A mode preview performs one batch container-state query rather than one
  inspect per manifest row.
- Docker absence, daemon failure, and malformed output remain unresolved
  states and prevent mutation.
- Admission refreshes live state after eviction before starting the exclusive
  owner.
- Existing transactional rollback and model lifecycle tests remain green.
