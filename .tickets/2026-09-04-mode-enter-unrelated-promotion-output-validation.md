# Mode entry validates unrelated promotion output directories before target scoping

**Status:** Open — diagnosed during a restoration dry-run; implementation deferred

## Problem

A target-scoped operating-mode plan can be blocked by a promotion that is
unrelated to both the explicitly selected exclusive target and its explicit
restore group. The failing command shape is:

```text
python -m anvil_serving.cli serves mode enter <target> \
  --restore-group <group> --manifest <operator-home>/serves.<selected>.toml \
  --dry-run
```

The selected manifest and restore group were sufficient to describe the
intended restoration plan. However, `mode` expands the explicit manifest to
every adjacent `serves*.toml`, then loads and validates every `[[promotion]]`
before transaction relevance is evaluated. An unrelated promotion gate whose
`json_out` parent directory did not exist therefore caused the dry-run to
return `bad promotion plan` instead of rendering the selected plan.

No container, route, or service mutation occurred in this failed dry-run. No
cross-cutting loader change was attempted while restoration was active.

## Confirmed code path

1. `main()` classifies every `mode` action as a manifest-set operation and
   calls `load_manifest_set()`.
2. `mode enter` then iterates every path returned by
   `manifest_set_paths(manifest_path)` and calls `load_promotions()` for each
   adjacent manifest.
3. `load_promotions()` calls `validate_write_target()` for every promotion
   gate `json_out`. A missing parent directory raises before
   `_preflight_gate()` and `_finding_is_relevant()` can classify the promotion
   as outside this transaction.

The safety gate already treats lint and rollback findings on unrelated serves
as advisory. Eager promotion loading currently bypasses that scoped contract.
Existing test
`test_mode_enter_first_promotion_load_reports_bad_plan` explicitly codifies the
older global behavior ("a bad plan anywhere in the manifest set must
surface") and will need to be split into involved and unrelated cases when the
fix is implemented.

## Required behavior

- An explicit `mode enter <target> --restore-group <group> --manifest <path>`
  must construct and validate the transition from the selected target, the
  named restore-group members, and only their required promotion/rollback
  dependencies.
- A missing `json_out` parent on an unrelated adjacent manifest must not block
  `mode enter` preview or `--dry-run`. It may be reported as an advisory, but
  the advisory path must not create the missing directory.
- Invalid promotion data that is required by the target or restore group must
  still fail closed before any mutation, including an invalid or unwritable
  `json_out` target.
- Whole-catalog validation surfaces such as `serves lint` and
  `serves rollback-check` must continue to report defects across the complete
  adjacent manifest set.
- Target scoping must not make GPU occupancy, duplicate serve identity, or
  rollback dependencies invisible. The implementation must separate catalog
  discovery from transaction-blocking promotion validation rather than simply
  ignoring adjacent manifests.
- `--dry-run` must remain read-only: no output-directory creation, Docker
  mutation, route change, or operator-state write while building the plan.

## Acceptance / regression tests

1. Create a hermetic manifest directory with a valid selected target and
   restore group plus an unrelated `serves.extra.toml` promotion whose
   `json_out` parent does not exist. Patch `cmd_mode` with a no-I/O spy. The
   explicit `mode enter ... --dry-run` returns success, reaches the spy once,
   and does not create the missing directory.
2. Put the same invalid `json_out` on a promotion required by the selected
   target or restore group. The plan returns nonzero before `cmd_mode`, Docker,
   router, or filesystem mutation.
3. Give the unrelated manifest a malformed serve identity, duplicate
   container/name, or reservation that affects transaction safety. Verify the
   relevant catalog/occupancy validation still fails closed; output-path
   scoping must not weaken those checks.
4. Run `serves lint` and `serves rollback-check` against the fixture catalog.
   Both whole-catalog surfaces still identify the unrelated bad promotion and
   its source manifest.
5. Pass an explicitly named manifest that does not match `serves*.toml` and
   verify that its target and restore group remain authoritative while truly
   unrelated adjacent promotion output paths cannot veto the plan.
6. Replace the current global bad-promotion test with two regressions: a bad
   involved promotion blocks, while a bad unrelated promotion is advisory and
   does not block.

## Implementation boundary

This likely requires separating promotion discovery/parsing from
transaction-specific validation, or adding a relevance-aware validation phase.
That is cross-cutting behavior shared by promotion, rollback-check, profile,
and mode dispatch. Implement and review it in an isolated change after the live
restoration completes; do not work around it by broadly skipping preflight
checks.
