# ADR-0050 — The router relays `/v1/realtime` WebSockets

- **Status:** Proposed
- **Date:** 2026-11-23
- **Amends:** [ADR-0019](0019-anvil-serving-owns-the-tailnet-edge.md), for `/v1/realtime` only
- **Relates to:** [ADR-0004](0004-router-as-a-service-containerized-and-authed.md),
  [ADR-0024](0024-normalized-audio-gateway.md), [ADR-0036](0036-voice-relocation-and-qualification-host.md),
  `anvil_serving/router/realtime.py`, `anvil_serving/router/front_door.py`

## Context

ADR-0019 decided that the router does no WebSocket proxying. Tailscale Serve
carries WebSockets for surfaces such as ComfyUI, and the router stays a
stdlib HTTP gateway for the `/v1` request/response contract.

Realtime voice does not fit that split. An OpenAI Realtime client connects to
`/v1/realtime` and expects the same name, token and key policy as every other
`/v1` surface. Today it has to reach the Anvil Voice proxy directly, on a
different host and port, with a separate bearer token and none of the router's
device-key grants, rate limits or admission accounting. A path mount on
Tailscale Serve cannot check a router key, swap credentials or count a session
against router drain, because Serve forwards `Authorization` untouched (ADR-0019).

The constraints from ADR-0019 still hold: no third-party dependency, no
rewriting of any request/response `/v1` route, and no frame parsing in the
gateway.

## Considered options

1. **Keep realtime on Tailscale Serve.** Rejected. It cannot authenticate a
   router key or replace the caller's credential with the upstream's, so every
   realtime client would need the voice proxy's own token.
2. **Parse and re-emit Realtime events in the router.** Rejected. It puts a
   second protocol implementation in the inference gateway, the risk ADR-0019
   warned about.
3. **An authenticated byte relay for one path.** Chosen.

## Decision

`GET /v1/realtime` with a WebSocket upgrade is handled by the router when
`[[router.realtime_routes]]` is configured. Nothing else in ADR-0019 changes.
ComfyUI and other WebSocket surfaces stay on Tailscale Serve, and the router
still never proxies any other upgrade.

For each upgrade the router:

1. checks the caller exactly as for other `/v1` routes: the legacy token, a
   device key granted the `/v1/realtime` path *and* the route id as a model, or
   a policy token with `inference:use`;
2. resolves the route from `?route=<id>` or the single default, never from a
   caller-supplied address;
3. takes an `audio` router-admission permit and a slot from
   `realtime_max_concurrency`. Quiesce therefore refuses new sessions, and
   drain waits for open ones;
4. checks the route's token-free `health_path`, with the result cached briefly;
5. opens the upstream upgrade with only the route's `auth_env` bearer token.
   It drops the caller's `Authorization`, `Origin` and cookies, any
   `openai-insecure-api-key.*` subprotocol, and extension offers;
6. validates the upstream `101`: `Upgrade`, `Connection`, `Sec-WebSocket-Accept`
   for the caller's key, and a subprotocol that the caller offered. It then
   sends its own `101` to the caller and copies bytes both ways until either
   side closes or the session idles out.

Every refusal is a JSON error sent before any `101`. Base URLs pass the same
SSRF rules as audio routes. A route whose `auth_env` is unset is left unbound,
and the router never falls back to another route.

## Consequences

- A long-lived socket now lives in the router process, bounded by its own cap,
  an idle timeout and admission. It never uses the general request slots.
- Realtime sessions count under the existing `audio` work family. The
  maintenance contracts' eight families stay unchanged.
- Device keys gain one grantable path, `/v1/realtime`, which is GET only.
- Capacity-based route selection is deferred: routes are chosen by id or
  default only.
- The router relays bytes and records route, duration and byte counts. It
  never inspects or logs frame content.
