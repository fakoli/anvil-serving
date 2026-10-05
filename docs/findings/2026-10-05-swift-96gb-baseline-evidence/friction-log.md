# Failure evidence and engineering lessons

## Repeated-word stress: open configuration/workload investigation

The strict 8K/C1, 128-word cell completed zero of 12 requests with visible
output. Native failure is `stream_validation_failed`: “stream completed
without visible content”. The stream/nonstream follow-up returned visible
ordinary prose in both modes, while the repeated-word workload returned no
visible content in either mode. The latter stopped rather than demonstrating
a universal budget exhaustion or transport failure. This is a workload-specific
answer-separation observation, not a general NInfer streaming defect.

Disposition: preserve the failed stress artifact and its ineligibility. The
campaign owner froze a separate practical summary/canary service-latency v2
workload before candidate results. It must retain correctness gates and cannot
be pooled with the strict repeated-word population. Variable output lengths
permit bounded service TTFT/E2E comparisons, not fixed-output decode rankings.
Independent gate: all attempts must have visible output, their own marker,
no foreign marker and complete validation capture; results are separate artifacts.
All four v2 cells completed 12/12 with the independent visible/canary gates.
The failed stress remains unresolved for that workload; v2 is a separate
service-latency population, not a repaired stress result.

## Historical SWE image identity: bounded evidence limitation

Five official grader results exist, with four resolved. The task images were
observed after the task stage and again after grading with unchanged IDs.
That does not prove pre-task enforcement or continuous absence of tag movement.
Disposition: retain this limitation beside the 4/5 result. Future paired arms
need frozen task-image identities before execution and independent mismatch
rejection. Do not retrospectively label the historical run as having that guard.

## Nominal RAM is not enforced process capacity

Nominal 96 GB system memory leaves about 93.68 GiB visible physical RAM, while
the configured WSL VM has a separate 64 GB ceiling. The exact runtime flags,
resident experts, staging peaks, Windows reserve and swap bound all matter.
Disposition: the product now checks Windows/WSL identity and headroom before
memory-bounded loads. A managed 32 MiB RAM/no-swap Python canary attempting
128 MiB was independently observed as OOM-killed with exit 137. That checks
enforcement, not whether a model fits. Its private receipt is separate from
these model results; no candidate memory success is claimed here.

## Reproducible baseline rather than current-source assumptions

The baseline native runs record source 1704cf58 plus a tracked diff digest and
oracle revision 7. Preserve both, rather than relabeling them with the later
publication commit. The older engine uses separate host-KV and host-state
pools; current upstream flag names are not safe substitutions.
Disposition: source/recipe/image pins are retained; same-model cache trials
must hold all other variables fixed. Increased inactive-history retention
does not promise faster cold decode or four simultaneous full context windows.

## Independent image custody and operational diagnostics

The paired SWE replay resolved 4/5 with exact images checked before the agent
and grader. [PR 620](https://github.com/fakoli/anvil-serving/pull/620) records
that durable guard; the first run retains its weaker boundary-only evidence.
[PR 619](https://github.com/fakoli/anvil-serving/pull/619) fixes local Docker
Desktop WSL memory observation without weakening admission.
[PR 621](https://github.com/fakoli/anvil-serving/pull/621) preserves bounded
Docker diagnostics when managed recipe unload fails. All three are merged.
[PR 622](https://github.com/fakoli/anvil-serving/pull/622) is also merged and
makes sampled Windows admission bounds visible. Source merge alone does not
establish installation or prove that a refused load is safe.

## Initial Flash latency: open optimization investigation

The initial M50 cell passed all twelve canaries but failed the predeclared
practical latency thresholds. Its native result remains eligible service
evidence with a failed campaign gate. Instrumented MMQ phase counts guide
diagnosis only. Preserve cumulative/per-run distinctions, decode-only file
counters and uncertainty about physical I/O; no kernel or offload cause is
established solely by aggregate timing labels. Further supported trials and
an uninstrumented twelve-request gate are required before any improvement claim.

## Flash investigation closed without promotion

Fixed-4096 reduced refill cost while increasing batched prompt time; three diagnostic requests did not establish useful service improvement. The original twelve-request practical gate remains failed, and the earlier trace tool disconnect remains unresolved. Flash expansion/finalist gates were not run. Retain the pinned artifacts and measured negative results; finish the independent Swift cache/restoration branch. [Paired diagnostic analysis](prefill4096-analysis.json).

## Retain failed requests and verify observation ordering

A 19/20 preflight result originally retained only the nineteen successful
observations. [The failure-evidence change](https://github.com/fakoli/anvil-serving/pull/624)
now records each failed attempt with its stage, optional batch index, elapsed
time, exception type and bounded redacted detail. Validators, HTTP behavior,
concurrency and timeouts are unchanged. This improves future diagnosis; it
neither repairs the historical disconnect nor supplies its missing root cause.

[Concurrency and fixture corrections](https://github.com/fakoli/anvil-serving/pull/623)
preserve identical admission replay under a concurrent commit and read a media
job's state, events and artifacts from one SQLite snapshot. Deterministic
interleaving tests cover both races. Test fixtures now wait for actual client
stream observations and completion of response auditing; server writes and
receipt of response bytes alone are not completion barriers. Child cleanup
coverage creates the declared process population before timing cleanup. These
changes improve reproducibility without relaxing production limits, response
expectations or model gates; all four Linux/Windows full CI suites passed.
