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
| [0005](0005-anvil-503-native-failover-unreliable.md) | anvil-503 native-failover loop: OpenClaw's fallback walk does not escape a `providerOverride` | Accepted |
| [0006](0006-multiplexer-swap-draining.md) | Multiplexer swaps drain in-flight requests before evicting the resident model | Accepted |
| [0007](0007-subscription-auth-cloud-tier.md) | Subscription-auth cloud tier: feasible via `claude` CLI subprocess — opt-in only, text-only classes, no tool broker | Accepted |
| [0008](0008-heavy-tier-speculative-decoding.md) | Heavy tier enables NEXTN speculative decoding (self-speculation, no draft model) | Accepted |
| [0009](0009-profile-write-back-loop.md) | Measured quality-profile write-back loop (offline-batch-first, fingerprint-keyed) | Accepted |
| [0010](0010-specialized-engine-tier.md) | Specialized-engine tier: run any model on any engine (config-first, RelayBackend-served) | Accepted |
| [0011](0011-two-mode-operation.md) | Two modes of operation: agentic vs maximum-flexibility (global mode switch) | Accepted |
| [0012](0012-serve-and-router-management-verbs.md) | Serve & router management flows through anvil-serving verbs; deployed router config is a mutable volume promoted via validate→atomic-write→reload→rollback | Accepted |
| [0013](0013-openclaw-layers-and-mcp-control-plane.md) | OpenClaw layers and MCP control plane: hook adapter for intent, router data plane for quality, MCP for operations | Accepted |
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
| [0026](0026-opt-in-transparent-response-model.md) | Opt-in transparent response model reports the served tier | Accepted |
