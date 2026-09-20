# Campaign friction log

| Time | Stage | Category | Earliest actionable evidence | Immediate disposition | Durable fix-forward artifact | Independent verification | Status |
|---|---|---|---|---|---|---|---|
| 2026-09-20T00:40:00Z | SCOUT | unsafe-default | 128K default host-cache startup pressure | stop this configuration | direct-I/O profile | final 160K startup telemetry | closed |
| 2026-09-20T00:51:00Z | SCOUT | failure | 128K 2 GiB host-cache resource-floor failure after successful load | retain failure | direct-I/O profile and feasibility boundary | retained feasibility artifacts | closed |
| 2026-09-20T00:55:00Z | FINALIST | missing-identity | local build lookup initially did not resolve a tested immutable digest | stop identity claim until build receipt | directio-build-receipt.json and sanitized recipe | pinned final image/binary reconstruction | closed |
| 2026-09-20T01:00:00Z | FINALIST | repeated-command | corrected managed tool invocation required after invalid control form | use validated product command | run-plan.json and retained native artifacts | final preflight 8/8 | closed |
| 2026-09-20T01:17:08Z | PUBLICATION | failure | macOS native shell-marker initial failure; read probe passed | retain failure without root-cause assignment | pending client investigation | read-nonce acceptance retained privately | open |
