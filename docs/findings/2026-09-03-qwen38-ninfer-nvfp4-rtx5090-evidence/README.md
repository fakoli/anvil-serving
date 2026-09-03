# Qwen3.8-27B NInfer NVFP4 RTX 5090 evidence

This directory contains the sanitized evidence bundle for the
`2026-09-03-qwen38-ninfer-nvfp4-rtx5090` campaign. Native benchmark artifacts
remain authoritative; the common files provide the campaign index.

## Campaign boundary

- **Campaign ID:** `2026-09-03-qwen38-ninfer-nvfp4-rtx5090`
- **Capability:** text generation and tool use
- **Repository revision:** `3fe82086592830ac1707ceaa8184428d2622ac7e`, clean isolated `codex/` worktree at campaign start
- **Evidence labels:** `local-functional`, `local-capacity`, `local-performance`, `local-quality`, `exact-restoration`
- **Decision labels:** `verified`, `challenger`, `no-promotion`
- **Promotion boundary:** this isolated benchmark did not authorize or perform a route, serve-role, or client-catalog change

## Outcome

The NInfer NVFP4 MTP3 arm is the preferred measured direct text/tools
performance challenger on this RTX 5090. At 4K/C1 it produced 0.430-second
median TTFT, 165.9 tok/s median decode, and 0.720-second median E2E versus
0.421 seconds, 75.3 tok/s, and 1.085 seconds for the exact no-speculation
control. A 201746-token API-reported prompt passed with an 8192-token
completion cap.

This is not promotion evidence. The selected arm left 2354 MiB free, below
the ordinary 3 GiB reserve, and each 20-way shared-prefix tool burst completed
17/20 because three requests received explicit C1 overload admissions. The
exact incumbent was restored and passed a fresh smoke check.

## Common campaign artifacts

- [`artifact-manifest.json`](artifact-manifest.json) - completed role ledger
- [`source-registry.json`](source-registry.json) - dated source provenance
- [`workload-manifest.json`](workload-manifest.json) - deterministic workload contract
- [`run-plan.json`](run-plan.json) - preregistered execution order and gates
- [`configuration-start.json`](configuration-start.json) and
  [`configuration-end.json`](configuration-end.json) - sanitized before/after identity
- [`summary.json`](summary.json) - bounded machine-readable decision
- [`friction-log.md`](friction-log.md) - failures and operational friction
- [`restoration.json`](restoration.json) - verified incumbent restoration
- [`publication-summary.md`](publication-summary.md) - derivative public claims

## Raw run evidence

- Matched capacity: [`capacity-nospec-4k-c1.json`](capacity-nospec-4k-c1.json),
  [`capacity-mtp3-4k-c1.json`](capacity-mtp3-4k-c1.json),
  [`capacity-nospec-4k-c1-warm.json`](capacity-nospec-4k-c1-warm.json), and
  [`capacity-mtp3-4k-c1-warm.json`](capacity-mtp3-4k-c1-warm.json)
- No-spec functional/context: [`preflight-nospec-smoke.json`](preflight-nospec-smoke.json),
  [`preflight-nospec-json.json`](preflight-nospec-json.json),
  [`preflight-nospec-tools-c1.json`](preflight-nospec-tools-c1.json),
  [`preflight-nospec-tools.json`](preflight-nospec-tools.json), and
  [`preflight-nospec-needle-244k.json`](preflight-nospec-needle-244k.json)
- MTP3 functional/context: [`preflight-mtp3-smoke.json`](preflight-mtp3-smoke.json),
  [`preflight-mtp3-json.json`](preflight-mtp3-json.json),
  [`preflight-mtp3-tools-c1.json`](preflight-mtp3-tools-c1.json),
  [`preflight-mtp3-tools.json`](preflight-mtp3-tools.json),
  [`preflight-mtp3-needle-244k.json`](preflight-mtp3-needle-244k.json), and
  [`preflight-mtp3-needle-244k-output-reserve-8192.json`](preflight-mtp3-needle-244k-output-reserve-8192.json)
- Repeated bounded quality: [`quality-nospec.json`](quality-nospec.json) and
  [`quality-mtp3.json`](quality-mtp3.json)
- Restoration check: [`restoration-smoke.json`](restoration-smoke.json)

## Decision and publication

- [Dated finding](../2026-09-03-qwen38-ninfer-nvfp4-rtx5090.md)
- [Feasibility record](../2026-09-03-qwen38-ninfer-nvfp4-rtx5090-feasibility.md)
- [Managed recipe](../../../configs/qwen38-27b-ninfer-nvfp4-rtx5090-252k-recipes.toml)

The intake source-builds the exact NInfer revision on a digest-pinned CUDA
base, but Ubuntu package resolution is not immutable. That limitation, the
C1 admission result, the narrower memory reserve, and the unrun broader gates
must be resolved before a separate human promotion decision.
