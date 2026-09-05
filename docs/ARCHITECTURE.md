# Architecture

Anvil Serving is one umbrella product with six explicit authority domains:
Model Serving, Capability Gateway, Evaluation & Evidence, Anvil Voice, Anvil
Media, and Control Plane & Fleet. The gateway is an explicit capability
meta-router implemented as a thin request path; it is not the whole product
and does not perform semantic model selection.

## Product-family architecture

| Family | Runtime authority | Handoff |
| --- | --- | --- |
| Model Serving | Artifacts, recipes, manifests, lifecycle, and reservations | Presents a concrete endpoint for qualification. |
| Capability Gateway | Auth, exact route selection, protocol translation, readiness, admission, and relay | Exposes only a configured, reviewed capability. |
| Evaluation & Evidence | Functional gates, benchmarks, and durable evidence | Supports human review; never mutates serving state. |
| Anvil Voice | STT/TTS and realtime proxy lifecycle plus voice qualification | Uses declared owners and explicit audio routes. |
| Anvil Media | Named workflows, durable jobs, cancellation, qualification, and artifacts | Uses one declared worker and typed controller operations. |
| Control Plane & Fleet | Topology resolution, controller/MCP dispatch, host utilities, integrations, and fleet state | Preserves the selected resource owner's authority. |

The code-owned catalog and ordered journeys are documented in
[Product families](PRODUCT-FAMILIES.md). Each operational root command belongs
to one family, while a reviewed user journey can cross family boundaries
without transferring authority.

```mermaid
flowchart LR
    C["client or harness"] --> A["authenticated front door"]
    A --> D["dialect translation"]
    D --> R{"explicit capability alias"}
    R -->|"llm.primary"| H["configured primary-local tier"]
    R -->|"llm.secondary"| F["configured secondary-local tier"]
    R -->|"unknown"| X["404"]
    H --> HM["configured served metadata"]
    F --> FM["bounded metadata from selected service"]
    HM --> S["admission + SSE / normalized response"]
    FM --> S
    S --> L["metadata-only DecisionLog"]
```

## Request boundary

The router accepts Anthropic Messages, OpenAI Chat Completions, and the
supported stateless Responses subset. It authenticates the caller, translates
the request and tools for the selected upstream dialect, enforces the selected
tier's context/tool/readiness/admission constraints, then relays ordinary or
SSE responses. There is exactly one selected tier per accepted chat request.

**Pending design — bounded same-host replicas.** A future explicit 2–16-member
equivalent set may add one internal member-admission/selection step after this
singular alias-to-tier decision. All members must be on the declared host and
share served model, declared revision, engine version, image/configuration
digests, dialect, and context/output/tool/media contract; each is independently
health-checked and live-verified for its model name. The initial strategy is
deterministic round robin among eligible member IDs. It is not a cross-host
scheduler, hidden substitution, lifecycle authority, or replay mechanism:
after selection, a failure returns without another member attempt. Declared
deployment provenance is not runtime attestation, and readiness is distinct
from qualification and promotion. Until implementation ships, each tier has
one endpoint. See [ADR-0039](adr/0039-capability-meta-router.md) and
[ADR-0034](adr/0034-fleet-control-plane-and-node-runtime-classes.md).

The front door also has deterministic purpose-model endpoints for embeddings
and reranking, plus normalized audio endpoints. They have separate
operator-configured route tables and never join the chat route vocabulary.
The same authenticated origin may additionally expose MCP, A2A, and opaque
artifact routes. Those routes adapt to a durable operation service; they do
not enter the inference relay or gain authority over lifecycle or placement.

## Meta-router authority planes

The architecture separates identity, topology, served configuration, and
evidence so each mutable fact has one authority:

| Plane | Authority | Runtime effect |
| --- | --- | --- |
| Capability | Caller alias plus `[router.model_routes]` | Selects exactly one tier or returns 404 |
| Topology and policy | Operator-owned router configuration | Fixes endpoint, dialect, auth reference, readiness, and safety rules |
| Private network | Tailscale user/node identity, grants, MagicDNS, and Serve | Makes only approved device paths reachable; does not replace Anvil application auth or resource ownership |
| Served configuration | Router config or the selected inference service | Supplies model identity, context, and allowlisted runtime facts for admission and metadata |
| Request | Router | Authenticates, validates, admits, translates, streams, and relays to the selected endpoint |
| Media operation | Durable media service | Validates named workflows and owns idempotent jobs, cancellation, reconciliation, and artifact metadata |
| Protocol projection | MCP and A2A adapters | Authorizes and projects the operation service; never owns execution state |
| Remote operation | Controller | Dispatches allowlisted typed operations and records confirmation/lifecycle receipts |
| Resource execution | Declared resource-owning host | Owns managed ComfyUI lifecycle, GPU reservations, and backend execution |
| Evidence and promotion | Evaluation artifacts and guarded operator commands | Determines whether configuration should change; never selects per request |

