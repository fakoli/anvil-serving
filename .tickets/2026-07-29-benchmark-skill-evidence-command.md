# Fix benchmark skill evidence inspection command

**Observed:** 2026-07-29

## Problem

The benchmark publication skill instructed operators to run
`anvil-serving eval evidence`, but the product exposes evidence inspection as
`anvil-serving eval benchmark evidence show`. The stale command failed during
the Agents-A1 Primary qualification.

## Resolution

Update the skill to name the real product command. Keep evidence inspection in
the CLI; do not add a skill-local workaround.

## Verification

- Inspect the protocol-v3 artifact with
  `python -m anvil_serving.cli eval benchmark evidence show <artifact> --json`.
- Validate `skills/anvil-serving-benchmark-docs` with the installed
  `skill-creator` validator.
