# Workload visibility

Anvil Serving exposes one read-only workload schema across a router, one node
controller, a fleet controller, MCP, the CLI, and the local observability
dashboard. These views report bounded metadata from the component that owns
each workload. They do not expose prompts, responses, job payloads, credential
values, endpoint addresses, file paths, or raw provider errors.

Workload visibility is observation, not control. Reading a record cannot start,
stop, cancel, route, promote, qualify, or repair a workload. Continue to use the
type-specific lifecycle command and its normal confirmation boundary for those
actions.

The source implementation is still proceeding through consolidated review,
CI, packaging, and deployment gates. A documented command or schema does not
prove that a particular installed controller or router already serves it.

## What a record means

Every record has schema `anvil-workloads/v1` and one fixed owner/kind pairing:

| Owner | Kind | Authority represented |
| --- | --- | --- |
| `router` | `router-request` | Active router memory and its bounded terminal decision log. |
| `controller` | `controller-operation` | The controller operation store. |
| `benchmark` | `benchmark-job` | The benchmark job store. |
| `media` | `media-job` | The media job store. |
| `recipe` | `recipe-serve` | Bounded recipe configuration and managed status. |
| `manifest` | `recipe-serve` | Bounded manifest configuration and managed status. |

Host IDs are 1–64 ASCII characters. They start with a letter and otherwise use
letters, digits, `_`, or `-`. A record ID is an opaque,
deterministic digest of the host, owner, kind, and an owner-generated native
identity. It is not derived from a prompt, payload, credential, caller request
label, filename, or endpoint.

States are fixed to `checking`, `admitted`, `dispatched`, `streaming`, `queued`,
`running`, `terminal`, `configured`, `absent`, `unavailable`, or `unsupported`.
Phases are fixed to `checking`, `admitted`, `dispatched`, `streaming`, `queued`,
`running`, `completed`, `failed`, `cancelled`, `awaiting-approval`, `preparing`,
`submitting`, `configured`, `absent`, `unavailable`, or `unsupported`.

Outcome values are `success`, `error`, `cancelled`, `timeout`, `rejected`,
`disconnected`, `unavailable`, or `unknown`. Active records have no outcome:
configured and absent records also omit it. The `outcome` field is omitted
rather than serialized as JSON `null`. Optional progress is likewise omitted
when unavailable; when present it contains a bounded integer `completed`, a
bounded integer `total` or JSON `null` when the total is unknown, and the unit
`items`, `steps`, or `requests`.

### Provenance and time

Configuration, recorded state, a running process, and verified served identity
are different evidence:

- `source_authority` is one of `router-memory`, `controller-store`,
  `benchmark-store`, `media-store`, or `managed-status`.
- `observation_quality` is `recorded`, `configured`, `observed-running`,
  `healthy-identity`, `stale`, `absent`, or `inspection-error`.

`healthy-identity` requires both health and exact identity evidence. A health
response alone, a configured model name, or an observed running container does
not establish it. Recipe and manifest observations report at most
`observed-running`; unsupported native or ambiguous runtime observations remain
explicitly unsupported.

Record timestamps retain owner evidence: `created_at`, `updated_at`, and
`source_timestamp` are not rewritten when a later reader collects them. The
source or node `collection_timestamp` says when that envelope was assembled.
It does not make an older source observation fresh. Freshness uses source age,
with a 30-second default stale threshold, and preserves the original supported
state while labeling stale evidence honestly.

## Completeness and omissions

Each source and node reports `complete`, `partial`, or `unavailable`:

- `complete` means the bounded query completed with no known omissions.
- `partial` means safe records survived but a source, node, malformed peer, or
  bound prevented a complete answer.
- `unavailable` means the owner could not provide trustworthy records. It is
  not an empty or idle result.

`truncation.returned` is the number of returned records. A numeric
`truncation.omitted` is an exact known count. `truncation.omitted: null` means
the count cannot be known safely, for example while a source is unavailable or
anonymous saturated work could match the query. Never interpret unknown
omissions as zero.

One source returns at most 200 records and a node or fleet result at most 1000.
Fleet results retain an entry for every declared node, including sleeping,
unreachable, incompatible, or wrong-identity nodes. A fleet with failed nodes
is partial or unavailable; it is never silently presented as idle.

## Query filters

Router, controller, CLI, MCP, and dashboard reads share seven filters:

| Filter | Values |
| --- | --- |
| `owner` | One of the six owners above. |
| `kind` | One of the five kinds above. |
| `state` | One fixed state above. |
| `host` | One exact canonical host ID. This filters records; it does not select the controller. |
| `active_only` | Boolean; on the CLI, the valueless `--active-only` flag means true. |
| `recent_seconds` | Integer 1–86400; default 3600. |
| `limit` | Integer 1–1000; default 200. Source results remain capped at 200. |

By default a query includes active work, terminal work updated within the
recent window, and current configured, absent, unavailable, and unsupported
records. `active_only` excludes stale active observations. Filters are applied
before the result limit, then records sort by newest `updated_at` first with
record ID ascending as the tie-break. Repeated, unknown, null, malformed, or
abbreviated filters are rejected rather than guessed.

## CLI reads

The CLI never discovers an endpoint, credential, topology, SSH target, or node
identity for these commands. Supply all three connection facts explicitly.
The credential value is read from the named environment variable and is never
an argument.