In upstream-owned mode, the router asks only the endpoint selected by the
configured route. Metadata resolution is therefore downstream of route
selection. It cannot introduce a second candidate, fallback, or endpoint
choice. See [Capability meta-router](META-ROUTER.md).

For media work, operator configuration likewise maps one named workflow
version to one media service and resource owner. The gateway never accepts a
raw ComfyUI graph, proxies backend routes, or substitutes another workflow or
host. See [ADR-0040](adr/0040-media-gateway-and-controller-authority.md).

## Capability topology

A representative primary inference node exposes two equivalent GPUs as stable
UUID-backed Compute A and Compute B resources. Split mode admits compatible
workloads independently on either role. `dual-gpu-exclusive` mode drains and
stops every GPU inference competitor, then grants both roles to one explicitly
configured TP=2 serve. The router and controller may remain online, but an
alias whose backing serve is offline returns unavailable and never substitutes
the TP=2 model.

The broader multi-device example keeps a lightweight harness node model-free,
places the voice agent and STT/TTS on a voice/audio node, and places ComfyUI
plus an optional fast LLM on a media/burst node. Phones, tablets, and operator
computers join only as approved clients. These are roles, not public machine
identities or a claim about live operator state. See
[Private networking with Tailscale](TAILSCALE-NETWORKING.md) and
[Device topologies](DEVICE-TOPOLOGIES.md).

`serves` manages compose-backed model lifecycle and GPU reservations.
`eval preflight` and benchmark commands qualify a concrete endpoint. The
gateway can only expose a configured capability. It neither proves model
quality nor promotes a new recipe.

## Split-host controller

The resource-owning inference node owns the execution plane. Its dedicated
Linux controller image
contains Anvil Serving, the pinned Docker CLI and Compose plugin, and the
NVIDIA runtime view. It receives only the Docker socket, declared serving
manifests, and a durable operation-state volume. Host loopback URLs in those
manifests are rewritten to the explicit `host.docker.internal` alias inside
the container; ordinary native and router-container behavior is unchanged.

The harness node owns the client plane. `anvil-serving mcp serve` is the
model-free stdio bridge that authenticates to the resource owner's controller
through host-owned Tailscale Serve. The packaged bridge uses the official
TypeScript MCP SDK:
its client-facing side negotiates either the initialize era through
`2025-11-25` or stateless `2026-07-28`, while its downstream client is pinned
to `2026-07-28`. OpenClaw can therefore launch it with its initialize-based
SDK without adding a legacy listener to the resource owner. The controller is
published on the owning host's loopback only, so neither the container port
nor Docker socket
is directly reachable from the tailnet.

```mermaid
flowchart LR
    O["Legacy or modern MCP client on harness node"] --> P["TypeScript SDK stdio bridge"]
    P -->|"MCP 2026 only"| T["Tailscale Serve /anvil-controller on resource owner"]
    T --> C["controller container on 127.0.0.1:8765"]
    C --> D["Docker Desktop socket"]
    C --> H["resource-owner host endpoints"]
    D --> S["router and declared serves"]
```

The controller runs non-root with a read-only root filesystem, dropped Linux
capabilities, and an explicit operation allowlist. Docker-socket membership is
still host-equivalent authority over Docker, so the token, loopback publish,
tailnet ACL, restricted tool catalog, and absence of home/SSH/GitHub mounts are
the actual security boundary. Git and SSH credentials are deliberately not
available in the image.

## Durability model

Every class of runtime state has exactly one authoritative home (ADR-0033). Secrets are
file-backed references in the operator home; the process environment overrides but is never
the only copy. Desired state is git-tracked operator config, with the `anvil-router-cfg`
volume holding the installed copy mutated only through the promote pipeline. The controller's
operation ledger lives on its durable volume and is reconciled at boot: an operation
interrupted by a crash is marked failed with a typed error, never silently re-executed.
Operator intent (tier quiescence) may be persisted opt-in on a router state volume;
readmission always re-passes the fail-closed health and identity gate. Docker itself is the
supervision ledger — no pidfiles, no registry service, no self-healing daemon. Everything
else — availability results, admission counters, in-flight requests — is process memory, and
restart is the reset.

