# Preserve the primary startup failure when exited cgroup counters disappear

Observed in the [2026-10-25 sampling-fix trial](../docs/findings/2026-10-25-sampling-fix.md): the preweight regression child failed `Ordinary-logit exact mask parity failed`, the startup wrapper exited 1, and Docker reported `OOMKilled=false`. Once the container exited, effective cgroup limits were unavailable. Managed status/health waiting surfaced a memory-containment failure, obscuring the actionable assertion.

Open product gap; no runtime fix in this publication change. Preserve the exit state and earliest bounded startup error, and classify unavailable post-exit containment observations separately. Continue to fail closed for a live container whose memory limits genuinely disagree with the recipe. Keep failed containers available for managed logs and retain automatic rollback/readmission behavior.

Acceptance: an offline stopped-container fixture reports exit 1 and unavailable cgroup evidence without asserting a containment breach; a live mismatched-limit fixture still fails the containment gate. A supported bounded logs path exposes the original assertion. Do not rerun model inference merely to test this reporting change.
