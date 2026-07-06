# ADR-0014 — Tailnet controller transport for split-host OpenClaw deployments

- **Status:** Accepted
- **Date:** 2026-07-05
- **Relates to:** ADR-0004, ADR-0012, ADR-0013,
  `anvil_serving/mcp.py`, `anvil_serving/harness.py`,
  `plugins/openclaw-anvil-intent-router/`

## Context

ADR-0013 establishes an MCP control plane for anvil-serving operations. The local
transport is a stdio MCP server, which works when the caller can launch the
`anvil-serving` CLI on the same machine that owns the router, model serves, and
management verbs.

The current reference OpenClaw deployment is split across machines:

- `fakoli-mini` runs the OpenClaw gateway and the OpenClaw plugin runtime.
- The anvil-serving utility, router management, serve management, and GPU-local
  model operations may live on another host, such as `fakoli-dark` or a Windows/WSL
  GPU workstation.
- The OpenClaw gateway host still needs structured access to anvil-serving operations:
  route probes, preflight and benchmark probes, serve/router status, rendered
  OpenClaw config, and safe lifecycle actions.

These host names are examples, not product roles. Additional laptops,
workstations, or small edge hosts can take the same gateway, router, serve,
voice, or controller roles when they are reachable over Tailscale or another
private or direct network path.

If the only transport is local stdio, the gateway host must either install the full
anvil-serving CLI stack locally or shell across machines for every operation. That
reintroduces the raw SSH/shell coupling ADR-0012 and ADR-0013 are trying to reduce.

The machines are expected to be reachable over Tailscale. The tailnet gives us a
private routable substrate, but tailnet membership alone is not the product auth
model. anvil-serving still needs explicit application-level auth, narrow tools, and
auditability for mutating operations.

## Considered options

1. **Install the full anvil-serving CLI on `fakoli-mini` and keep MCP stdio-only.**
   Rejected as the primary model. It duplicates host-specific dependencies on the
   OpenClaw gateway box and still cannot manage GPU-local Docker/serve state without
   secondary transport.

2. **Have the gateway-side MCP server SSH into the anvil-serving host for every
   operation.** Useful as a fallback, but rejected as the product contract. SSH
   command construction is harder to schema, audit, and test than a typed controller
   API. It is also brittle across macOS, Windows, and WSL host boundaries.

3. **Expose the existing router management and MCP operations on a listening
   controller over the tailnet.** Chosen. The host that owns anvil-serving state runs
   a long-lived controller. The OpenClaw gateway host, local operator skill, or other
   trusted agent clients call it over Tailscale.

4. **Expose controller operations on a public network interface.** Rejected. The
   default transport must be private-tailnet-only. Public exposure would require a
   separate hardening ADR and a stronger threat model.

## Decision

Add a fourth deployment layer on top of ADR-0013: a tailnet-reachable
anvil-serving controller.

The controller runs on the host that owns the anvil-serving CLI, router config,
serve manifests, model services, and local GPU operations. It listens on a Tailscale
address or tailnet DNS name, not on a public interface by default. It exposes the
same control-plane contract as `anvil-serving mcp`: structured tools, JSON schemas,
dry-run previews, explicit `confirm` fields for disruptive operations, and no raw
credential values in requests or responses.

The implemented split-host shape is:

- The anvil-serving host runs the long-lived controller:

  ```bash
  export ANVIL_CONTROLLER_TOKEN="<generate-and-store-out-of-band>"
  anvil-serving controller serve \
    --host anvil-gpu.tailnet.example \
    --port 8765 \
    --auth-token-env ANVIL_CONTROLLER_TOKEN
  ```

  For single-host local development, bind to `127.0.0.1` instead of the tailnet
  hostname.
- `anvil-serving mcp` remains the local stdio MCP server when no remote controller
  URL is supplied.
- On the gateway or operator host, the MCP bridge points at the controller and
  resolves the same token from the environment:

  ```bash
  export ANVIL_CONTROLLER_TOKEN="<same-secret-as-controller-host>"
  anvil-serving mcp \
    --controller-url http://anvil-gpu.tailnet.example:8765 \
    --auth-env ANVIL_CONTROLLER_TOKEN
  ```

  The bridge presents the same tool names and schemas to the operator or agent
  whether the call is satisfied locally or by the tailnet controller.
- Gateway-local operations, such as restarting the OpenClaw gateway, should prefer a
  pull/local-apply model: the gateway host asks the controller for rendered config or
  instructions, then applies the gateway-local action itself. Controller-initiated
  push or SSH back to the gateway remains possible only as an explicit, confirmed
  tool target. The current `harness sync openclaw --gateway-host ...` path is that
  explicit fallback; it is not the long-term default contract.

The controller is a management plane only. It must not become a model-response data
plane or a general shell relay. Model requests continue to use the router HTTP
front door. Per-turn OpenClaw routing remains the OpenClaw hook plugin's job.

### Transport and auth requirements

The controller implementation:

- binds only to an explicit tailnet hostname/address, an explicitly configured
  private bind address, or `127.0.0.1` for local development;
- requires an auth token resolved from the environment variable named by
  `--auth-token-env`; the MCP bridge resolves the same token through
  `--auth-env`;
- rejects or redacts raw secret values in tool arguments, command previews, logs, and
  structured responses;
- writes an audit log with request ids, operation names, target metadata,
  dry-run/confirm state, and result status;
- exposes `GET /health` for readiness checks;
- enforces the same dry-run/confirm behavior as the stdio MCP tools;
- returns structured failure envelopes instead of human-only stderr text;
- keeps router core modules OpenClaw-free and controller-transport-free.

Tailscale or equivalent direct private connectivity is the network substrate,
not the sole security control. Tailnet ACLs or private network policy should
limit which machines can reach the controller, and the controller should still
verify its own token on every request. Health checks should use the
controller's private address, for example:

```bash
curl -fsS \
  -H "Authorization: Bearer $ANVIL_CONTROLLER_TOKEN" \
  http://anvil-gpu.tailnet.example:8765/health
```

### Product contract

The split-host product contract becomes:

- The gateway host owns OpenClaw gateway runtime and gateway-local reload/restart
  actions. In the reference deployment, this is `fakoli-mini`.
- The anvil-serving host owns router, serve, model, benchmark, preflight, voice
  lifecycle for local voice manifests, and harness rendering operations. In the
  reference deployment, this is usually `fakoli-dark` or another resource-owning
  host.
- The controller is the typed transport between those hosts.
- The operator skill can use either local stdio MCP or remote controller transport
  without changing the high-level playbook.

## Consequences

- ADR-0013 remains valid, but stdio MCP is no longer sufficient for the full target
  deployment. It is the local transport; tailnet controller is the remote transport.
- `harness sync openclaw` should be refactored toward render/apply primitives that
  support both push and pull flows. In split-host mode, rendering can happen on the
  anvil-serving host while gateway-local apply/restart happens on the gateway host.
- The controller server uses the same tool schemas as `anvil-serving mcp`, not a
  second bespoke REST API with different semantics.
- Tool schemas must stay transport-neutral. A skill should not care whether a tool
  call is satisfied by local stdio, a local controller, or a tailnet controller.
- Controller logs become operational evidence. They should record operation metadata,
  target host, dry-run/confirm state, and result status, but never credential values.
- The OpenClaw plugin remains thin. It should not learn controller transport details
  unless OpenClaw later exposes a native, safe way for plugins to call operator tools.
- The controller introduces a new security boundary. Before accepting remote mutation
  tools, implementation must include auth tests, bind-address tests, redaction tests,
  and negative tests for missing `confirm`.
