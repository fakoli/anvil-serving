# Native Memory browser contract

The native browser uses the existing authenticated `POST /v1/memory` device-key
endpoint. A Connect human-owned `memory.user` key must be separately issued with
current account generation/epoch checks. An Open WebUI token or Hindsight backend
key is not a native Memory credential. Enrollment and transport admission are
separate from this facade.

Native inventory request:

```json
{"alias":"memory.user","operation":"banks","arguments":{"native":true,"expected_principal":"human:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","limit":100,"offset":0}}
```

The result has `schema: anvil-native-memory/v1`, the verified `principal`, a
nullable `default_bank`, `total`, `limit`, `offset`, and `banks` with `bank_id` and
explicit `operations`. Only banks granting both `list` and `detail` appear.
Native inventory never uses the administrator's unrestricted upstream inventory.

All new operations require an explicit per-bank grant, including administrators.
Legacy default provisioning remains retain/recall/reflect. Deploy compatible
policy readers before deliberately adding new grants. Application roles and
retain permission do not authorize curation.

| Operation | Arguments | Behavior |
| --- | --- | --- |
| `list` | `limit` 1–100, `offset` 0–1000000, optional `type`, `state`, `q`, `tags`, `tags_match` | Defaults to active records, page size 50. Query at most 512 characters; 16 tags of 128 characters. Only `any_strict`, `all_strict`, `exact` tag modes. |
| `detail` | `id` canonical UUID | Reads live/archive record in the authorized bank. Missing or cross-bank ID is 404. |
| `invalidate` | `id`, nonempty `reason` ≤1024 characters | Separately granted PATCH to invalidated state. Also requires detail grant for preflight/reconciliation. |
| `restore` | `id` | Separately granted PATCH to valid state; also requires detail grant. |

Place `bank` beside `alias` in the envelope. The facade owns all upstream URLs,
headers and paths. List/detail allow no caller-selected backend or raw filters.
Responses are capped at 2 MiB. Raw fact text/context, type/state, bounded tags,
source IDs, document/chunk IDs and dates are projected. Inline source bodies,
history, arbitrary metadata, attachments and URLs are excluded. Observation
entities are excluded because Hindsight 0.10.1 can inherit them through source
references without a bank predicate. Source text must be requested through an
additional authorized bank-scoped detail call; do not resolve against the
upstream inline object.

Curation preflights a bank-scoped raw fact; observations fail before PATCH. Grants
are reread after preflight and before dispatch. The upstream curation API has no
atomic revision/ETag guard: this contract does not promise stale-write detection.
A lost PATCH acknowledgment must be reconciled through detail, never replayed
automatically. Invalidation/restoration can update embeddings and derived
observations; they are not presentation flags. Permanent deletion and general
text/entity editing are not exposed.

Offset pagination is not a stable snapshot. Native consumers deduplicate IDs and
provide explicit refresh. Cached records must be partitioned by origin, verified
human principal and bank, display their download date, and disable offline
curation. Revoked grants must remove the affected downloaded partition.

## Native OIDC gateway

The optional native Memory gateway lets one installed application reuse its
public-client OIDC sign-in without giving the device a router key. It is a
separate loopback service; it does not add OIDC handling to the Capability
Gateway and does not restart or reconfigure model serves. The bridge accepts
only `GET /v1/native/memory/capabilities` and `POST /v1/memory`.

The caller supplies the same provider access token used for native Agents.
Admission reuses the exact native Agents issuer, introspection, userinfo,
trusted-client, `openid` scope and fresh Connect authority projection checks.
The first personal deployment therefore requires the person to have the native
Agents resource grant as well as Memory bank grants. This is an intentional
intersection for the combined Agents and Memory application; it is not a
general Memory-only identity policy.

A successful capabilities response is closed and stable:

```json
{"version":1,"owner_id":"human:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","capabilities":["banks","list","detail","invalidate","restore"]}
```

The array describes the gateway surface. The `banks` result remains the live
authority for each bank's exact operations. `POST /v1/memory` accepts only
`memory.user` with those five operations and keeps the existing request and
response envelopes. Bank discovery must include `native: true` and the exact
OIDC-derived `expected_principal`. The gateway never accepts retain, recall,
reflect, MCP, a caller-selected upstream, or a caller-selected credential.

The server loads exactly one current `anvil-user-memory-key/v1` receipt through
systemd `LoadCredential=memory-key:/absolute/protected/principal-receipt.json`.
It never reads the root-only key directory. The protected gateway configuration
pins the complete credential SHA-256 and expected principal. Each request checks
the receipt schema, current expiry and admission generation before substituting
the key in a fixed loopback `/v1/memory` request. The router then independently
checks the key's owner generation/epoch, model and path grant, expiry, request
budget and current bank policy. A response is withheld if OIDC authority changes
while the router request is in flight.

The first implementation is intentionally one principal per process. Deploy a
separate dedicated service and credential for another principal. Do not grant a
shared process access to the key directory, copy an administrator key, or expose
the router/Hindsight credential to the client. A key rotation requires a newly
digest-pinned config and managed service restart; the old snapshot fails closed
at the router meanwhile.

Example protected configuration (all identity and digest values are operator
supplied outside Git):

```json
{
  "version": 1,
  "deployment_id": "personal",
  "listen_port": 9443,
  "expected_principal": "human:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "credential_name": "memory-key",
  "credential_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "router_origin": "http://127.0.0.1:8000",
  "timeout_seconds": 120,
  "auth": {
    "issuer": "https://identity.example.test",
    "introspection_endpoint": "https://identity.example.test/api/oidc/introspection",
    "userinfo_endpoint": "https://identity.example.test/api/oidc/userinfo",
    "introspection_client_id": "native-memory-introspection",
    "secret_file": "/run/anvil-native-memory/introspection-secret",
    "native_client_ids": ["anvil-ios"],
    "authority_file": "/run/anvil-native-memory/native-agents-authority.json",
    "resource": "native-agents",
    "source_resource": "pi"
  }
}
```

Run the installed package as
`python -I -m anvil_serving.native_memory_gateway --config /absolute/protected/native-memory.json`
under the managed unit `anvil-native-memory-personal.service`. The unit name and
`deployment_id` bind the accepted credential directory to
`/run/credentials/anvil-native-memory-personal.service`; another directory is
rejected. Use a distinct unprivileged account, a root-owned non-writable config,
`NoNewPrivileges`, a read-only filesystem, bounded memory/tasks, and network
policy limited to loopback plus the verified identity-provider endpoint. The
managed deployment must verify effective unit properties, exact configuration
and receipt digests, the private loopback target, and the HTTPS edge route before
publication.

Missing or rejected OIDC admission returns `401`; native clients keep downloaded
Memory data as dated offline content and offer sign-in again. A valid OIDC
identity that differs from the pinned principal/generation, or a router bank
denial, returns `403` and clients purge that principal/origin partition. A router
key `401` is converted to `503 memory_unavailable` because it is a server-side
credential rotation fault, not an OIDC failure. Router `404`, `422`, `429`,
`503`, and `504` remain typed Memory outcomes. The currently deployed router may
still return `503` for a missing record until its independently qualified 404
mapping is promoted.