Both long-lived servers drain gracefully on SIGTERM within a bounded budget before Docker's
`stop_grace_period` expires, which is what makes restart-as-reload a legitimate mechanism.

## Operator command architecture

The public CLI is assembled from explicit command families under
`anvil_serving/commands/`. Each family module owns the paths, dispatch target,
safety class, topology metadata, and controller-operation mapping for one
cohesive area such as `serves`, `models`, or `voice`.

The `@command_family` decorator attaches the root-help category to a
module-local factory. `commands/registry.py` imports an explicit family list,
validates it, and orders the public roots deterministically. It does not scan
the filesystem or import operational handlers. Handlers remain lazy and are
imported only when dispatch or explicit validation resolves them.

```mermaid
flowchart LR
    F["command family modules"] --> R["deterministic registry"]
    R --> C["CLI resolution and safety policy"]
    R --> M["v6 command + product manifest"]
    R --> T["topology and controller contracts"]
    C --> H["lazy command handler"]
```

The registry joins machine-relevant command facts to the code-owned product
catalog. Leaf `argparse` parsers own detailed argument help, while the family documentation owns
workflows, examples, configuration precedence, and behavioral guidance. The
v6 manifest intentionally omits prose copies of that documentation while
carrying stable family ids, promises, boundaries, commands, and docs anchors.

## Evaluation and control-plane composition

The benchmark and control-plane public modules are stable compatibility
facades. Responsibilities live in directed internal packages so callers keep
their supported imports while persistence, security, protocol, and tool-family
code remain independently reviewable.

```mermaid
flowchart LR
    B["benchmark.py facade"] --> BP["benchmarking package"]
    BP --> BA["artifacts and specs"]
    BP --> BR["requests, evaluation, and runner"]

    C["controller.py facade"] --> CP["control_plane/controller"]
    CP --> CS["security and store"]
    CP --> CH["catalog, HTTP, server, and CLI"]
    CH --> M["public mcp.py facade"]

    M --> MF["control_plane/mcp foundations"]
    M --> MT["explicit ordered tool families"]
    MT --> MF
    MF --> D["direct dictionary dispatch"]
```

`anvil_serving/benchmark.py`, `anvil_serving/controller.py`, and
`anvil_serving/mcp.py` preserve their documented imports, command entrypoints,
and compatibility trampolines. Internal modules do not scan the filesystem,
load entry points, or dynamically discover handlers. The MCP catalog is built
once from an explicit family tuple, rejects duplicate family and tool names,
and dispatches through one dictionary lookup.

Benchmark artifact validation is owned by `benchmarking/artifacts.py`. MCP
adapts its domain errors to the MCP error contract rather than carrying a
second path-validation implementation. Controller internals consume the public
MCP catalog/call surface, while MCP foundations and tool families do not import
controller internals.

The resource-owner controller uses the `2026-07-28` stateless request contract
exclusively. `server/discover`, `tools/list`, and `tools/call` are the only
JSON-RPC methods served at `/mcp`; `initialize` is intentionally absent.
Request metadata and the matching `MCP-Protocol-Version`, `Mcp-Method`, and
conditional `Mcp-Name` HTTP headers are validated before dispatch. The
harness-side bridge is the only dual-era boundary. The SDK pins one era per stdio
connection, converts both client eras to a modern authenticated controller
client, validates the controller identity and dynamic tool schemas, and
returns the result in the caller's negotiated wire format.

## Deliberate non-components

The direct inference relay has no workload classifier, intent presets,
quality-profile router, policy engine, residency selector, verification chain,
circuit-breaker fallback, cloud route, or routing calibration loop. A selected
tier that cannot serve returns an error rather than changing the request's
capability. Named media workflows may expose explicit caller-selected quality
profiles, but each profile is only a locked parameter set inside the same
workflow; it cannot select another route, model, host, or backend. There is
likewise no fleet registry service and no background reconciler daemon: the
topology file is the registry, and Docker restart policy is the supervisor.

## Evidence and observability

`DecisionLog` records routing metadata without request/response content:
normalized alias, selected tier, timing, token counters where available, and
terminal outcome. Records carry a creation timestamp and can be persisted
opt-in to a bounded, append-only JSONL sink on the router state volume;
aggregate views remain snapshots of the in-memory buffer, not historical
windows. The controller's audit stream can likewise tee to a bounded file on
its state volume. All of it is audit evidence, not a feedback signal for
future routing. Benchmark results and preflight artifacts remain the source of
truth for changes to the configured mapping.
