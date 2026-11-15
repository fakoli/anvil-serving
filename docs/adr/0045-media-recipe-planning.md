# ADR-0045 — Media recipes compile briefs into bounded execution requests

- **Status:** Accepted
- **Date:** 2026-10-27
- **Relates to:** [ADR-0040](0040-media-gateway-and-controller-authority.md),
  [ADR-0042](0042-anvil-serving-product-family-boundary.md),
  [Media commands](../cli/media.md)

## Context

Creative requests vary across harnesses, while a worker executes a concrete
graph with specific conditioning and resource limits. A generic prompt cannot
provide a missing image, speaker mapping or negative-conditioning path. UI
workflow files also differ from the API graphs accepted by the worker.

Anvil already owns immutable named graphs, declared bindings, quality profiles,
principal-scoped artifacts and durable jobs. Moving node selection into an
agent or creating a second orchestrator would duplicate those boundaries.

## Considered options

- Let each harness construct graphs. This makes authority and behavior depend
  on the client and bypasses the named-workflow boundary.
- Add a server LLM that interprets every creative request. This adds inference,
  nondeterminism and another resource owner before the execution contract is
  established.
- Compile an explicit structured brief through the selected operator recipe,
  then use existing execution operations. This reuses the current authority.

## Decision

Keep one product, package and release, with Media owning its planning,
workflow, job and artifact modules. CLI and MCP remain thin projections.
Harnesses interpret intent; a server-owned literal template compiles supported
shot fields into declared inputs. Preserve dialogue verbatim and reject
unsupported fields. A semantic LLM planner can be considered only after a
measured requirement for unrestricted server-side interpretation; it would
still have to satisfy this compiler.

Preparation returns exact run arguments and a digest covering the owner,
complete execution descriptor, rendered graph, resolved inputs and request
key. Submission rechecks that binding, owned references and normal admission.
Approval/retry reuses the compiled request rather than regenerating it.
Preparation does not establish backend readiness or submit a job.

Keep a bounded recipe catalog in the existing registry. Source discovery and
static checks are distinct from readiness. A ready catalog entry must resolve
to an available registered workflow of the same category. UI imports require
reviewed API export and local qualification. Neither the catalog nor the
compiler accepts arbitrary graphs, paths, model selections or installation
requests from the caller.

References have explicit declared roles. First-frame guidance, motion transfer,
identity replacement, speaker conditioning and style control are different
capabilities. Multiple people require explicit subject-to-reference-to-target
mapping; an input list does not imply identity tracking. New input modalities
must add bounded owned ingress, decoder checks and staging together before a
recipe is advertised as ready. Original audio/timeline retention and sequential
resource release require their own acceptance evidence.

## Consequences

Known recipes behave consistently across harnesses and preserve the existing
lifecycle, qualification, idempotency and cancellation gates. Direct legacy
submissions remain compatible. Optional prepared requests fail when their
contract or inputs change. Model-specific recipes and deployment selections
stay in private operator state; public examples remain synthetic.

Transport status alone does not resume an idle agent conversation. A harness
adapter observes the same durable job and delivers completion through the
harness's supported background mechanism. Session restart reattaches that
observer rather than submitting another render.
