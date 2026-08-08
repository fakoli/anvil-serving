# Explicit Ruff rule scope

## Problem

CI temporarily pinned Ruff 0.15.20 after Ruff 0.16.0 expanded its default rule
set and exposed 2,975 pre-existing findings on current `main`. The repository
did not declare the lint families it intended to enforce, so an unpinned
tooling update could turn the required lint gate red without a source change.

## Decision

- Select `E4`, `E7`, `E9`, and `F` explicitly in `pyproject.toml`, matching the
  pre-0.16 default policy the repository already passed.
- Preserve the existing `E701` and `E702` ignores.
- Remove the emergency `ruff==0.15.20` CI pin. Ruff versions may advance
  without inheriting unrelated newly promoted default families; changes within
  the selected families can still affect the gate and require review.
- Make the required CI gate name the root `pyproject.toml` explicitly so a
  missing canonical configuration fails immediately.
- Do not bulk-migrate unrelated code to Ruff 0.16's newly promoted rules.

## Changes

- Added the explicit lint selection to the canonical Ruff configuration.
- Restored CI's unpinned Ruff install and documented why that is safe.
- Made CI fail closed when the named root Ruff configuration is missing.
- Recorded the CI-policy change under the changelog's Unreleased section.
- After `origin/main` advanced to the 0.15.0 release during verification, the
  initial rebase placed the changelog bullet under the new release section.
  The post-rebase diff review caught and moved it back to `[Unreleased]` before
  review or publication.

## Verification

- Before the change, Ruff 0.15.20 passed and Ruff 0.16.0 failed with 2,975
  findings on the same `origin/main` tree.
- Ruff 0.15.20 and the current unpinned Ruff 0.16.0 both pass the edited tree.
- Full repository suite: 3,137 passed, 1 skipped.
- Full CLI reference audit: 481 files, zero violations; inventory and generated
  artifacts current.
- Strict MkDocs build and tracked Markdown relative-link validation: passed.
- Source distribution and wheel build, Twine metadata checks, and clean-wheel
  smoke: passed.

## Review

- Reviewed commit `92e9711cf8f04ce78e512698617b2b294e2db02b`
  independently through line-by-line, removed-behavior, cross-file,
  conventions, reuse, simplification, efficiency, altitude, and documentation
  passes.
- The reviewers independently reproduced the 2,975-finding Ruff 0.16.0
  baseline and confirmed that both tested versions resolve the same
  57 selected rule IDs. `F401` remains enforced, `UP031` remains excluded, and
  `E701` remains ignored.
- No implementation defect was found. Review corrected the ticket's overly
  broad version-stability wording and required explicit final dispositions.

## Adversarial review

- Recall-mode review of commit
  `92e9711cf8f04ce78e512698617b2b294e2db02b` added one hardening fix: CI now
  names the root Ruff configuration explicitly, and a missing named config
  fails with exit 2.
- Malformed Python and invalid rule selectors fail non-zero; Ruff installation
  and lint failures stop CI; the CI job timeout bounds resource exhaustion.
- A real nested-config probe confirmed that Ruff still honors a nested
  `pyproject.toml` with the explicit root option. The repository has no nested
  Ruff config today; this discovery behavior is retained and was not
  misrepresented as closed.
- Future Ruff releases can still add or change rules inside `E4`, `E7`, `E9`,
  or `F`. This residual state-drift risk is retained deliberately because issue
  #298 chooses a family-level policy and requires lifting the emergency pin;
  exact rule enumeration would not freeze rule semantics. Such a CI change
  remains a deliberate review event rather than an unrelated default-family
  expansion.
