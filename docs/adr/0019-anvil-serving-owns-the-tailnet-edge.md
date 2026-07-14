# ADR-0019 — anvil-serving owns the tailnet edge

- **Status:** Accepted
- **Date:** 2026-07-13
- **Relates to:** [ADR-0014](0014-tailnet-controller-transport.md),
  [ADR-0004](0004-router-as-a-service-containerized-and-authed.md),
  the [single tailnet DNS endpoint runbook (T014/F008)](../TAILNET-ENDPOINT-RUNBOOK.md),
  `anvil_serving/edge.py`, `anvil_serving/router/front_door.py`

## Context

T014 (the [single tailnet DNS endpoint runbook](../TAILNET-ENDPOINT-RUNBOOK.md), shipped
in PR #244) established the goal of **one front door, one name, one token**: every serving
surface — chat, embeddings, rerank, the routed OCR/vision presets — is published from the
router's single stdlib front door, reached through the host's **one MagicDNS name**. That
runbook was explicitly *document-only* for two pieces it identified but did not build:

1. **TLS / a single HTTPS listener** via `tailscale serve`, so a caller that requires
   `https://` reaches the same name.
2. **ComfyUI under the same name** — its web UI serves on `127.0.0.1:8188` and its live queue
   needs **WebSockets**. A stdlib reverse proxy in Python would have to re-implement the WS
   upgrade/pump; Tailscale Serve proxies WebSockets natively.

Several hard constraints bound the option space:

- **The `/v1` OpenAI/Anthropic contract is the product.** The router
  (`anvil_serving/router/front_door.py`) parses Anthropic Messages and OpenAI Chat
  Completions, streams SSE, and enforces the bearer token. Nothing may rewrite or reshape a
  `/v1` request or response — preserving OpenAI/Anthropic compatibility is non-negotiable.
- **The router is stdlib-only by design** (`http.server` + `urllib`; no FastAPI, no aiohttp —
  see the gotchas in `CLAUDE.md` and ADR-0004). Adding a WS-capable reverse proxy in Python
  would either pull in a dependency or hand-roll a WS pump in the inference gateway.
- **The operator may already own tailnet edge state.** A node can already have its own
  `tailscale serve` mappings (e.g. a dashboard parked at `/`). Anything anvil-serving does to
  the edge must be **additive** and must never clobber an operator-set mapping.

We need something to *own* the tailnet edge: bind the one MagicDNS name and path-route it to
the right local service, without touching the router's request path or its dialects.

## Considered options

1. **Add a reverse proxy inside anvil-router.** Rejected. It fights the stdlib/no-aiohttp
   constraint, mixes an L7 edge concern into a pure inference gateway, and puts hand-rolled
   WebSocket proxying (for ComfyUI) directly in the process that must never risk the `/v1`
   contract. Every line of proxy code added next to the front door is a line that could
   regress OpenAI/Anthropic compatibility.

2. **Leave it document-only (status quo after T014).** Rejected. The runbook already proved
   the shape; the operator wants a managed, idempotent, dry-runnable verb rather than
   copy-pasted `tailscale serve` commands that are easy to get subtly wrong (and easy to
   clobber an existing mapping with).

3. **A new anvil-serving verb group that renders/applies a `tailscale serve` config.**
   Chosen. anvil-serving owns the edge as a thin, stdlib-only manager over the Tailscale CLI;
   Tailscale does the WS-capable proxying. The router is untouched.

## Decision

anvil-serving **owns the Tailscale tailnet edge** through a new `edge` verb group
(`anvil-serving edge {render,status,up,down}`) implemented in `anvil_serving/edge.py`. It
manages a `tailscale serve` configuration that binds the host's single MagicDNS name and
path-routes it:

| Mount | Target | Surface |
|-------|--------|---------|
| `/v1` | `127.0.0.1:8000` | The **existing anvil-router, COMPLETELY UNCHANGED** — the whole OpenAI/Anthropic inference surface. |
| `/comfyui` | `127.0.0.1:8188` | ComfyUI, whose live queue needs WebSockets (handled natively by Tailscale Serve). |

Additional `path -> local port` mappings (future dashboards, the anvil dashboard on `:8766`,
…) are **config-driven**, not hardcoded — via `[edge.routes]` in a TOML file or repeatable
`--map MOUNT=TARGET` overrides.

Design commitments:

- **The edge is a pure L7 path-router in front of the unchanged router.** Each managed mount
  forwards its path to the target verbatim (`tailscale serve --set-path=/v1` appends the mount
  to the MagicDNS base URL and proxies the request through), so `/v1/models` reaches the
  router as `/v1/models`. The router's request path and dialects are never touched. The
  direct router binding (e.g. `100.87.34.66:8000`) is left as-is.
- **Stdlib-only, no new runtime deps.** `edge.py` renders and applies `tailscale serve`
  invocations via `subprocess` and parses `tailscale serve status --json`; it adds **no proxy
  server in Python**. This keeps the router a pure inference gateway and puts the WS-capable
  proxying in Tailscale — the whole reason Serve is used instead of a stdlib WS proxy.
- **The MagicDNS name is read from Tailscale, never hardcoded** (`.Self.DNSName`, trailing dot
  stripped).
- **`--dry-run` / `render`** print the exact `tailscale serve` commands without applying them.
- **`up` is additive and idempotent**; it sets only the mounts this tool manages.
- **`down` removes ONLY the mappings this tool manages** — and only when a managed mount is
  currently present *and* points at the configured target. It issues per-path
  `tailscale serve … off` invocations and **never** `tailscale serve reset`, so an
  operator-set mapping (e.g. a dashboard at `/`) is never clobbered.

## Consequences

- A new `edge` verb group ships under "Control plane & integrations", backed by
  `anvil_serving/edge.py`, with render/apply/status/config-parse tests. The
  [T014 runbook](../TAILNET-ENDPOINT-RUNBOOK.md) is updated to point at the verb, superseding
  its *document-only* note for the `tailscale serve` + ComfyUI-path sections.
- The router keeps its single responsibility. `front_door.py` is unchanged; the `/v1`
  OpenAI/Anthropic contract is preserved because the edge rewrites nothing.
- ComfyUI's base-path caveat from the runbook still stands: ComfyUI serves absolute asset
  paths, so the `/comfyui` prefix may need a matching ComfyUI base-path before its UI loads
  end-to-end. A clean `502`/connection-refused when ComfyUI is down is expected passthrough,
  not an edge failure.
- Tailscale ACLs remain the outer boundary and the router's bearer token the inner one
  (ADR-0004); the edge changes neither. Tailscale terminates TLS but forwards the
  `Authorization` header untouched.
- Because the edge and any operator-owned `tailscale serve` mappings share one node-level
  serve config, `up`/`down` are deliberately scoped to managed mounts only. Applying the edge
  on a node with an active operator session is safe *only* as a purely additive change; when
  that cannot be guaranteed, stop at `render`/`--dry-run`.
