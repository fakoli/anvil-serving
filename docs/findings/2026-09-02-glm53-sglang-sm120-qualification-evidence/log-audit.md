# Bounded candidate log audit

The final 245,760-token C1 candidate remained healthy through preflight,
capacity, quality, multimodal, and endurance workloads. The bounded managed log
query found no traceback, process restart, corruption report, CUDA error, or
out-of-memory event.

Expected non-fatal observations were retained:

- the SM120 sparse-MLA `glm53_nope` CPB calibration rejected implausible fits
  on both ranks and used the runtime's C++ heuristic;
- optional NCCL network, RMA, GIN, tuner, profiler, and environment plugins
  were absent, while the local shared-memory/direct collective path operated;
- Triton emitted deprecation/data-race-analysis warnings during compilation;
- Torch emitted deprecation warnings for its distributed all-gather API.

No kernel-tune artifact was adopted. The calibration warning is an
optimization lead, not proof that a tune improves end-to-end performance; an
exact image/model/topology default-versus-tuned A/B remains required.
