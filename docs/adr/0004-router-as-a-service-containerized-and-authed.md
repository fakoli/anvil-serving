# ADR-0004 — Router as a service: containerized, network-facing, token-authed

- **Status:** **Accepted** (2026-07-01)
- **Date:** 2026-07-01
- **Relates to:** the `router-service` PRD · [ADR-0002](0002-serves-are-compose-defined.md) (serves are
  compose-defined — this extends it to the router) · [ADR-0003](0003-portable-defaults-and-generic-onboarding.md)
  (portable-by-default) · `SECURITY.md` · `anvil_serving/router/front_door.py`, `config.py`, `secrets.py` ·
  CLAUDE.md gotcha #1 (loopback bind) / #2 (stdlib-only).

## Context

The router (`anvil-serving router run`) runs as a **bare loopback process with no authentication** — SECURITY.md
says so explicitly, and a code check confirms `secrets.py` only *redacts* tokens from logs; the front door
validates nothing (the README even tells clients to send `ANTHROPIC_AUTH_TOKEN="$ANVIL_ROUTER_TOKEN"`, which
the router currently **ignores**). It is supposed to be "the auth/routing front that fronts the serves," but
it does not play that role. To make the cross-box topology (OpenClaw on Fakoli Mini → router → GPU serves on
fakoli-dark) work this session we had to (a) publish the **raw, unauthenticated** SGLang/vLLM serves on
`0.0.0.0` so Mini could reach them over Tailscale — a real exposure the repo's own loopback-only invariant
forbids — and (b) hand-roll a macOS **LaunchAgent** for keep-alive. The router→serve relay also crossed the
network, which is where the reachability/timeout fragility surfaced.

The GPU serves already run under Docker with `restart: unless-stopped` (ADR-0002); the router does not.

## Considered options

1. **Status quo** — loopback router on Mini, raw serves published on the LAN/tailnet. Rejected: exposes
   unauthenticated model servers, has no standard supervision, and keeps the router→serve hop cross-network.
2. **Reverse proxy (Caddy/nginx/Traefik) in front of the router for auth/TLS.** Rejected for v1: heavier
   infra and a second moving part for what a small in-process token check + Tailscale ACLs already cover.
3. **Containerize the router as the single network-facing, token-authed endpoint, co-located with the
   serves.** The serves go back to loopback/internal-only; only the router is published, behind a token;
   Docker provides keep-alive. Chosen.

## Decision

The router becomes a first-class, optionally-containerized **service** with built-in auth. Three parts:

- **Built-in token auth (opt-in).** The front door validates an incoming `Authorization: Bearer <token>`
  **or** `x-api-key: <token>` against the value of the env var named by `[server].auth_env` (convention:
  `ANVIL_ROUTER_TOKEN`), using a constant-time compare (`hmac.compare_digest`); mismatch/missing → `401`
  JSON. Auth is **off when `auth_env` is unset** (preserving today's loopback default). A `GET /healthz`
  liveness endpoint stays **unauthenticated** (for container healthchecks). The token secret is referenced
  by env-var NAME only — never stored in config — consistent with the tiers' `auth_env` contract, and it is
  redacted from logs by the existing `secrets.py` machinery.
- **Optional Docker deployment.** A repo-root `Dockerfile` (stdlib-only image, non-root, `HEALTHCHECK` on
  `/healthz`) plus a **compose service** with `restart: unless-stopped`, co-located with the serves on a
  shared Docker network. The router reaches the serves **by service name** over the internal network; the
  serves are **not published beyond loopback**; **only the router** is published, on a configurable bind
  address, behind the token. This makes the router the single auth boundary and restores the SECURITY.md
  loopback-only invariant for the raw serves.
- **The pip-install path is preserved.** Docker is an *additional* deployment option, exactly as ADR-0002
  framed it for the serves. The Python package stays **stdlib-only** and `pip install`-able; nothing is
  added to the hot path except the cheap constant-time auth check.

## Consequences

- **Keep-alive is standard** (Docker `restart: unless-stopped`) instead of a per-OS init unit; the router's
  lifecycle unifies with the serves' compose lifecycle (ADR-0002).
- **One authenticated endpoint** replaces N exposed model servers; the raw serves return to loopback/internal
  and the router→serve hop becomes local (the cross-network relay fragility is retired).
- **Mini repoints** its OpenClaw `anvil` provider from a loopback URL to `http://<fakoli-dark>:8000/v1` with a
  bearer header carrying `ANVIL_ROUTER_TOKEN`. The advise-and-defer 503→native-failover contract (ADR-0001) is
  unchanged.
- **New hot-path check:** every request is authenticated when a token is configured — constant-time, ~microseconds,
  no dependency. Defense-in-depth pairs it with Tailscale ACLs (network-level identity).
- **Extends, does not supersede,** ADR-0002/0003: it applies "compose-defined + portable-by-default" to the
  router itself and adds the auth model those ADRs did not cover.
- **Deferred (follow-up):** having the `deploy`/`init` generator emit the router service into the compose
  automatically (so the portable bring-up includes an authed containerized router out of the box).
