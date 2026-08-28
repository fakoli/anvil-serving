# ADR-0040 — One media gateway origin with separated operation authority

- **Status:** Accepted
- **Date:** 2026-08-27
- **Amends:** ADR-0019 and ADR-0028
- **Relates to:** ADR-0030; ADR-0031; ADR-0034; ADR-0039

## Context

Anvil Serving's public inference listener is deliberately small: an explicit
chat alias selects one tier, and the router authenticates, validates, admits,
translates, and relays that request. The controller separately exposes typed,
durable operations, while resource-owning hosts execute managed lifecycle
changes. ComfyUI is already a managed on-demand workload, but exposing its raw
queue, workflow graph, filenames, and WebSocket as an agent API would collapse
those boundaries and make private deployment details part of the product
contract.

Agents need one authenticated origin for ordinary inference, MCP tools, A2A
tasks, and media artifacts. Long-running generation also needs durable state,
idempotency, cancellation, artifact retention, and restart reconciliation that
do not belong in the direct inference relay.

## Considered options

1. **Publish ComfyUI directly to agents.** Rejected. Raw workflow JSON, node
   installation, backend paths, and queue control are not bounded agent
   capabilities.
2. **Put media generation behind a chat alias.** Rejected. It would infer or
   overload capability selection and weaken ADR-0028/0039's one-alias,
   one-tier, no-fallback rule.
3. **Give the protocol listener lifecycle and placement authority.** Rejected.
   It would bypass typed controller operations, confirmation receipts, the GPU
   reservation ledger, and resource-owner execution.
4. **Expose one origin with protocol-specific adapters over a shared operation
   service.** Chosen.

## Decision

The router deployment becomes the externally visible **Anvil Gateway**. One
authenticated origin may serve four explicit surfaces:

| Surface | Owner | Authority |
| --- | --- | --- |
| `/v1/*` | inference router | existing exact alias resolution, dialect translation, admission, readiness, relay, SSE, and `DecisionLog` |
| `/mcp` | MCP adapter | scoped discovery and calls into the typed operation service |
| `/.well-known/agent-card.json` and `/a2a` | A2A adapter | secret-free discovery plus task submission, polling, streaming, and cancellation projections |
| `/artifacts/{opaque-id}` | artifact boundary | authenticated, principal-scoped, bounded media delivery |

The media operation service owns named workflow validation, durable jobs,
idempotency, ordered transitions, cancellation decisions, restart
reconciliation, and artifact metadata. MCP and A2A are projections of that
same service; neither owns execution correctness or protocol-specific job
state.

The controller owns typed remote dispatch, durable operation receipts,
confirmation enforcement, and operation allowlists. The selected
resource-owning host owns managed ComfyUI lifecycle, the host-local GPU
reservation ledger, and backend execution. Operator configuration maps one
workflow version to exactly one declared media service and resource owner.
There is no inferred host, workflow, model, provider, or fallback list.

Gateway authentication happens before protocol dispatch. Media credentials
carry explicit read, submit, cancel, cross-principal, or operator scopes.
Operator lifecycle tools are not included in a media-only catalog. Credential
values remain environment referenced and never enter records or client-visible
configuration.

Accepted media work is durable and survives client disconnects. A gateway or
controller restart may reconcile an existing ComfyUI prompt from bounded
history; it must not silently resubmit, replace, or start work. When a request
would start or stop a serve, evict a workload, alter a reservation, or install
state, the job remains `awaiting_approval` with an exact preview unless an
already-reviewed policy and existing confirmation contract authorize it.

ADR-0019 is amended only for the product boundary: `/comfyui` remains an
optional operator UI route, but it is not the agent API. The gateway's MCP,
A2A, and artifact surfaces are protocol handlers, not a raw reverse proxy.
ADR-0028 and ADR-0039 remain unchanged for `/v1`: the new media capability
family is explicit, deterministic, and separate from chat aliases.

## Prohibited behavior

- accepting caller-supplied ComfyUI graphs, node names, model paths, Python,
  shell, installation requests, or arbitrary backend routes;
- raw proxying of ComfyUI's API, UI, WebSocket, filenames, or filesystem paths
  through MCP, A2A, or artifact URLs;
- hidden retry or fallback to another workflow, host, model, endpoint, agent,
  or cloud provider;
- remote Docker-socket access or unrecorded lifecycle commands;
- protocol-layer selection of GPU placement, model residency, checkpoint,
  engine, or quantization; and
- treating readiness, transport success, or artifact decoding as perceptual
  quality or promotion evidence.

## Consequences

The gateway remains stdlib-only and keeps long-running operation handling out
of the inference relay. Existing `/v1` behavior must pass byte-compatibility
fixtures whenever media surfaces are enabled. A backend adapter may be
replaced without changing the workflow, job, MCP, A2A, or artifact contracts;
backend-specific routes and response bodies stop at the adapter boundary.

The shared origin has a larger authentication and dispatch surface, so route
classification, scope checks, bounded parsing, artifact ownership, and
cross-protocol parity require explicit tests. Live enablement remains a
separate human-required deployment transaction after package, controller,
bridge, worker, workflow, image, and rollback evidence agree.
