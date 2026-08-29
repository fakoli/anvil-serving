# Router fleet status misprobes runtime-relative upstreams from the command host

**Status:** Resolved 2026-08-28

## Problem

After the 2026-08-15 Qwen3.8 split restoration, live router transition status
reported exact expected/observed identities, all tiers ready and admitting,
and real authenticated routed text and OCR requests passed. In the same state,
`router fleet-status` reported every configured alias and purpose endpoint as
`UNREACHABLE (URLError)`. Supplying the exact installed live router config did
not change the result.

The router runs in a container while the command executes on the Windows host.
An upstream address can therefore be correct from the router runtime and wrong
from the command host. The current fleet probe does not make that execution
perspective visible and can produce a false fleet outage after an otherwise
verified restoration.

## Required behavior

1. Resolve each upstream through its declared host/runtime ownership before
   probing it, or execute the probe from the same runtime perspective as the
   live router.
2. Report the probe perspective and endpoint kind without exposing private
   addresses or credentials.
3. Keep configured-file inspection distinct from live installed-router state.
4. When the two disagree, fail with a typed diagnostic that names the
   perspective mismatch instead of collapsing every route to `URLError`.

## Acceptance

- Hermetic coverage includes host-to-host loopback, router-container to host,
  and a genuinely unavailable upstream.
- A ready live router with runtime-relative upstreams reports its reachable
  aliases reachable from both local and controller-dispatched fleet checks.
- A configured-but-not-installed file can still be inspected explicitly and
  is labeled as configuration evidence rather than live health.
- Human and JSON output preserve alias, tier, readiness reason, probe
  perspective, and typed failure class without publishing capability-bearing
  URLs.

## Resolution

Live `router fleet-status` now probes the installed configuration by executing
from the canonical router container while the explicit configured-file mode
remains command-host evidence. Reports identify `router-runtime` versus
`command-host`, classify endpoint kinds without printing their addresses, and
return `probe_perspective_mismatch` when a configured-file probe fails only
because its host perspective cannot reach a runtime-relative endpoint.

Hermetic coverage includes host loopback, router-container-to-host, unavailable
upstreams, missing Docker/container cases, bounded execution timeout, output
size limits, and controller/MCP dispatch. Final live evidence on executable
revision `bb798b0` reported the installed router perspective and HTTP 200 for
all nine configured chat, vision, and audio routes. The same deployment passed
smoke, structured JSON, a 131K-context needle, and a 20/20 tool batch.
