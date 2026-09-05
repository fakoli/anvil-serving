# Regenerate command metadata after replica lifecycle CLI changes

Status: fix scoped as qualified-replica-sets:T013.1; acceptance remains batched.

The integration run against source 7473b866 completed with 5800 passed,
10 skipped and one failure: test_manifest_is_checked_in_and_matches_deterministic_regeneration.
The source stayed unchanged during that run; later commits changed tickets only.

Comparing checked-in and generated manifest bytes shows five missing --config
option declarations for local mode/profile operations, plus the install-config
summary change from direct-only wording. Those command declarations are already
implemented. No runtime defect, unknown extra command or live outage is implied.

Regenerate only through commands.spec.write_manifest in an isolated worktree,
confirm the exact six-hunk delta, run command-tree tests and diff checks, then
retain the full-suite failure as historical integration evidence. T013 still
owns narrative documentation and final combined gates. No per-task formal
acceptance or deployment claim is made by this mechanical synchronization.
