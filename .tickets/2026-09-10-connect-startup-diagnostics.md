# Bounded Connect startup diagnostics

Status: open; the strict-umask manager write fix is accepted and complete.

A coordinated gateway and connector upgrade rolled back after the gateway
readiness request could not be opened by the declared service identity. The
later missing-ingress response occurred after rollback had already stopped the
gateway; it is an artifact of that rollback, not the initiating failure.

The manager's atomic writer passed the requested public mode to `os.open`, but
the process umask still narrowed a readiness `status.json` request to `0600`.
The service identity could not read that non-secret request. Normal umasks
masked the defect. The write must apply the selected mode through the newly
created descriptor before fsync and atomic publication, while preserving the
owner-only mode for private writes.

Prove the fix with strict-umask public/private mode regressions and a real
service-identity read of the readiness request. Preserve bounded diagnostics,
component identity, timing, exit state, and allowlisted categories without
exposing credentials, capability-bearing URLs, cookies, request headers, or
arbitrary upstream bodies. Keep deployment evidence in private operator storage.
