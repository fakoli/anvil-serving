# Deployment security model

What operators must know before exposing Anvil Serving beyond one machine. To report a
vulnerability, see [SECURITY.md](https://github.com/fakoli/anvil-serving/blob/main/SECURITY.md).

## Transport: no TLS

The first-party HTTP services (router, controller, Anvil Workbench (`dashboard serve`) telemetry server, voice
Realtime server, model-swap multiplexer) speak plain HTTP and do not terminate TLS. Traffic that
crosses a host boundary must ride a private mesh such as Tailscale or a TLS-terminating
proxy. Prefer publishing a loopback listener with Tailscale Serve over binding every
interface; see [Private networking with Tailscale](TAILSCALE-NETWORKING.md). Network identity is
defense in depth on top of tokens, not a replacement.

## Default binds

| Service | Default bind | Authentication |
|---|---|---|
| Router | `127.0.0.1:8000` | Optional `[server].auth_env` |
| Controller | `127.0.0.1:8765` | Token required unless `--allow-unauthenticated-loopback` |
| Anvil Workbench (`dashboard serve`) | `127.0.0.1:8766` | Optional `--auth-env` |
| Voice Realtime | `127.0.0.1:8765` (`voice.realtime_host`/`realtime_port`) | Optional `realtime_token_env` |
| Model-swap multiplexer | `127.0.0.1:8000` | None |

**Listeners with no token must stay on loopback.** The `Host` checks below stop DNS rebinding
from a browser; they do not stop a direct peer on the network. The multiplexer has no token at
all and only prints a warning when bound to a non-loopback address, so any reachable peer can
load and evict GPU models.

## Router

- With `[server].auth_env` set, every route except `GET /healthz` needs the token as a bearer
  token or `x-api-key`.
- `router run` refuses a non-loopback bind without `auth_env` (exit 2) unless
  `--allow-unauthenticated-public-bind` is passed. Use that flag only on an isolated network.
- Without a token, the router answers 403 `forbidden_host` unless the `Host` header names
  `127.0.0.1`, `[::1]` or the exact `--host` value. This blocks DNS rebinding. The dashboard
  telemetry server (no token, no web console), the multiplexer, and the voice Realtime
  server (no `voice.realtime_token_env`) apply the same rule.
- With a wildcard bind (`--host 0.0.0.0`), only loopback `Host` names pass, so remote clients
  that use a hostname or address get 403 even with `--allow-unauthenticated-public-bind`. Bind
  a specific address, or set `auth_env`.
- See [Running without a token](CONFIGURATION.md#running-without-a-token).

## Voice WebSocket origins

The Realtime server rejects a request carrying a browser `Origin` that is not in
`voice.realtime_allowed_origins` (empty by default), with or without a token. Requests with no
`Origin` header, such as native clients, are accepted. The introspection routes `/pool` and `/usage` require the token when one is configured.
`GET /healthz` needs no token and returns a constant `{"ok": true}` for readiness probes; the
`Host` rule still applies to it. See
[Browser origins and Host checks](VOICE.md#browser-origins-and-host-checks).

## Controller and MCP

- **The controller token is host-level authority.** The reference compose file
  (`examples/primary-node/docker-compose.controller.yml`) mounts `/var/run/docker.sock` into the
  controller container, so anyone holding the token can drive Docker and should be treated as
  root on that host. Keep the controller on loopback or a private mesh and protect the token.
- **`confirm=true` and dry-run are operator safety gates, not access control.** They stop
  accidental mutation and keep a preview step; any caller authorized to call the tool can
  supply the confirmation.
- **The legacy controller token is unscoped.** It authenticates a "legacy" principal that may
  call every tool that declares no scope (and the media scopes) without a scoped-policy entry.
  Scoped credentials from the authorization policy are limited to their declared scopes
  (for example `workloads:read`). Hand scoped credentials to clients that need less, and keep the
  legacy token to operators.
- **Unauthenticated controller mode.** With `--allow-unauthenticated-loopback` every caller is
  the full-authority legacy principal. The controller applies a Host guard in that mode: a
  foreign `Host` gets 403 on every route, and a `POST` carrying a non-loopback browser `Origin`
  (or duplicate `Origin` headers) gets 403. This stops DNS rebinding from a browser, not a
  direct local process or network peer. Configure the token anyway and avoid the flag on shared
  hosts.
- Bind safety: a public address or wildcard (`0.0.0.0`) needs `--allow-public-bind` even with a
  token. Non-loopback binds always require the token. `--allow-operation` (repeatable) restricts
  the controller to declared operations. The reference compose file binds `0.0.0.0` inside the
  container and publishes `127.0.0.1:8765` on the host (`ANVIL_CONTROLLER_PUBLISH` overrides).

## Environment files

The router and the controller resolve token references from the process environment, then
`$ANVIL_SERVING_HOME/.env`, then `~/.env`. `~/.env` is read by default; set
`ANVIL_SERVING_NO_HOME_ENV=1` to exclude it. The Workbench `--auth-env` and voice
`realtime_token_env` read the process environment only. Keep these files out of Git and mode 0600.

## Anvil Connect sessions

- The Connect browser session lasts 8 hours by default and is configurable from 60 seconds to
  24 hours (`gateway.browser_session_lifetime_seconds`). It is independent of the identity
  provider's login lifetime; changes at the IdP do not shorten an existing Connect session.
- Connect logout invalidates that human's Connect sessions and cancels admitted streams. It
  does not end the Authelia session, and a later sign-in can reuse it. End the Authelia session
  separately when a full sign-out matters.
- See [Anvil Connect](ANVIL-CONNECT.md) and [identity](ANVIL-CONNECT-IDENTITY.md).
