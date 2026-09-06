# Remove inferred physical Fast/Heavy GPU roles from dashboard

Date: 2026-09-05
Status: confirmed source defect; implementation and consolidated acceptance pending

`observability/dashboard/static/index.html::resolveGpuRoles` inspects container
names for fast/heavy substrings, then sorts two cards by total memory and labels
them Fast and Heavy even without a declared assignment. `refreshCurves` displays
those inferred roles as physical tier groups. The existing dashboard test asserts
these labels, so the test currently preserves the outdated assumption.

This contradicts the current independent-lane topology and can mislabel two equal
cards or cards serving unrelated capabilities. Memory size and container naming
are observations, not routing or ownership authority.

Fix the display to group measured GPU series by their existing host/card identity,
using observed identity labels or a neutral Graphics card label. Preserve every
card, including equal-capacity cards and hosts with repeated local GPU indices;
never infer route, qualification, promotion or Fast/Heavy identity. Aggregate VRAM
may remain an explicitly aggregate metric, not a unified-memory capacity claim.
Keep shared graphics memory separate from dedicated per-card memory.

Add executable browser/JavaScript fixtures for two equal cards, distinct hosts,
missing identity and misleading container names. Update obsolete HTML assertions
to the neutral identity contract. Coordinate this static-file change after the
workload panel, not concurrently with another owner of the same asset.
