# ADR-0013 — OpenClaw layers and MCP control plane

- **Status:** Accepted
- **Date:** 2026-07-05
- **Relates to:** ADR-0001, ADR-0004, ADR-0005, ADR-0012,
  `docs/OPENCLAW-INTEGRATION-SPEC.md`, `plugins/openclaw-anvil-intent-router/`,
  `anvil_serving/harness.py`

## Context

anvil-serving now has three different OpenClaw-adjacent responsibilities:

1. Route each OpenClaw turn to the right intent preset.
2. Serve the model request through an anvil quality gate.
3. Manage the multi-machine deployment that makes the first two work: the OpenClaw gateway,
   the anvil router, the local model serves, and the harness-side config tying them together.

These responsibilities have different lifetimes and trust boundaries. The request path must stay
low-latency and reliable. The router must stay useful to non-OpenClaw harnesses. Operational control
often needs to cross machines: the OpenClaw gateway may run on one box, while the router and model
serves run on a Windows/WSL GPU host. The repo already has typed management verbs (`serves`,
`router`, `harness`) that reduce raw Docker/SSH usage, but an agent or OpenClaw session still needs
a structured way to invoke those operations safely.

OpenClaw also has an important live-confirmed caveat: once a `before_model_resolve` hook emits
`providerOverride:"anvil"`, OpenClaw's native fallback walk does not reliably escape that provider
on an anvil exhaustion response (ADR-0005). Therefore the OpenClaw hook cannot be treated as a full
quality-fallback system. It should influence the initial model/provider resolution only; verified
fallback remains an anvil router responsibility.

There is a fourth product need above those runtime layers: a repeatable operator playbook that can
use anvil-serving without deep model judgment. If the product exposes precise MCP tools and a skill
or lightweight agent knows the procedures, then a modest model can still execute useful workflows:
swap to a model suited for the current use case, run a requested model through preflight and
benchmarking, sync the OpenClaw harness config, and report the outcome. The intelligence should live
in the product contracts, schemas, and playbooks as much as possible, not only in the model driving
the tools.

## Considered options

1. **Make the whole product an OpenClaw plugin.** Rejected. This would couple the router's core
   product contract to a young, changing OpenClaw plugin API, and would make the router less useful
   to Claude Code, Codex, Aider, Continue, pi, and other protocol-compatible harnesses. It would
   also put serving, quality verification, Docker control, and remote host management into a
   gateway plugin runtime that should only need to adapt OpenClaw.

2. **Keep only the existing OpenClaw hook plugin and rely on manual CLI/runbooks for operations.**
   Rejected. ADR-0012 already establishes that raw operational tools are product gaps once they are
   required for normal use. Hand-editing OpenClaw config, manually restarting gateways, or reaching
   for raw Docker/SSH leaves the deployment easy to drift and hard for agents to inspect.

3. **Expose only an MCP server and drop the native OpenClaw hook plugin.** Rejected. MCP tools are
   good for explicit operations, not for per-turn `before_model_resolve` routing. A tool call cannot
   replace OpenClaw's model-resolution hook without changing the user's agent flow and introducing
   per-turn prompting overhead.

4. **Use three clean layers: an OpenClaw hook adapter, the anvil router data plane, and an MCP
   control plane.** Chosen. Each layer owns one job, can be tested independently, and can fail without
   corrupting the others.

## Decision

anvil-serving will present three clean integration layers around OpenClaw.

### Layer 1: OpenClaw intent adapter plugin

The native OpenClaw plugin lives in `plugins/openclaw-anvil-intent-router/`. It is a thin
`before_model_resolve` adapter. It classifies the current prompt and either:

- returns `{}` for cloud-preferred classes, leaving OpenClaw's native provider resolution alone; or
- returns `{ providerOverride: "anvil", modelOverride: "<bare preset>" }` for local-preferred
  classes, sending the turn to the anvil provider.

The plugin may call anvil's `POST /v1/route` endpoint when configured for authoritative routing,
but it must degrade to the local deterministic classifier on timeout or error. It must never break a
user run because classification, logging, or configuration failed.

The plugin does not verify model output, manage local serves, restart gateways, promote profiles, or
own cloud fallback. Those are not hook-plugin responsibilities.

**Product value:** OpenClaw gets per-turn intent routing with minimal latency, while anvil avoids
wasted local round-trips for known cloud-preferred work. OpenClaw API churn is isolated to one small
adapter package, not the router core.

### Layer 2: anvil router data plane

The router remains the product's request path: `anvil-serving serve` exposes the Anthropic Messages
and OpenAI-compatible front doors, resolves intent presets, filters/ranks tiers through policy,
verifies local output, runs fallback, streams responses, and writes the decision log.

The router core must remain protocol-standard and OpenClaw-free. Its inputs are HTTP requests and
router config; its outputs are normal model responses, `/v1/models`, `/v1/route`, health, and
decision records. OpenClaw can be the first-class beachhead without becoming a runtime dependency.

