# One tailnet endpoint

**One front door, one name, one token.** The Anvil Serving gateway publishes
chat, embeddings, rerank, OCR, and vision from one HTTP front door on
`127.0.0.1`. Tailscale Serve projects the reviewed paths from that loopback
listener onto the owning node's private MagicDNS HTTPS name. There is no
separate router, embeddings, or OCR endpoint to resolve, no per-surface
hostname, and no raw model port exposed to peers.

This runbook is the operational companion to
[Private networking with Tailscale](TAILSCALE-NETWORKING.md). It records the
exact MagicDNS form, managed `tailscale serve` mappings, dated direct-bind
verification, and ComfyUI UI caveats. The Serve-to-loopback shape is preferred
for multi-device use; a direct bind to a tailnet IP remains an explicit
alternative.

> Update (ADR-0019): the `tailscale serve` + ComfyUI path-routing sections below are **no
> longer document-only**. anvil-serving now **owns the tailnet edge** through the
> `anvil-serving edge {render,status,up,down}` verb group — a stdlib-only manager that
> renders and applies exactly this `tailscale serve` config (`/v1` → the unchanged router,
> `/comfyui` → ComfyUI). Prefer the verb over hand-run `tailscale serve` commands: it is
> idempotent, dry-runnable, and removes only the mappings it manages. The commands below
> remain accurate as the underlying mechanism the verb renders. See
> [ADR-0019](adr/0019-anvil-serving-owns-the-tailnet-edge.md) and
> [`edge` in the CLI reference](cli/control-plane.md#edge).
>
> Directly binding the front door to the tailnet is still a `router run
> --host` choice. It is orthogonal to the preferred `edge` shape, which keeps
> the process on loopback and fronts it under the one private name.

---

## The decision: no separate router endpoint

The front door is one stdlib `ThreadingHTTPServer`
(`anvil_serving/router/front_door.py`) that multiplexes every wire surface behind one
bind address and one bearer token (ADR-0004):

| Method | Path | Surface |
|---|---|---|
| GET | `/healthz` | Liveness (token-free — container healthchecks) |
| GET | `/v1/models` | Capability-alias and purpose-model discovery |
| POST | `/v1/chat/completions` | OpenAI chat through a configured capability alias |
| POST | `/v1/messages` | Anthropic Messages |
| POST | `/v1/embeddings` | Embeddings purpose model (routed by model name) |
| POST | `/v1/rerank` | Rerank purpose model (routed by model name) |
| GET | `/v1/decisions` | Recent routing decisions |

Because chat, embeddings, rerank, and the OCR/vision aliases are *already* one
server, publishing them needs no second application endpoint. The managed edge
maps the existing `/v1` surface to its loopback listener. A dedicated
embeddings host, OCR proxy, or separate DNS name would duplicate auth, TLS,
and discovery and fragment the one credential the operator has to rotate.
**The single endpoint is the whole design.**

The `GET /healthz` `routes` list is the live proof that one server carries the unified
surface — see the verification below.

---

## The MagicDNS form

The dated direct-bind reference used the synthetic Tailscale IPv4
`100.64.0.10:8000`. The preferred managed edge uses HTTPS on the same node's
MagicDNS name and proxies to loopback. In either shape, read the current name
from Tailscale rather than hardcoding a host identity:

```bash
tailscale status --json | python -c "import sys,json;print(json.load(sys.stdin)['Self']['DNSName'])"
# -> node-a.example.ts.net.
```

`.Self.DNSName` carries a trailing dot (a fully-qualified DNS name); strip it
for URLs. The preferred managed endpoint is therefore:

```
https://node-a.example.ts.net
```

Any peer allowed by tailnet policy can resolve the MagicDNS name. The useful
forms are:

| Form | Endpoint | When |
|---|---|---|
| **MagicDNS HTTPS** (preferred) | `https://node-a.example.ts.net` | Tailscale Serve to loopback; human-facing and certificate-backed. |
| Direct tailnet IPv4 | `http://100.64.0.10:8000` | Explicit direct-bind deployments only. |
| Direct tailnet IPv6 | `http://[<tailnet-ipv6>]:8000` | Explicit IPv6 direct-bind deployments only. |

### Alternative: bind the front door directly to the tailnet

The front door binds `127.0.0.1` by default (never `localhost`—that triggers a
~21 s IPv6 stall on Windows). Prefer leaving that default in place and using
the managed edge. When direct binding is explicitly required, bind to the
tailnet interface at start time—**do not** change the default; pass `--host`
on the `router run` verb:

```bash
# Bind all interfaces (tailnet included). REQUIRE token auth when non-loopback.
anvil-serving router run --host 0.0.0.0 --port 8000 --config <config.toml>
#   ...or pin exactly the tailnet IP so nothing else is exposed:
anvil-serving router run --host 100.64.0.10 --port 8000 --config <config.toml>
```

A non-loopback bind is gated by `_warn_if_public_bind`
(`anvil_serving/router/serve.py`): with `[server].auth_env` configured it prints a
NOTE and proceeds; **with no auth it prints a loud WARNING** because the bind would
expose the front door with no credential. Always run the tailnet bind with
`[server].auth_env` set (see the token section) — MagicDNS reachability and token auth
are a pair, never one without the other.

> Tailscale grants or legacy ACLs remain the outer boundary: only allowed
> peers can reach
> `100.64.0.10:8000` at all. The bearer token is the inner boundary. T014 changes
> neither — it documents binding the existing server to the interface Tailscale already
> owns.

---

## Token auth

Every surface except `GET /healthz` requires the bearer token (constant-time compared;
never logged). Keep the current token in the private operator environment. If its value is
quoted there, strip the quotes when exporting a single value:

```bash
# Load ANVIL_ROUTER_TOKEN, stripping surrounding quotes if present.
TOKEN=$(grep '^ANVIL_ROUTER_TOKEN=' ~/.env | head -1 | cut -d= -f2- | sed "s/^['\"]//;s/['\"]\$//")

curl -H "Authorization: Bearer $TOKEN" http://node-a.example.ts.net:8000/v1/models
# Bearer or x-api-key are both accepted:
curl -H "x-api-key: $TOKEN"           http://node-a.example.ts.net:8000/v1/models
```

The router is started with `[server].auth_env = "ANVIL_ROUTER_TOKEN"`; the secret is read
from the environment **once** at start (never per request).

---

## Dated verification through MagicDNS

The sanitized 2026-07-13 public evidence records requests through
`node-a.example.ts.net:8000`, not a raw IP. It is a dated reference, not a claim about
current operator state. See the [endpoint verification](findings/2026-07-13-t014-tailnet-endpoint/live-verification.txt)
and [routed OCR capture](findings/2026-07-13-t014-tailnet-endpoint/routed-ocr-via-magicdns.txt).

> **Publication redaction:** The original host-specific Tailnet IPv4, Tailnet IPv6,
> and MagicDNS identities were replaced with the repository's synthetic IPv4,
> explicit IPv6 placeholder, and example DNS name. HTTP results, model identity,
> measurements, and event ordering are unchanged.

### 1. Discovery + auth enforcement — `GET /v1/models`

```
$ curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOKEN" \
    http://node-a.example.ts.net:8000/v1/models
200
$ curl -s -o /dev/null -w "%{http_code}" \
    http://node-a.example.ts.net:8000/v1/models      # no token
401
```

`GET /healthz` confirms one server carries every route:

```json
{"status": "ok", "dialects": ["anthropic", "openai"],
 "routes": ["/v1/chat/completions", "/v1/decisions", "/v1/embeddings",
            "/v1/messages", "/v1/rerank"]}
```

### 2. Embeddings — `POST /v1/embeddings`

```
$ curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"model":"qwen3-embedding-0.6b","input":"tailnet endpoint smoke test"}' \
    http://node-a.example.ts.net:8000/v1/embeddings
# HTTP 200
# object=list  model=qwen3-embedding-0.6b  dim=1024
# embedding[:3]=[0.0365, 0.0477, -0.0116]  usage={prompt_tokens:6, total_tokens:6}
```

Routed **by model name**: an unknown embedding model is a clean 404 that names the served
model — it never falls through to chat routing.

### 3. OCR capability alias — `POST /v1/chat/completions` (`model: "vision.ocr"`)

An OCR request is a chat completion naming the `vision.ocr` alias with an `image_url` content
part; the router sends it to the resident PaddleOCR-VL serve — no separate OCR endpoint.

```
$ python ocr_probe.py docs/assets/explainer-quality-gate.png   # model="vision.ocr" + data: image_url
# HTTP 200  model=vision.ocr  finish_reason=stop
# usage={prompt_tokens:1215, completion_tokens:15, total_tokens:1230}
# extracted: "measured score 98% / GATE / QUALITY ..."
```

All three acceptance surfaces (`/v1/models`, `/v1/embeddings`, a routed OCR request)
resolve and authenticate through the **one** MagicDNS name. The hermetic half of this
proof — one server + one token serving discovery, embeddings, and the OCR alias — lives in
`tests/router/test_front_door.py::test_t014_single_endpoint_serves_every_surface_under_one_token`.

---

## HTTPS via `tailscale serve` (managed by `anvil-serving edge`)

Plain HTTP over the tailnet is already encrypted by WireGuard, so TLS is optional. When a
caller *requires* `https://` (a client that refuses plaintext, or a browser surface),
front the port with `tailscale serve` so Tailscale terminates TLS with a MagicDNS
certificate and proxies to the loopback front door:

```bash
# Terminate HTTPS on 443 for this node's MagicDNS name and proxy to the front door.
# Bind the front door to 127.0.0.1 in this mode — tailscale serve reaches it locally,
# so it need NOT be bound to the tailnet interface.
tailscale serve --bg --https=443 http://127.0.0.1:8000

# Result: https://node-a.example.ts.net/v1/models  (443, valid TS cert)
tailscale serve status          # inspect the mapping
```

Trade-off: `tailscale serve` binds one HTTPS port per node, so HTTPS + ComfyUI path
routing (below) share the same `tailscale serve` config. Token auth is unchanged —
Tailscale terminates TLS but forwards the `Authorization` header untouched.

> Prefer `anvil-serving edge` over hand-run commands (ADR-0019): `edge up` renders and
> applies exactly this config idempotently, and `edge down` removes **only** the mappings it
> manages (per-path `… off`), never `tailscale serve reset`, so an operator-set mapping is
> never clobbered. Applying still mutates tailnet Serve state and requires
> HTTPS certificates to be enabled for the tailnet.

---

## ComfyUI UI under the same name (managed by `anvil-serving edge`)

The ComfyUI tenant (gpu-reservations:T012) serves its web UI on `127.0.0.1:8188`
(loopback-only; `COMFYUI_PUBLISH` is the tailnet opt-in). To reach both the router API and
the ComfyUI UI under the **one** MagicDNS name, `anvil-serving edge` uses `tailscale serve`
**path routing** so each path proxies to its own loopback service. The verb renders exactly
these commands (the default route map is `/v1` → router, `/comfyui` → ComfyUI):

```bash
anvil-serving edge render
# $ tailscale serve --bg --https=443 --set-path=/v1      http://127.0.0.1:8000/v1
# $ tailscale serve --bg --https=443 --set-path=/comfyui http://127.0.0.1:8188

anvil-serving edge up --confirm   # additive; only the mounts this tool manages

# -> https://node-a.example.ts.net/v1/models       (router front door, path unchanged)
# -> https://node-a.example.ts.net/comfyui/        (ComfyUI UI)
```

Tailscale strips the matched mount, then joins the remainder to the target URL.
The `/v1` target must therefore end in `/v1` to preserve `/v1/models` and the
inference endpoints. Built-in and port-only `/v1` routes now include that
target path. Explicit full URLs remain authoritative; for example,
`--map /v1=http://127.0.0.1:8000/v1`. Other numeric mounts target the service
root. Add further paths via `[edge.routes]` or `--map /dashboard=8766`.

**2026-09-05 correction:** older renders omitted the target's `/v1` suffix.
`edge status` now reports an older mapping as drift, and `edge down` does not
remove it implicitly. Review `edge up --dry-run` before applying the corrected
target, then verify authenticated `/v1/models`. Source verification and the
[fix ticket](https://github.com/fakoli/anvil-serving/blob/main/.tickets/2026-09-05-edge-v1-prefix.md) do not prove an existing
tailnet mapping has been updated.

Caveats to validate before adopting:

- **ComfyUI base-path support.** ComfyUI serves absolute asset paths (`/`); behind a
  `/comfyui` prefix its static assets/websocket may 404 unless ComfyUI is started with a
  matching root/base-path or the proxy rewrites. Verify the UI loads end-to-end (assets +
  the `/ws` websocket) before relying on the prefix; if it does not, give ComfyUI its own
  MagicDNS path at the root of a *second* serve config or a dedicated port instead.
- **The ComfyUI tenant is on-demand and evicts a serve.** It is not always resident; path
  routing to `:8188` only works while the tenant is up (`up comfyui --evict`).
- **Auth.** The router enforces its bearer token; ComfyUI has none. If ComfyUI is exposed
  on the tailnet, its only boundary is the Tailscale grant or legacy
  ACL—scope tailnet access
  accordingly, or keep ComfyUI loopback-only and reach it via `tailscale serve` from a
  trusted peer.

> A clean `502`/connection-refused on `/comfyui` when the ComfyUI tenant is down is expected
> passthrough, not an edge failure.

---

## Rollback / teardown

`anvil-serving edge down --confirm` removes only the mappings the verb manages.
If a mapping was applied by hand, remove that exact HTTPS path rather than
resetting every Serve mapping on the node:

```bash
tailscale serve --https=443 --set-path=/v1 off
tailscale serve --https=443 --set-path=/comfyui off
# Rebind the front door to loopback-only by restarting `router run` with --host 127.0.0.1.
```

The router and model-serve lifecycle is managed **only** through the anvil-serving
`router` / `serves` / `voice` verbs (with `--confirm`), never raw `docker` — see
[Operator playbooks](OPERATOR-PLAYBOOKS.md).

---

## See also

- [Private networking with Tailscale](TAILSCALE-NETWORKING.md) — identity,
  grants, mobile access, and the Serve-to-loopback architecture.
- [Configuration reference](CONFIGURATION.md) — `[server].auth_env`, `[server].host`, and
  the tier/purpose-model keys.
- [ComfyUI migration runbook](COMFYUI-MIGRATION-RUNBOOK.md) — the ComfyUI tenant and its
  `127.0.0.1:8188` UI.
- [ADR-0004](adr/0004-router-as-a-service-containerized-and-authed.md) — front-door token auth.
- [ADR-0017](adr/0017-gpu-residency-reservations.md) — GPU residency reservations and the
  purpose-model surfaces (embeddings/rerank/OCR).
