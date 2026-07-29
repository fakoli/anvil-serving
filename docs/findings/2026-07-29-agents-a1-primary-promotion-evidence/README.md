# Agents-A1 FP8 Primary-promotion evidence

All timestamps are observations from 2026-07-29 on Fakoli Dark. The benchmark
artifacts are machine-readable product output; logs are retained for startup
identity, memory, kernel selection, and managed transition evidence.

| Artifact | Purpose |
|---|---|
| `candidate-status.json` | Exact qualification-container identity |
| `candidate-startup.log` | Engine, model, memory, KV, and startup evidence |
| `preflight-240k-thinking-disabled.json` | Smoke, JSON, 240K retrieval, and 20-tool functional gate |
| `quality-protocol-v3-262k-thinking-disabled.json` | Three-repetition context/tool/session/intelligence gate |
| `promotion-transaction.stdout.log` | Managed quiesce, start, gate, router install, identity, and readmission |
| `promotion-transaction.stderr.log` | Terminal promotion diagnostics; empty on a clean transaction |
| `promotion-functional-preflight.json` | In-transaction direct 240K Primary gate |
| `routed-primary-preflight.json` | Post-promotion routed smoke, JSON, and 20-tool gate |
| `serve-state-after.json` | Managed serve state after promotion |
| `router-state-after.json` | Router readiness and exact Primary identity after promotion |

The protocol-v3 artifact was independently inspected with
`eval benchmark evidence show`; it reports a complete quality artifact, three
repetitions, no validation errors, and the expected deterministic suite
counts. Media bytes and credentials are not present in these files.
