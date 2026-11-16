# Deployment security model

What operators must know before exposing Anvil Serving beyond one machine. To report a
vulnerability, see [SECURITY.md](https://github.com/fakoli/anvil-serving/blob/main/SECURITY.md).

## Transport: no TLS

The first-party HTTP services (router, controller, dashboard telemetry server, voice
Realtime server, model-swap multiplexer) speak plain HTTP and do not terminate TLS. Traffic that
crosses a host boundary must ride a private mesh such as Tailscale or a TLS-terminating
proxy. Prefer publishing a loopback listener with Tailscale Serve over binding every
interface; see [Private networking with Tailscale](TAILSCALE-NETWORKING.md). Network identity is
defense in depth on top of tokens, not a replacement.

## Router

- With `[server].auth_env` set, every route except `GET /healthz` needs the token as a bearer
  token or `x-api-key`.
- `router run` refuses a non-loopback bind without `auth_env` (exit 2) unless
  `--allow-unauthenticated-public-bind` is passed. Use that flag only on an isolated network.
- Without a token, the router answers 403 `forbidden_host` unless the `Host` header names
  `127.0.0.1`, `[::1]` or the exact `--host` value. This blocks DNS rebinding. The dashboard
  telemetry server (no token, no web console), the multiplexer, and the voice Realtime
  server (no `voice.realtime_token_env`) apply the same rule.
- See [Running without a token](CONFIGURATION.md#running-without-a-token).

## Voice WebSocket origins

The Realtime server rejects a request carrying a browser `Origin` that is not in
`voice.realtime_allowed_origins` (empty by default), with or without a token. Requests with no
`Origin` header, such as native clients, are accepted. See
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
- Non-loopback controller binds always require the token; an unauthenticated start is
  permitted only on strict loopback with `--allow-unauthenticated-loopback`.

## Environment files

Token references (`auth_env` and similar) resolve from the process environment, then
`$ANVIL_SERVING_HOME/.env`, then `~/.env`. `~/.env` is read by default. Set
`ANVIL_SERVING_NO_HOME_ENV=1` to exclude it. Keep these files out of Git and mode 0600.

## Anvil Connect sessions

- The Connect browser session lasts 8 hours by default and is configurable from 60 seconds to
  24 hours (`gateway.browser_session_lifetime_seconds`). It is independent of the identity
  provider's login lifetime; changes at the IdP do not shorten an existing Connect session.
- Connect logout invalidates that human's Connect sessions and cancels admitted streams. It
  does not end the Authelia session, and a later sign-in can reuse it. End the Authelia session
  separately when a full sign-out matters.
- See [Anvil Connect](ANVIL-CONNECT.md) and [identity](ANVIL-CONNECT-IDENTITY.md).
