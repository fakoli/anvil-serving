# ADR-0042 — Anvil Serving is one umbrella with six explicit product families

- **Status:** Accepted
- **Date:** 2026-08-30
- **Relates to:** ADR-0028; ADR-0029; ADR-0039; ADR-0040; ADR-0041

## Context

Anvil Serving began as a serving and benchmark substrate with a thin direct
gateway. It now also owns qualified voice paths, bounded media workflows,
durable jobs and artifacts, topology-aware controller operations, host
utilities, client reconciliation, and fleet visibility. Those capabilities
share one package, topology, ownership model, confirmation contract, evidence
policy, and release line.

The public story did not keep up. Root help grouped Media as a generic
integration, the command manifest carried no product-family metadata, and
there was no installed-code path from a user goal to the correct journey. The
result made the router appear to be the whole product and made first-class
Voice and Media domains look incidental.

## Considered options

1. **Keep a router-centric product and describe every other surface as a
   utility.** Rejected because it contradicts shipped lifecycle, evidence,
   voice, media, and fleet authority.
2. **Spin Anvil Media and Anvil Voice into separate products and release
   lines.** Rejected because their execution, qualification, topology,
   controller, secrets, and artifacts depend on the same contracts. Splitting
   the brand would duplicate boundaries without creating technical isolation.
3. **Keep one umbrella with explicit product families and executable
   journeys.** Chosen.

## Decision

**Anvil Serving** is the umbrella product. Its stable product families are:

1. Model Serving;
2. Capability Gateway;
3. Evaluation & Evidence;
4. Anvil Voice;
5. Anvil Media; and
6. Control Plane & Fleet.

Anvil Voice and Anvil Media are first-class branded domains inside the
umbrella. They remain in the same repository, Python distribution, CLI,
topology, controller/MCP contract, documentation site, semantic version, and
release transaction.

The canonical family catalog is code-owned and stdlib-only. It records each
family's stable id, name, promise, boundary, root commands, documentation
anchor, and ordered journey. Every visible operational root command belongs
to exactly one family. Command-tree construction fails if coverage is missing,
duplicated, or stale.

`anvil-serving product families` and `product journey FAMILY` are bounded,
read-only projections of that catalog. The machine-readable command manifest
contains the same family catalog and records a `product_family` id on every
operational command. The docs explain richer workflows but may not redefine
the boundary.

Family attribution does not create implicit authority. A journey may hand off
from Model Serving to Evaluation & Evidence and back to guarded promotion, but
evidence still cannot mutate a serve. Media and Voice may use Control Plane &
Fleet dispatch, but protocol adapters still cannot select placement or bypass
the declared resource owner. The Capability Gateway remains direct-only with
no intent classifier or fallback.

## Consequences

- Root help and navigation begin with user goals and product families rather
  than internal plane names.
- Media receives a dedicated command reference covering discovery through
  qualification and artifact inspection.
- New operational roots require an explicit family assignment and journey
  impact review before the manifest can regenerate.
- A separate Anvil-branded distribution remains possible only after a future
  ADR proves a genuinely independent authority, state, compatibility, and
  release boundary.
- The first release of this enforced public contract is a semantic-versioning
  major release because automation-visible command-manifest schema and product
  categorization change together.
