# Qwen3.8 efficient variants on RTX 5090 benchmark evidence

This directory contains the sanitized evidence bundle for the dated finding.
The native benchmark artifacts remain authoritative; the files below provide a
consistent campaign-level index.

## Campaign boundary

- **Campaign ID:** `2026-09-12-qwen38-efficient-variants-rtx5090`
- **Capability:** text and tools
- **Repository revision:** `948346f5ab553361c6b61d9517b71a0e5e3cfe98`; clean isolated worktree at start
- **Evidence labels:** functional and diagnostic quality; failed strict capacity retained
- **Decision labels:** incumbent retained; challengers no-promotion
- **Promotion boundary:** operator authorized selection and promotion, but no challenger cleared replacement gates

## Common campaign artifacts

- [`artifact-manifest.json`](artifact-manifest.json) - role ledger, native
  schemas, file hashes, and explicit gaps
- [`source-registry.json`](source-registry.json) - dated source provenance and
  decision impact
- [`summary.json`](summary.json) - bounded machine-readable outcome and
  decision
- [`friction-log.md`](friction-log.md) - failures, workarounds, ambiguity, and
  recurring manual steps
- [`restoration.json`](restoration.json) - starting/ending state and post-run
  verification, or the reason restoration was not applicable

## Working campaign controls

- [`campaign-state.json`](campaign-state.json) - compact resumable stage,
  launcher, assignment, completed-cell, and next-action ledger
- [`dispatch-packet.md`](dispatch-packet.md) - bounded task packet for a
  delegated campaign stage
- [`coverage-and-gaps.md`](coverage-and-gaps.md) - request-to-evidence matrix
  that keeps partial, rejected, and missing outcomes visible

These controls organize execution. They become evidence only when the final
artifact manifest retains them under an applicable role.

## Workload and plan

- [`workload-manifest.json`](workload-manifest.json) — built-in deterministic workload and controls
- [`run-plan.json`](run-plan.json) — predeclared order, gates, budgets, and 50 percent usage stop
- [`configuration.json`](configuration.json) — sanitized starting hardware and incumbent identity
- [`feasibility-input.json`](feasibility-input.json) — interval screen before downloads or loads
- [Managed recipes](recipes.toml) — byte-identical public copy of the four exact candidates in `configs/qwen38-efficient-rtx5090-recipes.toml`

## Raw run evidence

| Profile | Functional gate | Thinking-enabled diagnostic |
|---|---|---|
| Signal Q6_K MTP3 | [Preflight](signal-preflight.json) | [9/10 strict](signal-mmlu-thinking.json) |
| Swift Q6_K MTP3 | [Preflight](swift-preflight.json) | [9/10 strict](swift-mmlu-thinking.json) |
| Qwopus Flash Q6_K no-spec | [Preflight](qwopus-preflight.json) | [1/10 strict](qwopus-mmlu-thinking.json) |
| Minitron20B Q6_K no-spec | [Preflight](minitron-preflight.json) | [3/10 strict](minitron-mmlu-thinking.json) |
| Incumbent UD-Q4_K_XL MTP3 | [Restored preflight](incumbent-restored-preflight.json) | [8/10 strict](incumbent-mmlu-thinking.json) |

These ten-question, one-repeat scores include exact format and completion
failures; they are not general knowledge benchmark scores. Native artifacts
retain complete visible outputs, reasoning metadata, finish reasons and keys.
See [workload](workload-manifest.json), [errata](provenance-errata.json),
[lifecycle review](lifecycle-review.json), [recipes](recipes.toml), and
[matched no-spec controls](nospec-controls.toml).

## Decision and publication

Retain the exact healthy262K incumbent. Signal is the best new efficiency
research lead, but its tested64K profiles did not qualify as replacements.
Thinking-off repeated results: [Signal24/30](signal-nospec-quality-disabled.json),
[Qwopus24/30](qwopus-quality-disabled.json), [Minitron15/30](minitron-quality-disabled.json),
and [incumbent21/30](incumbent-mmlu-disabled.json). These repeat the same ten
questions, not30 independent questions. The [Signal9216-token retry](signal-nospec-mmlu-budget-retry.json)
also exhausted reasoning without a final answer.

The [cold32-word diagnostic chart](cold-output-diagnostic.svg) and
[hash-bound data](cold-output-graph-data.json) show successful N5 populations
only. Swift's warm and Minitron's failed populations are excluded. Context,
weight quantization and speculation differ, and CPU load was uncontrolled;
the plot is not a finalist or causal speedup claim.

See the [dated finding](../2026-09-12-qwen38-efficient-variants-rtx5090.md),
[publication summary](publication-summary.md), [restored state](restored-state.json),
[recovery admission](recovery-admission.json), and [Pi review](pi-rpc-review.json).
The common role ledger retains all failed and successful native artifacts;
no raw result was rewritten to manufacture a pass.

[Repository and publication verification](verification.json) records the
8,117-pass full regression, documentation gates and checkout-byte safeguards.
This does not remove the separate general lifecycle release hold.
The [independent evidence review](evidence-review.json) accepts the bounded
decision, subject to exact final manifest integrity.
