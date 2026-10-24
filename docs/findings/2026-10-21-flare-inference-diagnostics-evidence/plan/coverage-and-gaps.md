# Wave 1 coverage ledger

This ledger was consolidated during the instrumented run. The execution plan and its amendments predate their dependent requests; this table was not frozen before the first baseline request. It does not establish a formal serving-comparison/v1 campaign.

| Requested outcome | Current evidence or remaining gate |
| --- | --- |
| Record a repeatable plan | execution-plan.md, frozen-source-files-v2.json, retained commands and config files; analysis-contract-v2.md governs interpretation. |
| Preserve exact code/model/build identity | Flare patch including the untracked source file, pinned image build context and image digest, model/package revision, recipes, runtime identity before every block. |
| Monitoring follows swaps | Control and instrumented generations registered; stale generation fenced; authenticated model discovery and local Prometheus/Grafana checks pass after scrape catch-up. Native fleet CLI/controller release remains unexecuted. |
| Observe power and host constraints | Both GPUs fixed at300W; per-block native/GPU/observer snapshots, continuous Prometheus history, 2-second host CPU/memory/PSI/I/O. No continuous cgroup sampler; bounded identity checks retain memory/OOM state. |
| Compare incumbent and unchanged Flare | Incumbent-before complete with one C4 reasoning-only length failure. Control measured C1 156/156 and C4 480/480. No overall ranking. |
| Measure instrumentation outside capture | Instrumented measured C1 156/156 and C4 480/480 complete. Same exported model package and runtime settings, different executable images/source; detailed trace off. This is not a causal observer-overhead measurement. |
| Obtain live internal diagnostics | One-second C4 pilot accepted independently:44samples, both ranks, no loss; four exact1024-token completions. |
| Repeat captures and measure observer cost | Deferred under the original outage/restore reserve; next-experiments.md retains the repeated fixed-token plan. |
| Kernel timeline and hardware counters | Earlier Nsight/module mapping retained in parent directory. New node/hardware captures deferred under budget; not measured this wave. No Nsight Compute replay. |
| More context/cache lanes | Deferred prospectively after baseline failure; original matrix retained. |
| Find a patch candidate | Cache counters and source support a52-row churn hypothesis; no causal fix or performance patch yet. Forward-prime/reverse-replay probe is next, followed by row/page pressure instrumentation if needed. |
| Preserve incumbent and prove rollback | Exact retained incumbent restored/readmitted, direct/routed gates passed, 20/20 final monitoring checks. Incumbent-after complete with one C4 failure; all16before/after native populations reconcile. |
| Build repeatable skills | Existing kernel-tuning skill updated/reviewed; authenticated trace and capacity config behind Anvil CLI. Existing observability skill retained. No new plugin required. |
| Publish a full record | Private report closes32blocks/3392attempts with two retained failures. Sanitized publication matrix is validated separately after restoration. All failed/blocked/deferred cells stay visible. No upstream submission or promotion. |