**Product value:** the quality gate stays reusable across harnesses. Measured profiles, residency,
verification, streaming commit windows, and fallback behavior are implemented once in the service
that actually sees the model response.

### Layer 3: MCP control plane

Add an `anvil-serving mcp` server as the structured operational surface. It should expose tools that
wrap the existing typed management verbs and their underlying library functions, for example:

- inspect router, serve, GPU, and gateway status;
- preview and apply `harness sync openclaw`;
- restart the OpenClaw gateway after config changes;
- run preflight or route-decision probes;
- manage serves and router lifecycle through explicit, gated operations;
- surface logs and recent decision summaries in bounded, machine-readable form.

The MCP server is a control plane, not the request data plane. It should not proxy model traffic or
replace `/v1/route`. It can run on the same machine as OpenClaw, on the GPU host, or behind SSH /
private-network transport. Its first implementation should be stdio so OpenClaw can launch it as a
local or SSH-backed MCP server; a token-authenticated HTTP transport can follow if remote operation
needs a long-lived service.

Mutating MCP tools must be narrow, explicit, and schema-driven. Read-only inspection should be easy;
state-changing tools should require exact targets and support dry-run or confirmation fields where
the underlying operation is disruptive.

**Product value:** agents can operate and reconcile the whole deployment without scraping human CLI
text or reaching for raw Docker/SSH. The product owns harness configuration, remote gateway reloads,
and serve/router lifecycle as first-class capabilities.

### Operational skill or agent on top of MCP

The MCP control plane should be designed for a higher-level anvil-serving skill or lightweight
operator agent. That skill is not another runtime layer; it is a guided consumer of the MCP tools and
the documented CLI/API contracts.

The skill should encode deterministic procedures such as:

- choose or swap a serve for a stated use case by inspecting model inventory, current GPU/serve
  state, router presets, and known profile decisions;
- run `preflight` and `benchmark` for a requested model, with the right base URL, model id, context
  probe, and sampling/thinking settings;
- compare benchmark/preflight output against the current profile and produce a promotion or
  no-promotion recommendation for a human gate;
- apply or preview OpenClaw harness sync after router/preset/model changes;
- recover from common operational states, such as a stopped serve, stale OpenClaw config, or a
  mismatched context window, by following bounded steps rather than open-ended troubleshooting.

The skill's value is that it lowers the reasoning burden on the model using it. A less capable model
can still perform a correct operation if the tool schemas, required inputs, safe defaults, and
verification steps are explicit. The model should mainly choose among documented playbooks and fill
tool arguments; it should not need to infer Anvil's operational contract from scratch.

**Product value:** anvil-serving becomes easier to operate as the model/serve matrix grows. The same
control plane supports expert manual use, OpenClaw sessions, and lightweight agentic automation,
while keeping promotion and destructive operations behind the product's existing human gates.

## Consequences

- The existing OpenClaw hook plugin remains the right place for per-turn intent adaptation, but it
  should be hardened to read OpenClaw plugin config as well as env vars, with env vars taking
  precedence for operator overrides.
- The router stays OpenClaw-free. Any future harness gets the same data-plane contract through HTTP
  without importing OpenClaw or the OpenClaw plugin SDK.
- The MCP server becomes the preferred agent-facing control surface. It should call structured
  Python helpers or JSON-capable CLI paths, not scrape human-oriented output.
- Management verbs that are useful through MCP need stable result dictionaries or `--json` modes.
  This is especially important for `serves status`, `router status`, `doctor`, `harness sync`,
  preflight probes, and bounded log reads.
- Remote control should reuse the safe transport choices already started in `harness.py`: explicit
  SSH/SCP targets, backups before writes, no secret literals in config, and no shell-script blobs
  where an argv list or a structured file transfer is possible.
- MCP tool descriptions, JSON schemas, and future skill docs are product surface. They should encode
  enough operational context that a model can execute Anvil workflows by selecting documented tools
  and checking returned status, not by improvising shell commands.
- Model swap and benchmarking workflows need deterministic, inspectable steps: inventory/discovery,
  capacity and GPU checks, serve start or swap, preflight, benchmark, profile comparison, and an
  explicit human promotion gate before router policy changes.
- ADR-0005 remains in force. The OpenClaw hook plugin must not promise native fallback recovery for
  local-preferred classes after it emits `providerOverride:"anvil"`. Durable recovery for those
  classes belongs inside the anvil router through a bound cloud tier, or by classifying them as
  cloud-preferred before they touch anvil.
- Native OpenClaw tool plugins are optional packaging, not the primary architecture. If ClawHub-style
  distribution later needs a native tool plugin, it should be a small wrapper around the MCP/control
  contract rather than a second implementation of serve/router/harness management.
