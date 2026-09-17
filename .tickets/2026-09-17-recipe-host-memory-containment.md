# Contain candidate host-memory exhaustion

Status: open; blocks retry of the mixed-3.5-bpw startup configuration.

The September 17 GLM mixed EXL3 startup exhausted host RAM and swap, killed a desktop process, and shut down the graphical session before the model became ready. GPU fit did not establish host-loader fit. The managed recipe argv currently has no RAM or swap limit.

Acceptance:

- Add validated recipe RAM and RAM-plus-swap bounds through the shared managed launcher; reject nonsensical or unlimited candidate settings. Preserve an explicit desktop/OS reserve.
- Retain configured limits, memory peak/events, and OOM/exit state in managed status/evidence. Verify containment with a small synthetic allocation test, not a repeat of this host-wide failure.
- Diagnose checkpoint CPU staging/retention before a candidate retry; demonstrate bounded peak RAM and exact baseline restoration under a detached owner so loss of the initiating desktop cannot strand admission.
- Regression-check rendering, invalid limits, and restoration after a failed load.

Evidence: [startup finding](../docs/findings/2026-09-17-glm53-mixed35-startup.md). This is a launch-containment gap, not a measured model-quality failure.
