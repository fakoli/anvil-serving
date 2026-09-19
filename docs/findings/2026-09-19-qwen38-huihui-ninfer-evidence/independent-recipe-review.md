# Independent intake review

Reviewer: Sol; author: Astra; evaluated model: Qwen.

- The source-build cache trusts an executable plus a revision marker. This
  proves neither a clean rebuild nor the identity of a reused binary. Current
  execution is one cold scout build only. Promotion requires a baked pinned
  runtime and independent clean recreation evidence.
- Repeatability must precede a promotion decision. The run plan now makes that
  ordering explicit; live post-deployment acceptance follows authorized apply.
- The health timeout can leave a build container running. On any failed load,
  retain the owning error and unload this exact candidate using its managed
  recipe. Do not start another build against its volume concurrently.
- The preliminary feasibility result applies to no-spec at 8K/C1 only. MTP and
  long-context profiles require their own capacity bounds and measurements.
