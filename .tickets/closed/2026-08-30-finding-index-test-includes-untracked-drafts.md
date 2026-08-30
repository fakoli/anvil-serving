# Findings-index regression includes untracked local drafts

**Status:** Resolved 2026-08-30

## Problem

The new findings-index completeness regression globbed every top-level
Markdown file in the worktree. An untracked author draft could therefore fail
the public evidence gate even though the repository contract and link checker
scope publication requirements to Git-tracked files.

An independent GPT-5.5/xhigh adversarial re-review found the defect on the
`1.0.0` candidate at revision
`481057c04d0d80a5eac2752e10078c4e54e866cb`.

## Acceptance

- Index completeness is computed from the bounded Git-tracked path inventory.
- Tracked top-level findings remain required in the chronological index.
- An untracked top-level draft does not change the gate result.

## Resolution

The regression now filters the existing bounded `git ls-files` inventory to
top-level findings, and a temporary-repository case proves an untracked draft
is ignored.
