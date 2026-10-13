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
