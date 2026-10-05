# Router status does not expose exact restart custody

During a same-model descriptive fingerprint correction, `router status` reported
only container name and running state. `router export-config` correctly refused
single-file export because the installed config references a protected durable
key store. Its refusal must remain intact; it does not imply the key store is
missing or authorize exporting credentials.

The existing installed-fleet reader binds raw config bytes, and the key-container
helper verifies durable writable storage and exact container identity. Neither
status result binds the current immutable image, local image-reference resolution,
and complete mount projection required to review restart fidelity. One bounded,
read-only Docker projection was used for those metadata fields; no environment,
credential contents, inference, restart or configuration effect was performed.

Required scope: expose a bounded metadata-only restart-custody observation on
the existing router status surface. Include immutable container/image IDs,
local image-reference equality and an allowlisted mount projection. Keep
credential contents and environment excluded. Existing installed-fleet hashes
and key-container ownership checks remain separate complementary observations;
status must not claim transaction fencing or deployment authorization.

## Resolution

The existing router status summary now adds a bounded, allowlisted custody
projection. CLI status uses the established CommandResult seam to expose it
under JSON while preserving human text. This intentionally changes CLI JSON
`data` from rendered text to an object; MCP/controller fields are additive.
No lifecycle, ownership, config-export, or admission behavior changes.

## CI readiness race

The first complete Linux Python3.13 suite reached7388 passing tests, then the
existing Pi process-group fixture returned SIGTERM exit-15 instead of graceful0.
It published its child-PID readiness file before registering its SIGTERM handler.
The parent could terminate the process in that gap. Publish readiness after
handler registration; production cleanup and the exit/group-removal assertions
remain unchanged. Retain the failed run, then require a fresh complete CI run.
