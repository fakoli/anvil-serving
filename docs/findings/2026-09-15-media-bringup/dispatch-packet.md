# Campaign dispatch packet

- **Campaign ID:** `2026-09-15-media-bringup`
- **Task ID:** `publication-finalization`
- **Stage and gate:** Publication; inspect final native video artifact before
  publishing a completed evidence set.
- **Objective:** Publish the bounded functional bringup without changing serve,
  route, alias, or workflow availability.
- **Owned outputs:** This evidence directory, dated finding, catalog and
  dossier links, and topology-dispatch ticket.
- **Authority:** Repository documentation writes only. Lifecycle and topology
  configuration mutations are forbidden.
- **Stop conditions:** Missing or contradictory native artifact identity,
  unsafe artifact path, or any implication of a promotion.
- **Verification:** JSON parse, artifact-set finalizer, and path-scoped Markdown
  link validation.
- **Return contract:** Sanitized evidence paths, bounded facts, missing data,
  ticket disposition, and validation result.
