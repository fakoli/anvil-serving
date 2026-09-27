# 2026-09-26 — pi-web manifest confirmation metadata vs documented gates

## Observation

`anvil-serving --command-manifest` reports `requires_confirmation: false` on
the option flags for the Pi Web lifecycle commands (`workbench
pi-web-install/up/down`), while the focused help and
`docs/WORKBENCH-PORTAL.md#pi-web` document that `install`/`down`/`up` are
gated behind an explicit `--confirm` flag and host authorization (sudo).

## Question

Should flag-gated mutations (`--confirm` as the gate itself) be marked in the
manifest (e.g. option-level `requires_confirmation: true` on `--confirm`, or
a command-level gate field), so machine-readable consumers do not have to
fall back to focused help to discover the gate?

## Impact

Agent-facing discovery surfaces (skill bodies, AGENTS.md pointers) that cite
the manifest's confirmation metadata could under-state the gate. Interim
wording in the workbench skill and AGENTS.md instructs agents to verify exact
gates in focused help and owning docs.

## Scope

Metadata/registry clarification only; no behavior change proposed here.
