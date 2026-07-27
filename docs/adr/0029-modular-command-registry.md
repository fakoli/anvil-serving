# ADR-0029 — Modular command registry with parser-owned leaf help

- **Status:** Accepted
- **Date:** 2026-07-26
- **Relates to:** ADR-0021; ADR-0028

## Context

The operator CLI had one 3,400-line `command_tree.py`. Its
`build_command_tree()` function repeated command options, examples,
configuration notes, and behavioral notes already owned by leaf parsers and
reference documentation. Adding or removing a capability required editing a
distant central function, and the duplicated prose made the command contract
harder to review and test.

ADR-0028 narrowed the product to serving, lifecycle, qualification, benchmark
evidence, and a thin direct gateway. Retaining an oversized intent-era command
catalog implementation would work against that simplification.

## Considered options

1. Keep the central builder and only split helper functions. This changes file
   size without removing duplicated representations.
2. Discover command modules dynamically. This reduces registration code but
   makes ordering and packaged behavior depend on filesystem discovery.
3. Decorate operational handlers directly and import them at registry startup.
   This colocates every fact, but makes ordinary help and manifest generation
   import modules that may own subprocess, filesystem, or network behavior.
4. Use decorated family factories, an explicit family list, lazy handler
   references, and parser-owned leaf help.

## Decision

Use option 4.

Command declarations live in cohesive modules under
`anvil_serving/commands/`. Each module exposes one `@command_family` factory.
The registry imports an explicit family list, rejects duplicate or missing
roots, applies deterministic public ordering, inherits documentation anchors,
and validates structural policy without resolving handlers.

The registry owns only facts needed before dispatch:

- public path and summary;
- handler reference and argument prefix;
- mutation and output policy;
- topology resource, runtime, transport, and recovery constraints;
- dispatcher-owned options; and
- typed controller-operation mappings.

Leaf parsers own detailed argument help. Reference pages own examples,
configuration precedence, and behavioral guidance. Command manifest schema v4
removes the duplicated `examples`, `configuration_notes`, and
`behavior_notes` fields.

`anvil_serving.command_tree` remains a small compatibility import surface.
Production code imports `anvil_serving.commands`.

## Consequences

- Adding or reviewing one command family no longer requires navigating a
  multi-thousand-line central builder.
- Help and manifest generation remain import-safe and deterministic.
- Parser details and authored documentation have one owner each.
- Manifest consumers must accept schema v4 and stop reading the removed prose
  fields.
- The removed `CLI-UX-AUDIT.json` ratchet is not reintroduced; current command
  contract tests, the CLI reference audit, and strict documentation gates cover
  the streamlined surface.
- Family-level decorators provide modular registration without hidden
  filesystem scanning or import-time operational side effects.
- The command contract remains independently testable through registry,
  manifest, CLI, topology, and MCP/controller consistency tests.
