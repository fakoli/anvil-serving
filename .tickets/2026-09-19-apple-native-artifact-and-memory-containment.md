# Apple native artifact identity and memory containment

Status: open; bounded cache inventory/removal-preview subitem delivered in this PR after tests

The 2026-09-19 Swift/stock Qwen3.8 Apple feasibility screen stopped before a
trial. The native runtime admits MLX only and rejects native llama.cpp, while
static `memory_mib` admission does not enforce loader peak or a RAM-plus-swap
limit. With unknown peak and Metal allocation behavior, candidate operations
cannot contain a trial safely. The product also has no managed native
exact-artifact pull/recipe identity surface for the selected GGUF pair and
matching projector.

Acceptance:

- **Delivered (bounded):** read-only native cache inventory and non-mutating
  removal preview now report local snapshot link integrity and byte accounting.
  They intentionally return private local paths and cannot prove that a local
  snapshot contains every upstream artifact without an upstream manifest; do
  not publish their raw output or treat a preview as cleanup authorization.
- Add a managed native exact-artifact pull and recipe identity contract that
  pins model, projector, runtime, and supported format without exposing private
  paths, hosts, or credentials.
- Enforce a candidate loader peak plus RAM-and-swap containment limit, with an
  explicit OS reserve, through the managed native launcher. Surface the
  configured bound, observed peak/events, exit/OOM state, and independent
  restoration result in status/evidence.
- Add a Metal-runtime containment mechanism or reject the trial before launch
  when no enforceable mechanism exists. `mx.set_memory_limit` alone is not
  sufficient because it is a guideline that can use swap.
- Regression-test invalid limits, preview non-mutation, exact identity,
  containment failure, and baseline restoration. No route, direct-alias, or
  serve-promotion behavior may change as part of this work.

Evidence: [Apple feasibility stop](../docs/findings/2026-09-19-swift-qwen38-apple-feasibility.md).
This is a product-operation gap, not a model-quality failure or a recommendation
to delete artifacts.
