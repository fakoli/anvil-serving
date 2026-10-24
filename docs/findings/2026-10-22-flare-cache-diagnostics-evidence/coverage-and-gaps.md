# Diagnostic wave coverage and limits

This common ledger was consolidated during closure. The research plan, counter-arm plan, request fixtures, phase order and hashes were retained before their respective requests; this is not a retrospectively frozen serving comparison.

| Request | Status | Evidence / limit |
| --- | --- | --- |
| Research general serving stacks and vLLM/SGLang | Complete | research-and-plan.md and pinned source registry; analogies, not imported speed claims. |
| Refine and execute reverse/forward cache experiment | Complete after final analyzer gate | Two images, fresh forward/reverse pairs, exact bodies and per-request counters. |
| Identify internal bottleneck | Measured structural constraint | Counter-only image observes independent state-row pressure and page availability. Not an end-to-end speedup. |
| Add instrumentation and debugging | Complete locally | Ten host-pool metrics, CPU reproducer, 37 applicable tests, pinned managed build. Disk io_uring runtime tests remain unqualified. |
| Repeat trace off/on | Complete, causal conclusion blocked | Three C1 and three C4 pairs; all 15 request pairs differ in generated token sequence. Equal length/seed does not establish equal work. |
| Preserve monitoring across swaps | Live verified locally | Generation-specific Prometheus discovery; ten new series for each counter incarnation. Existing dashboard verification repeated after restoration. |
| Trace exact kernel limiter | Not collected | No new Nsight critical-path or hardware-counter capture. Scheduler samples are overlapping rank intervals. |
| Improve allocator or eviction policy | Next experiment | No policy change tested. Fixed-byte row/page repartition and protected workloads must precede gain claims. |
| Prove parity with incumbent | Not measured in this wave | Different checkpoints/stacks; no new matched incumbent performance matrix or semantic-quality qualification. |
| Reusable skill/plugin | Skill refined | Existing kernel-tuning skill covers pool counters, decoded-work matching and retained rollback. No new plugin needed. |
| Retain reports and easy conversion | Retained | Pinned source, recipe, patch/build, corpus, responses, counters, restoration, derived summary and bounded next test. No promotion or upstream submission. |

All natural-output cache successes mean visible transport completion, not semantic correctness. Cache C1 means serial HTTP requests, while the serve permits C4. Host cache is configured 32 GiB; reported resident bytes and row/page capacities are separate measurements. Both GPUs retain 300 W limits.
