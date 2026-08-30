# Product-family journeys are not enforced across the public surface

**Status:** Resolved 2026-08-30

## Problem

Anvil Serving already ships distinct model-serving, routing, evaluation,
voice, media, and fleet/control-plane capabilities, but the product boundary
exists only in prose fragments. The root CLI groups commands by implementation
area, the machine-readable manifest has no product-family metadata, and there
is no command that tells a user which journey to follow.

The gap is most visible in Anvil Media: its workflow, job, artifact, worker,
qualification, CLI, and MCP surfaces are implemented, yet every `media`
command links to the broad `docs/CLI.md#media` section and root help labels it
as a generic control-plane integration. That understates a first-class product
family and leaves its lifecycle boundary unclear.

## Required behavior

1. Keep Anvil Serving as the umbrella product and declare one canonical,
   code-owned map for Model Serving, Capability Gateway, Evaluation & Evidence,
   Anvil Voice, Anvil Media, and Control Plane & Fleet.
2. Give every visible operational root command exactly one family and fail
   validation if a command is missing, duplicated, or assigned to an unknown
   family.
3. Expose bounded human and machine-readable family and journey discovery from
   the CLI without changing serving, routing, or operator state.
4. Publish a dedicated Media command reference and keep every family boundary,
   starting point, ordered journey, and non-goal consistent across README,
   docs navigation, architecture, command help, and package metadata.

## Acceptance

- `anvil-serving product families` lists all six families and their boundaries.
- `anvil-serving product journey FAMILY` returns ordered, executable CLI steps
  and the expected outcome for that family.
- The command manifest includes the canonical family catalog and a family id
  for every visible operational command.
- A hermetic test proves the family catalog covers the complete public root
  surface exactly once.
- Media commands link to a dedicated page containing capability discovery,
  workflow, job, artifact, worker, and qualification journeys.
- Root help, README, docs home/getting-started/architecture, CLI reference, and
  package description use the same umbrella and family story.

## Resolution

The `product_families` catalog is now the code-owned authority for the six
public families, their boundaries, root commands, documentation anchors, and
ordered user journeys. The command registry validates complete and exclusive
root-command coverage, command-manifest schema 6 publishes the same catalog,
and `anvil-serving product families` / `product journey` expose it in both
human and global JSON modes.

Anvil Media now has a dedicated command reference and first-class help group.
The README, documentation home, getting-started path, architecture,
terminology, CLI reference, package metadata, and ADR-0042 all use the same
umbrella/family contract. The focused release regression passed 531 tests;
strict MkDocs, tracked Markdown links, the full CLI-reference audit, Ruff, and
patch hygiene also passed on the staged candidate.

Candidate review additionally found that the Media inventory/staging journey
and examples omitted the commands' required workflow id. They now include
`<WORKFLOW>`, and a regression verifies every catalog journey step begins with
a visible canonical command path.
