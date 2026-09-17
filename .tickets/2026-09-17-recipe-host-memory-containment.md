# Contain candidate host-memory exhaustion

Status: resolved; bounded loader retry reached readiness and passed functional checks.

The September 17 GLM mixed EXL3 startup exhausted host RAM and swap, killed a desktop process, and shut down the graphical session before the model became ready. GPU fit did not establish host-loader fit. The original managed recipe had no RAM or swap limit.

Acceptance:

- Add validated recipe RAM and RAM-plus-swap bounds through the shared managed launcher; reject nonsensical or unlimited candidate settings. Preserve an explicit desktop/OS reserve.
- Retain configured limits, memory peak/events, and OOM/exit state in managed status/evidence. Verify containment with a small synthetic allocation test, not a repeat of this host-wide failure.
- Diagnose checkpoint CPU staging/retention before a candidate retry; demonstrate bounded peak RAM and exact baseline restoration under a detached owner so loss of the initiating desktop cannot strand admission.
- Regression-check rendering, invalid limits, and restoration after a failed load.

Evidence: [startup finding](../docs/findings/2026-09-17-glm53-mixed35-startup.md). This is a launch-containment gap, not a measured model-quality failure.

Resolution: the shared recipe launcher now validates RAM, combined RAM-plus-swap,
and admission-reserve fields, checks the local Docker context and cgroup-v2
capabilities, and records memory limits, peak/events, and OOM state. A managed
64 MiB allocation probe contained its child OOM while the baseline stayed healthy.
An opt-in synchronous R7 slice transfer removed whole-checkpoint CPU retention;
the bounded retry loaded all 120 shards and passed direct smoke, JSON, and tool
checks. A detached owner restored the exact baseline and verified both direct
and routed preflight after the C1 trial. See the
[fix-forward finding](../docs/findings/2026-09-17-glm53-mixed35-fix-forward.md).
The reserve is an admission check, not a system-wide reservation.
