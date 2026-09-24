# Diagnose monitoring service readiness mismatch

Status: open

A managed restart of an explicitly adopted external Compose Prometheus
container completed its process restart, but the service adapter reported
`readiness_failed`. Subsequent native `/-/ready` returned HTTP 200 with a
plaintext ready response, and live Prometheus queries proved its new scrape
target was up. No monitoring outage remains.

Determine whether the generic engine-none readiness adapter wrongly requires
JSON, probes the wrong address, or has another timeout/transport mismatch.
Preserve the failed managed receipt and successful native observations. Add a
bounded explicit health contract if necessary, then verify managed lifecycle
with plaintext HTTP readiness without weakening ownership or TLS checks.
