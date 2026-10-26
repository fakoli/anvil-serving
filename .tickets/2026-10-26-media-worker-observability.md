# Expose bounded media-worker diagnostic counters

Status: deferred product gap; functional H3 MCP smoke is complete.

The campaign used managed lifecycle/status/log operations. Independent memory
verification and inherited Docker healthcheck diagnosis additionally required
narrow read-only container inspection: cgroup current/peak/swap/OOM counters,
actual image/limits, and the failing healthcheck's diagnostic text. These are
qualification fixtures, not the normal operator execution path.

Extend the existing managed status/diagnostic surface when repeatable media
qualification needs these fields. Derive identity from the declared worker;
bound output/time, redact private paths, and fail explicitly when unsupported.
Do not introduce a second lifecycle owner or infer GPU usage from configured
reservations. Check stopped/absent workers and unsafe/mismatched ownership.