Read one router node:

```bash
anvil-serving router workloads \
  --router-url http://127.0.0.1:8000/v1 \
  --auth-env ANVIL_WORKLOAD_TOKEN \
  --expected-node node-a \
  --owner router \
  --active-only \
  --limit 50 \
  --json
```

Read the fleet through its controller aggregator:

```bash
anvil-serving fleet workloads \
  --controller-url http://127.0.0.1:8765 \
  --auth-env ANVIL_WORKLOAD_TOKEN \
  --expected-node controller-a \
  --kind media-job \
  --recent-seconds 3600 \
  --limit 200 \
  --json
```

`--expected-node` authenticates the router or controller being contacted;
`--host` only filters returned workload records. Partial and unavailable
canonical snapshots are valid observations and exit successfully. Invalid CLI
usage exits 2. A failed authenticated HTTP snapshot exits 4 with a fixed error,
without reflecting the URL, credential reference, response body, or transport
exception.

## REST and MCP shapes

The authenticated router exposes `GET /v1/workloads` using the same seven query
fields. Its `NodeResult` contains the expected `host`, status and collection
timestamp, but exactly one source: the router. The controller's reserved
`node_workloads` operation composes all six owner sources into a `NodeResult`.
A fleet result instead contains fleet status and collection timestamp, an
ordered `nodes` array, and fleet-level truncation. Records inside those
different envelopes use the same canonical record serialization.

Controllers reserve the read-only `node_workloads` and `fleet_workloads`
operations for REST `/tools/call` and the scoped controller MCP catalog. Tool
arguments contain only present filters; do not send absent options as `null`:

```json
{"owner":"router","active_only":true,"limit":50}
```

```json
{"kind":"media-job","recent_seconds":3600,"limit":200}
```

The first object is a valid `node_workloads` argument and the second a valid
`fleet_workloads` argument. MCP's JSON-RPC `id` remains protocol correlation in
the outer response only. It does not enter workload application data, audit
metadata, or storage. Do not add idempotency headers to workload reads: they are
sealed read operations and do not create controller operation records.

## Authorization

Every unified workload read requires an explicit per-client `workloads:read`
grant. This applies to router HTTP, controller REST and MCP, CLI reads, and the
dashboard. These credentials do **not** grant model-serving, bootstrap,
lifecycle, routing, promotion, or other controller authority.

The reverse is also true: a legacy router data-plane bearer, media-only token,
legacy controller token, or a token carrying only `node-admin:bootstrap` does
not grant `workloads:read`. Do not reuse an administrator or data-plane token
and assume broader authority. Configure a scoped client in the controller's
bounded authorization policy and reference its credential through the
documented environment or file-backed secret boundary.

## Dashboard

Enable workload reads on the existing local dashboard with exactly three
workload startup options:

```bash
anvil-serving dashboard serve \
  --host 127.0.0.1 \
  --port 8766 \
  --auth-env ANVIL_DASHBOARD_TOKEN \
  --workload-controller-url http://127.0.0.1:8765 \
  --workload-expected-node controller-a \
  --workload-authorization-policy /srv/anvil-serving/workload-authorization.json
```

`--auth-env` remains the independent dashboard telemetry authentication gate.
The workload panel locally checks the separate credential the caller enters
for `workloads:read`, then forwards that same caller credential to the declared
controller. The telemetry credential does not grant workload access, and the
workload credential does not grant telemetry access.

All three `--workload-*` options must be valid and present to enable workload
collection. If they are absent, incomplete, invalid, or collide with legacy
authentication material, the workload service remains disabled while the
existing telemetry dashboard remains usable. Reads then return a fixed denial;
they never fall back to discovery or an unauthenticated empty inventory.

Open the Workloads tab, enter the scoped credential, and choose **Connect**.
Filters trigger a new bounded read; tab or document hiding stops polling.
Choose **Disconnect** to abort the current generation, clear rendered records,
and remove the in-memory credential. The workload credential is not written to
cookies, local storage, session storage, URLs, rendered text, or logs.

Dashboard telemetry and workload results remain distinct. “Aggregate GPU
memory” is a sum for display, not unified GPU capacity, and per-card graphs use
observed host/card identity rather than inferred fast/heavy roles. Hardware
telemetry never manufactures workload identity or changes workload state.

## Troubleshooting without overclaiming

- **Usage rejected:** check the exact option name, enum spelling, host grammar,
  bounds, and duplicate filters. The fixed error intentionally omits operands.
- **Access denied:** the presented client must have `workloads:read`; a valid
  legacy, data-plane, media, or bootstrap credential is still insufficient.
- **Partial:** inspect the per-source and per-node statuses. Trust surviving
  records, but do not claim a complete inventory.
- **Unavailable:** verify the explicitly configured endpoint, expected node,
  source configuration, and service state through their owning safe commands.
  Do not reinterpret the result as “no work.”
- **Stale:** the original source observation exceeded its freshness threshold.
  A recent collection timestamp does not refresh it.
- **Unsupported:** the owner retained an unknown or unsupported state without
  exposing the raw value. It is not evidence of failure or readiness.

Workload output is evidence about bounded observation only. Configuration is
not runtime state; `observed-running` is not health; health is not exact served
identity; and none of these observations is qualification, promotion, or proof
of deployment.
