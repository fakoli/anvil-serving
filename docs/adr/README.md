# Architecture Decision Records (ADRs)

This directory records the **significant architecture and design decisions** for anvil-serving —
the context, the options weighed, the decision, and its consequences — so the *why* survives the
people and the chat logs.

## Convention

- **One file per decision:** `NNNN-short-kebab-title.md` (zero-padded, sequential — `0001`, `0002`, …).
- **Format:** Context → Considered options → Decision → Consequences. Start from [`template.md`](template.md).
- **Status:** `Proposed` · `Accepted` · `Deferred` · `Superseded by ADR-NNNN`.
- **Never delete an ADR — supersede it.** A reversed decision is itself history; write a new ADR that
  supersedes the old one and mark the old one `Superseded`.
- **When to write one:** any non-trivial, hard-to-reverse, or cross-cutting decision — a product
  contract, a routing/auth model, a dependency, a protocol or wire-format choice, a security posture.
- **New ADR:** copy `template.md` → next number, fill it in, link related ADRs/issues, and add it to
  the index below.

## Index

| # | Title | Status |
|---|-------|--------|
| [0001](0001-cloud-cost-and-subscription-auth.md) | Cloud cost & subscription auth — why anvil should not relay cloud | Accepted |
| [0002](0002-serves-are-compose-defined.md) | Model serves are Docker-Compose-defined | Accepted |
| [0003](0003-portable-defaults-and-generic-onboarding.md) | Portable-by-default: out-of-box router correctness and a generated bring-up | Accepted |
| [0004](0004-router-as-a-service-containerized-and-authed.md) | Router as a service: containerized, network-facing, token-authed | Accepted |
| [0005](0005-anvil-503-native-failover-unreliable.md) | anvil-503 native-failover loop (historical fallback architecture) | Superseded by ADR-0028 |
| [0006](0006-multiplexer-swap-draining.md) | Multiplexer swaps drain in-flight requests before evicting the resident model | Accepted |
| [0007](0007-subscription-auth-cloud-tier.md) | Subscription-auth cloud tier | Superseded by ADR-0028 |
| [0008](0008-heavy-tier-speculative-decoding.md) | Heavy tier enables NEXTN speculative decoding (self-speculation, no draft model) | Accepted |
| [0009](0009-profile-write-back-loop.md) | Measured quality-profile write-back loop | Superseded by ADR-0028 |
| [0010](0010-specialized-engine-tier.md) | Specialized-engine tier: run any model on any engine (config-first, RelayBackend-served) | Accepted |
| [0011](0011-two-mode-operation.md) | Two modes of operation: agentic vs maximum-flexibility | Superseded by ADR-0028 |
| [0012](0012-serve-and-router-management-verbs.md) | Serve & router management flows through anvil-serving verbs; deployed router config is a mutable volume promoted via validate→atomic-write→reload→rollback | Accepted |
| [0013](0013-openclaw-layers-and-mcp-control-plane.md) | OpenClaw intent adapter and MCP control plane | Superseded by ADR-0028 |
| [0014](0014-tailnet-controller-transport.md) | Tailnet controller transport for split-host OpenClaw deployments | Accepted |
| [0015](0015-operator-skills-and-subagent-workflows.md) | Operator skills and sub-agent workflows above the MCP/controller control plane | Accepted |
| [0016](0016-runtime-tier-readiness.md) | Runtime tier readiness excludes stopped serves without config rewrites | Accepted |
| [0017](0017-gpu-residency-reservations.md) | GPU residency reservations: declarative VRAM ledger enforced by serve lifecycle verbs | Accepted |
| [0018](0018-router-transition-safety.md) | Router transition safety for slow single-workstation model swaps | Accepted |
| [0019](0019-anvil-serving-owns-the-tailnet-edge.md) | anvil-serving owns the tailnet edge: a `tailscale serve` path-router (`/v1` → router, `/comfyui`) in front of the unchanged router | Accepted |
| [0020](0020-init-defaults-to-home-scaffold-shipped-as-package-data.md) | `init` defaults to the home scaffold, shipped as package data (installed-tool fix) | Accepted |
| [0021](0021-cli-interaction-contract.md) | CLI interaction contract: resource-first, previewable, recoverable, and cross-platform | Accepted |
| [0022](0022-evaluation-evidence-protocol.md) | Evaluation evidence protocol: model-aware, repeated, and comparison-safe | Accepted |
| [0023](0023-lifecycle-aware-wsl-cache-reclaim.md) | Lifecycle-aware WSL page-cache reclaim | Accepted |
| [0024](0024-normalized-audio-gateway.md) | Normalized authenticated one-shot audio gateway | Accepted |
| [0025](0025-tts-authoritative-realtime-assistant-transcripts.md) | TTS-authoritative Realtime assistant transcripts | Accepted |
| [0026](0026-opt-in-transparent-response-model.md) | Opt-in transparent response model reports the served tier | Superseded by ADR-0028 |
| [0027](0027-public-findings-are-durable-evidence.md) | Public findings are durable evidence | Accepted |
| [0028](0028-serving-benchmarks-and-thin-capability-gateway.md) | Serving, benchmarks, and a thin capability gateway | Accepted |
| [0029](0029-modular-command-registry.md) | Modular command registry with parser-owned leaf help | Accepted |
| [0030](0030-containerized-remote-controller-and-mcp-2026.md) | Containerized Dark controller and MCP 2026-only remote operation | Superseded by ADR-0031 |
| [0031](0031-dual-era-typescript-mcp-bridge.md) | Dual-era TypeScript MCP bridge on Fakoli Mini | Accepted |
| [0032](0032-public-product-private-operator-state.md) | Public product, private operator state | Accepted |
| [0033](0033-production-durability-and-plane-contract.md) | Production durability model, plane contract, and controller-RPC fleet direction | Accepted |
| [0034](0034-fleet-control-plane-and-node-runtime-classes.md) | Fleet control plane on the gateway host, and node runtime classes | Accepted |
| [0035](0035-fleet-configuration-reconciliation.md) | Fleet configuration reconciliation: git as the state store, controller-mediated install and adopt | Accepted |
