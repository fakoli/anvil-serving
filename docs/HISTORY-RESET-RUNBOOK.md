# Repository History Reset Runbook

This is a plan for a future, separately approved migration. It does **not**
authorize deleting a repository, force-pushing, rewriting history, or changing
the live deployment.

Use it only when the current public snapshot is clean and the team has chosen
between a filtered rewrite and a new-root repository recreation.

## Preconditions

- Freeze writes and record the exact default-branch commit, tags, releases,
  open pull requests, and deployment revision.
- Rotate or revoke exposed credentials first. History cleanup is not rotation.
- Pass the current-snapshot semantic scan and pinned Gitleaks scan.
- Choose a private backup location outside both the public and operator repos.

## Create and prove the backup

1. Create a bare mirror clone and a Git bundle containing every ref.
2. Record SHA-256 digests, byte sizes, creation time, source revision, and the
   restore command in a private recovery note.
3. Restore the bundle into a temporary directory and verify branches, tags,
   commit counts, and representative historical objects.
4. Keep at least two independent private copies before any destructive step.

## Choose the migration

**Filtered rewrite:** use a pinned `git-filter-repo` version and a reviewed,
path/value-specific replacement plan. Preserve unrelated history. Compare the
old/new ref map and run a full-history secret scan on the result.

**New-root recreation:** create a new repository from the sanitized current
tree, record the old-to-new release/revision mapping privately, recreate only
the tags and release metadata intentionally retained, and document that commit
identity changed.

## Explicit execution gate

Stop here. Present the verified backup, scan results, chosen migration, exact
repositories/refs affected, collaborator impact, and rollback procedure. Obtain
explicit approval before deleting, force-pushing, transferring names, changing
repository visibility, or replacing the default branch.

## Post-migration verification

- Clone the public repository into a fresh directory with no local excludes.
- Run the semantic snapshot scan, pinned Gitleaks scan, full test gate, strict
  documentation build, and package/wheel smoke checks.
- Verify releases, documentation links, issue/PR references, branch protection,
  CI secrets, and deployment revision mapping.
- Notify collaborators that old clones must be archived and freshly cloned.
- Retain the private mirror and bundle until the new repository has completed
  the agreed observation period.
