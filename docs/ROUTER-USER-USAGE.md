# Router caller and usage contracts, v1

Status: implementation contract for `router-user-usage:T001`. This document
freezes planned interfaces for R001-R020 and F001-F005. The inventory describes
existing source; new signatures, schemas, routes and telemetry below remain
implementation commitments. This document alone proves no shipped feature,
source acceptance, deployment or live qualification. Examples and identifiers
are portable and synthetic.

## Authority and ownership

The approved router-user-usage PRD controls R001-R020, F001-F005 and its retention
matrix. Attribution records the API permission that actually admitted a request.
It grants no new permission. Browser access, service credentials and optional
forwarded users remain distinct. Accounting is opt-in; enabled accounting commits
starts before dispatch. It neither chooses models nor promises full GPU
consumption, exclusive time, utilization, energy or exactly-once inference.

Product owns portable schemas, hardened authentication, ledger, diagnostic API,
CLI, telemetry and managed drain. Private operator configuration owns actual
bindings, topology, approved forwarding recipients and activation/rollback.
AI Infra owns repeatable WebUI/client and Grafana provisioning/native dashboards.
Runtime secrets/account state never enter any repository. No custom accounting/analytics service, permission engine, queue, ORM, JWT
framework, exporter daemon or Grafana plugin is required. Native observability
store separation below is required for the inspected OSS viewer boundary.

## Verified source inventory and reuse

The inventory was checked against the claimed source baseline
`3bc6c17afe42f5bd3acae3791954db29dc8c96aa`. Paths in this table are relative to
`anvil_serving/`. Line references locate existing seams, not permanent API
versions. Reinspect affected seams in each implementation task.

| Actual path | Reuse and required change |
|---|---|
| `router/front_door.py:498,543,667,773,2105` | `handle_one_request`, `_device_access`, `_reset_request_correlation`, `_authenticated`, `_post_inner` authenticate before dispatch. Carry one trusted snapshot, reset every keepalive, overwrite caller reserved metadata. Configured service `check_scope` currently permits chat routes/discovery and workloads reads; do not silently expand its authority to purpose/audio/memory. |
| `router/keys.py:47,336,436,480` | Frozen `Principal`; protected per-operation connection; authentication/admission. Principal currently lacks approval revision, typed binding and key lifetime/RPM snapshot. Reuse private-path/DACL/no-link guards, grants validator and supported CLI. |
| `router/connect_keys.py:80,104,134,152,164` | Additive v1-to-v2 migration, monotonic revision, approved-account checks and shared RPM. `owned_binding` validates four fields but returns three: preserve revision without removing generation/epoch/liveness checks. |
| `control_plane/authorization.py:91` | `AuthorizationPrincipal/Decision` authenticate client_id/scopes. Canonical non-secret digest must be derived from validated admitted policy, never private credential matchers/references. Existing duplicate-object and bounded-reader helpers remain authoritative. |
| `router/front_door.py:679,1285,2441`; `router/serve.py:198,253,909,918` | Unmanaged SSE/buffered and managed `DeliveryWorker`/`RouterWorkloadStream` paths all need the same durable lifecycle. Generation result and downstream delivery outcome differ; HTTP 200 is insufficient. |
| `router/front_door_runtime.py:34,74`; `router/request_control.py` | Worker owns generator/thread-local result, bounded two-frame queue, cancellation and finish callbacks. Release drain ownership after actual worker completion, not socket close or the one-second join timeout. Keep request deadline/admission behavior. |
| `router/backends/relay.py:278,715,925`; `router/backends/sse.py:76,195` | `_note_usage`, structured extraction and assemblers preserve reported usage. Add normalized per-direction provenance/cache creation without serializing response/tool/reasoning content. `StructuredResult.usage` is optional (`router/internal.py:128`), not measured zero. |
| `router/serve.py:730` | Completion decision collects prompt/output provenance and cache read, but is best-effort diagnostics. Add immutable accounting terminal callback independent of optional workload observation. |
| `router/front_door.py:1399`; `router/purpose.py:146,253,308` | Embedding/rerank dispatch is separate, model-name only, raw JSON relay. `_usage_prompt_tokens` currently collapses missing/invalid to zero; new accounting must distinguish unknown and not-applicable. Preserve wire response. |
| `router/front_door.py:1438`; `router/audio.py:364,466,584,695` | STT/TTS routes use `AudioGateway`, transport deadlines and route semaphore; audio is non-token unless an explicit backend contract reports token units. Track actor/grant and actual dispatch without token guesses from audio duration. |
| `router/front_door.py:637`; `router/memory.py:102`; `router/memory_mcp.py:111` | Memory requires device-key aliases/principal, separate transport/semaphore. Recall/retain/reflect may trigger remote model work, but current router response does not expose per-attempt inference usage. Track outer operation as non-token and remote consumption unknown, never fabricate child observations. Instrument any router-owned generated inference at its own dispatch seam. |
| `router/gateway.py:28`; `media/worker.py:69` | MCP/A2A media gateway uses existing caller context and durable jobs; worker reconciles history rather than starting inference. Inventory job owner/controller admissions separately. Observation subscriptions/artifact reads are not GPU dispatch. Do not add media grants to inference keys or count worker polling as model execution. |
| `router/workloads.py:445`; `router/front_door.py:1800` | Active `/v1/requests` diagnostic is a separate administrator projection. Closed generic `observability.workloads` stays identity-free. Idempotent workload finish is best-effort; it cannot replace ledger commit. |
| `router/decision_log.py:638,751`; `router/router_telemetry.py:304` | Bounded log/rotating JSONL and buffer gauges remain diagnostics, never retained counters. Persisted ledger snapshot supplies the new bounded sensitive projection. |
| `router/front_door.py:1492`; `router/serve.py:1418,1521`; `router_manage.py` | Existing managed transitions quiesce/drain tier/member chat admission only. T023 must provide a whole-router barrier covering purpose/audio/memory and internal/background dispatch. |

Internal inference inventory is explicit: direct chat has one selected upstream
attempt (replica selection still picks one); purpose has one selected upstream;
managed delivery worker executes that same chat attempt, not an extra attempt;
memory remote service work is outside router visibility until reported; media
job submission/continuation is owned by its existing controller. Any discovered
additional router-owned attempt must use the child contract below and enter the
inventory with source evidence before live rollout. No classifier/fallback is
introduced. Client background/title/tag requests arriving as independent HTTP
requests receive separate server IDs and count their own actual dispatch.

## Trusted immutable metadata (`router-usage/v1`)

Use frozen stdlib dataclasses/tuples; explicit serializers reject unknown fields.
The schema is a metadata projection, not request-body metadata. Authentication
produces it once after existing path/model/rate/account admission succeeds; carry
it through queued worker, route selection, finalization and active diagnostics.
Do not reconstruct a historical grant by joining current accounts.

| Type | Closed fields and invariants |
|---|---|
| `Actor` | `kind` human/service/unattributed; `id` trusted opaque string or null; `binding_revision` integer or null; optional Connect `generation`, `epoch`. Binding revision snapshots typed owner reassignment. Legacy/unbound has null owner and explicit confidence. |
| `EndUser` | `instance`, `issuer`, `subject`, `state` verified/verified_unmapped; optional fenced Connect `owner,generation,epoch,approval_revision`. Null for direct/service-only. No name/email/role. Instance comes from credential binding, never a token URL/name. |
| `EffectiveGrant` | `kind` connect/key_policy/configured_scope/legacy; `reference`, `revision` or `policy_digest`; exact admitted `models`, `paths`, `scopes`, `rpm`, `created_at`, `expires_at` as applicable. Connect includes `owner,generation,epoch,approval_revision`, key narrowing and account limits; configured scope contains authenticated `client_id` and scopes. Absent dimensions are null/empty according to type, never invented. |
| `CallerSnapshot` | `schema`, `credential_id`, `actor`, optional `end_user`, `grant`, `attribution_state` owned_human/owned_service/verified_forwarded/unbound/legacy. Credential ID is existing key/client ID, or `_legacy`; never token digest. |
| `RequestStart` | `request_id`, `run_id`, `accepted_at`, `caller`, `kind`, logical model/route, optional parent/attempt linkage. Server-generated UUID lineage; caller correlation has no identity/uniqueness authority. |
| `RouteAssociation` | Declared `route_id`, `backend_id`, `serve_id`, `resource_owner_id`, optional member; null when unavailable, never private URL/GPU UUID. Logical model comes from validated configured route, not arbitrary text label. |
| `Terminal` | `request_id`, `ended_at`, `dispatched`, `generation_outcome`, `delivery_outcome`, `outcome`, route association, `latency_ms`, optional wait/start/first-content timings, normalized tokens, fixed error code, applicability/coverage. No exception strings/finish text. |

Opaque actor/instance/grant references use trusted operator IDs: ASCII letters,
digits, `_:-.`, 1-128 bytes, no URLs/control characters. Credential/client IDs keep
existing 1-64 validator. Connect human/generation/epoch/revision keep existing
strict validators (generation decimal <=2^64-1; monotonic revision <2^53).
WebUI subject is 1-128 UTF-8 bytes, no control characters; retain signed opaque
subject only, not profile claims. Configured model/path lists reuse existing
validators (<=64 grants, <=128-character model); no wildcard or authority
expansion. Maximum serialized caller snapshot 16 KiB; at most 64 paths/models
and existing finite allowed scopes. Exceeding a bound is a fixed policy error,
not truncation of effective authority. Request metadata target <=32 KiB.

Non-secret configured policy digest is lowercase SHA-256 over UTF-8 canonical
JSON (`sort_keys=True,separators=(',',':')`) of `{schema_version:1,client_id,
scopes:sorted(scopes)}`. Add genuinely enforced non-secret constraints only when
part of the actual policy schema. Credential env/file references, values,
matchers and token hashes are excluded; changing credentials alone does not
change policy semantics. Key-policy reference is existing immutable key ID;
Connect grant reference includes owner generation/epoch/revision. Snapshot
narrower effective key scopes and both relevant limits, not merely approval
maxima. Configuration digest/binding revision cannot create API authority.

Allowlist boundaries: storage and admin responses serialize only these closed
fields; metrics select the finite dimensions below; public/log projection omits
actor/end-user/grant/key dimensions by default. No JWT/header/signature, bearer
or hash thereof, content/tool/profile, raw exceptions or private endpoint URLs.
Current display labels are resolved only in an independently authorized view;
missing/deleted mappings preserve opaque historical identity.

Successful admission must return the snapshot that actually authorized the
request, never enrich it using a later mutable binding read. Tracked paths opt in
with `KeyStore.authenticate(token, snapshot=True, check_owner=False)` to capture
the complete bounded candidate before admission. Default `snapshot=False`
preserves the existing `Principal` with its complete model/path grants and
Connect owner checks, without serializing accounting metadata. It remains usable
with legacy `admit(principal.key_id) -> int`, including valid grants whose full
accounting projection exceeds 16 KiB. Explicit tracked authentication refuses an
oversized full snapshot with a fixed policy error; it never truncates authority.
The snapshot flag must be a bool. Typed `bind_owner` validates the current Actor
and expected revision within its existing writer transaction independently of
full grant serialization, while retaining live ordinary-key and non-Connect
fences. Minimum shared
change: `KeyStore.admit(principal, requested_path, requested_model) ->
AdmissionDecision(retry_after, caller_snapshot)` replaces key_id-only integer
return for tracked paths. Within existing BEGIN IMMEDIATE, reread key/grants/
lifetime, typed binding revision, Connect approval four-field binding and limits;
compare authentication candidate revisions (CAS). Changed identity/policy denies
fixed `admission_policy_changed` before dispatch; do not silently relabel or
consume a second retry's rate tokens. Apply existing key/account buckets and
freeze caller_snapshot only on successful transaction commit. Non-Connect typed
rebinding uses expected binding revision; Connect external owner liveness checks
validate the same frozen generation/epoch with existing checker. Perform external checker against candidate frozen generation/epoch BEFORE the
final admission transaction; no transaction spans the external call. The final
transaction compares every candidate revision, then consumes rate buckets and
returns the CAS-consistent snapshot; failed CAS consumes no rate tokens. The
optional trusted `local_check(admitted_snapshot, locked_clock)` runs after full
CAS and before buckets. T008 uses it to revalidate the required local signed
assertion after writer contention; no remote checker runs in this callback. Current
external authority cannot be made transactionally atomic with local SQLite;
preserve existing liveness semantics and do not promise impossible cross-system
exactness.
Device model/path checks must use this admitted snapshot. Configured service
`check_scope` already returns client_id/scopes from immutable loaded policy;
compute digest from THAT policy/decision, not reload config later. JWT attribution
is appended only under the authenticated admitted credential binding revision;
changing binding concurrently refuses admission rather than selecting a new user.
Test concurrent rebinding/reapproval/revocation/digest change between auth,
external check and rate admission, proving stored start keeps exact admitted
snapshot and later history joins cannot relabel it.

## Credential-bound signed WebUI identity (T004)

`identity.verify_webui(headers, binding, now_utc) -> EndUser` runs only after
credential authentication. Binding selects dedicated narrow service credential,
trusted instance, exact issuer `open-webui`, protected signer reference and
`require_user=true`. Unrelated credentials do not gain forwarded attribution.
Separate explicit service-only credential covers detached no-user work; required
identity path never downgrades. No per-human authorization engine is added.

Fixed HS256 only; no algorithm/key URL negotiation or token-selected signer.
Exactly one `X-OpenWebUI-User-Jwt` header (case-insensitive get_all); duplicate,
comma-combined or missing header rejected. Compact JWT <=8192 ASCII bytes,
exactly three nonempty canonical unpadded base64url segments; decoded header
<=512 bytes, payload <=4096 bytes, signature exactly32 bytes. UTF-8 JSON objects
reject duplicate keys at every object level, invalid numbers/recursion and
unknown JOSE controls (`crit`, `jku`, `jwk`, `x5u`, key-selection fields).
Header admits `alg=HS256` and optional `typ=JWT` only. Verify constant-time MAC
on original encoded signing input. Dedicated signer minimum32 random bytes;
protected configuration reader owns access, no logging or Git material.

Require exact issuer, nonempty bounded string subject and integer (not bool)
`iat,exp`, `0<exp-iat<=300` seconds. Default clock skew30 seconds, configurable
0-30; `iat<=now+skew`, `exp>now-skew`. Treat malformed/expired as fixed401
`identity_invalid` before upstream calls. Native WebUI email/name/role claims may
be parsed within payload cap then discarded; no authority derives from them.
Claim allowlist for retained metadata is only sub/iss/iat/exp, instance from
binding. Optional explicit Connect association validates current generation,
epoch and approval state with existing checks, retaining revision separately
from the actual service grant. Analytics-only association never intersects or
expands API scope implicitly.

JWT has no audience, jti or request binding; identical valid assertions may be
reused by legitimate concurrent requests within short lifetime. No one-use
replay cache. Credential/signer/instance binding and protected transport supply
the declared trust ceiling. Reset all identity/assertion/request fields before
each keepalive request, including denied requests. Test direct-key/legacy then
WebUI and WebUI then direct-key transitions on same connection. Installation
inventories all WebUI recipients and custom-header overrides before enabling
native global forwarding; foreground/title/tag/background must be observed.

## Token observation and normalization (T005/T022)

`TokenDirection(count:int|None, source:measured|estimated|unknown,
applicability:applicable|not_applicable, partial:bool)` is independent for input
and output. Unknown means count null; measured zero is count0. Not-applicable
means count null/source unknown/partial false, with explicit applicability.
Counts reject bool, negatives, floats and values >10^15 (existing request-control
ceiling); retain fixed parsing limitation, not malformed raw values. Optional
`uncached_input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `reasoning_output_tokens`
are validated counts or absent, never defaulted to zero. Checked integer sums
must not overflow SQLite signed64; exact query arithmetic uses Python integers
and API decimal integers (Grafana float precision is explicitly limited).

OpenAI Chat and Responses total input/output are inclusive: cache reads and
reasoning are subsets, never added again. Native Anthropic uncached input plus
cache read plus cache creation yields normalized total; preserve components.
Only use zero for omitted cache components when the actual upstream schema
explicitly defines absence as zero; otherwise complete total is unknown and
known components remain available/partial. SSE updates replace cumulative
components, never sum each event; final output count supersedes partial count.
Capture cache creation 5-minute/1-hour total according to native schema without
adding nested subtotals to an already reported creation total. Requested wire
dialect does not decide backend usage semantics; relay backend contract does.

T005 retains native Anthropic `uncached_input_tokens` in the closed normalized
observation even when total input is unknown. `TokenUsage.to_dict/from_dict`
serialize only input/output directions, these optional component counts and fixed
`limitations` codes. No content or malformed raw values survive. Shared
`normalize_usage` accepts optional per-direction partial flags and already-observed
scalar estimates; every fallback estimate is partial and zero visible output is
unknown. `StructuredResult.normalized_usage` and the relay's per-thread
`get_last_normalized_usage()` carry the same immutable observation separately from
legacy wire usage. Assemblers expose `get_normalized_usage()` without assembling
tool/reasoning content. T008 forwards this metadata through existing wrappers and
copies it on the owning worker before handler-thread delivery finalization.

Embedding/rerank reported prompt/total input is applicable; output is
not-applicable. Missing input is unknown, never zero. Audio/non-token memory
operations have both directions not-applicable unless a declared contract
reports token units; remote unreported child inference remains explicitly
unobserved, not zero GPU consumption. Pre-dispatch rejection may record submitted
input estimate diagnostically, but consumed-token contribution is none.

Visible-text estimates use existing estimator, labeled estimated and partial
whenever tool-only/hidden reasoning/media/disconnect leave unobserved output.
Zero visible text never proves zero generated tokens. Preserve measured counts
already observed even if delivery disconnects; generation outcome and delivery
outcome are separate. No token estimate is a complete GPU consumption measure.

`normalize_usage(raw_usage, backend_dialect, applicability) -> TokenUsage` is
one shared helper owned by `decision_log.py`/existing completion types, called
by relay/SSE and purpose; it receives usage metadata only. Preserve wire
`StructuredResult` fields for dialect rendering, add normalized metadata rather
than changing native wire semantics.

Each actual upstream attempt gets server-generated `attempt_id`, optional
`parent_request_id`, `usage_relation=exclusive|inclusive_parent|unobserved`.
Known inclusive parent totals are counted once and child metadata is
non-contributing. Exclusive children contribute once; outer request count is
one, child attempts counted separately. Never mix inclusive parent+children.
When completeness/relation cannot be established, expose limitation and no
claim to exact complete consumption. No duplicate based on user session IDs.

## Protected durable lifecycle and recovery (T002/T006/T008)

Minimal `UsageStore` lives in `router/usage_store.py`, receives existing protected
`KeyStore` connection boundary, and owns only accounting tables:
`migrate()`, `register_run(owner)->run_id`, `start(RequestStart)->started|same`,
`note_dispatch(request_id,attempt_route)`, `note_observation(request_id,checkpoint)`,
`finalize(Terminal)->committed|same`,
`query(UsageQuery)->UsageResult`, `prune(now)->PruneResult`,
`recover(proven_dead_run_ids)->RecoveryResult`, `health()->UsageHealth`.
No store calls inference. T002 owns schema/backup/compatibility; T006 owns
lifecycle/recovery; T007 owns query/retention; serialize usage_store edits.

Reuse owner-only database and parent, no-link/reparse/hardlink protections and
Windows DACL handling. Start with DELETE journal, explicit `synchronous=FULL`,
existing1-second busy timeout, per-thread short connections. No WAL switch
without measured contention and reviewed sidecar/backup durability. Atomic
migration uses individual DDL in `BEGIN IMMEDIATE`, not executescript inside
transaction. Support legacy v1/v2 inputs; advance format version only after
complete migration. Old binaries must refuse new incompatible schema; never
pretend binary downgrade makes newer state compatible.

Accepted start time is UTC RFC3339; elapsed durations use monotonic clock.
Start is committed after successful caller/policy validation and before any
inference dispatch (including internal attempts). Failed start returns fixed503
`accounting_unavailable`, zero upstream calls. Matching duplicate start compares
all immutable fields; conflicting identity/grant/run is409 conflict. Route is
selected from declared configuration; record optional selected route before or
with actual dispatch marker. A crash around dispatch cannot prove whether
upstream execution occurred: interrupted records expose dispatch uncertainty,
not an invented zero or inferred successful attempt.


The start row retains a nullable, bounded 4096-byte text observation checkpoint.
`note_observation` commits the latest ordered observation for an unresolved
request; it changes no aggregate. The latest committed bounded observation
survives proven-dead recovery, with recovered observed components marked partial.
An uncommitted observation remains unknown; NULL means unobserved, never zero.

Finalize changes unresolved row and daily/cumulative contribution in one
`BEGIN IMMEDIATE` transaction. Same canonical terminal payload is a no-op;
different terminal payload is409 `accounting_conflict`, no rewrite/increment.
Missing/pruned start is `accounting_start_missing`; never recreate it.
Generation counts remain independent of downstream delivery status. Bounded
one terminal retry (same immutable payload, <=1-second additional store wait)
may occur after inference; never retry inference. Terminal-write failure
preserves committed unresolved start, exposes accounting gap, and does not
truncate an already running stream. Worker and handler handshake must retain
terminal outcome until delivery outcome is known; existing finish callbacks
release ownership idempotently even when optional diagnostic sink fails.

Run ownership uses one conservative Linux comparability contract, not PID alone.
Private run metadata stores protected stable `host_domain_id` configured by the
resource owner, actual kernel `boot_id`, `/proc/self/ns/pid` device+inode,
`/proc/1/ns/pid` device+inode for the procfs view, effective UID and user-namespace
device+inode, native PID and `/proc/self/stat` start ticks. No hardware/account
identity or this private tuple enters public output/metrics. Registration checks
procfs self PID equals os.getpid() and its PID namespace agrees with the recorded
procfs view; otherwise enabled accounting refuses unsupported owner observation.

Recovery must prove same trusted host_domain_id, boot, PID and procfs namespace,
UID/user namespace and inspectable procfs access domain BEFORE looking up the
recorded PID. Read stable process identity before/after inspection. Permission
errors, hidepid/restricted view, inaccessible namespace, another host/namespace,
unverifiable procfs mount or changed owner config return UNKNOWN, preserve starts.
An absent PID is DEAD only after comparable unrestricted view is independently
established; ENOENT alone is UNKNOWN. A comparable visible PID with different
start ticks is DEAD for the old instance (PID reuse); matching identity is LIVE.
A different verified kernel boot on the same trusted physical host is DEAD only
with owner-attested boot transition and no container-synthetic boot ID/host-domain
reuse; otherwise UNKNOWN. Other platforms initially return UNKNOWN unless their
native owner supplies independently reviewed comparable process-instance proof.
No speculative cross-platform lock implementation. The short operation lock in
service_runtime/operations.py:34-57 is reused only for management serialization;
it is not a lifetime death proof. Linux namespace visibility/start-time semantics
are documented by [pid_namespaces](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html),
[proc_pid_ns](https://man7.org/linux/man-pages/man5/proc_pid_ns.5.html) and
[proc_pid_stat](https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html).

Only proven-DEAD runs reconcile to interrupted/unknown transactionally once,
preserving observed partial usage and dispatch uncertainty. UNKNOWN contributes
incomplete health, never age-based death or upstream replay. Mandatory fixtures:
live owner in another PID namespace, inaccessible owner/procfs, same PID reused,
comparable live second run, verified reboot and unverified boot-domain change.

Replay window is retained request detail (30 days by accepted time). Unresolved
starts are never automatically pruned; reconciliation may finalize old starts,
then prune on a later pass. Finalized start+terminal or compact suppression
marker stays through allowed replay cutoff. Replays after cutoff are refused,
including request ID whose row was pruned. A terminal callback can finalize an
old unresolved row because it exists; it cannot manufacture a missing row.
Detail/daily pruning never removes cumulative contribution. No destructive key/
account foreign-key cascade. SQLite backup API produces a consistent protected
snapshot; restore is to reviewed compatible state after whole-router drain.
Rollback preserves post-backup committed ledger and newest state; no older
snapshot overwrite to erase new accounting. Demonstrate on synthetic state.

## Retained exact queries and active diagnostics (T007/T009/T010)

`UsageQuery(granularity=detail|daily|cumulative,from_utc,to_utc,filters,group_by,
limit,cursor,require_complete=False)` rejects duplicate/unknown parameters.
`require_complete` is a closed boolean: default results are exact observed retained
subtotals with explicit coverage gaps; true refuses any gap with
`usage_coverage_unavailable`. This flag never restores expired/unsupported history. All intervals are start-time
`[from,to)` UTC. Allowed grouping/filtering is actor kind/id/binding revision,
optional end-user instance/issuer/subject, effective grant kind/reference/revision/
policy digest (including Connect generation/epoch), credential ID, logical model,
outcome, token applicability and input/output provenance/partial state. Persist
all these dimensions in observed combinations in both aggregate classes; no
Cartesian product, current-account relabeling or dimension erasure after day30.

| Class | Default retained coverage | Exact supported operations |
|---|---|---|
| Detail |30 days; all unresolved rows retained separately | Retained bounded sub-day ranges, request/start/end/latency/route fields and listed dimensions. Query must not imply older unresolved exceptions restore expired completed detail. |
| Daily |365 days, whole UTC accepted days | Whole-day ranges and every listed dimension; request counts and measured/estimated subtotals with unknown/partial/not-applicable counts. No per-request latency or ID. |
| Cumulative |All retained history from visible coverage epoch until explicit authorized purge | All-history dimensional subtotals; no arbitrary historic window, per-request fields or invented daily buckets. |

Daily and cumulative assign long-running completions to accepted/start day.
Coverage records small durable segments rather than a single activation epoch.
`coverage_segment` fields are opaque accounting-domain ID, run ID, configuration
revision, enabled flag, start/end UTC and closure reason; private run ownership
references remain internal. A protected `coverage_transition(enabled,revision)`
commits segment closure/opening BEFORE admitting traffic under the new mode.
Disable may proceed only after this commit; unavailable metadata store refuses
mode change or refuses new traffic until safely resolved. Re-enable creates a new
segment without resetting existing totals. Configuration/binary rollback obeys
the same protocol; an old binary incapable of recording segments remains in a
whole-router HOLD, not silently re-admitted. Crash/unknown owner makes interval
end uncertain and the interval UNKNOWN until verified reconstruction; no guess
from last heartbeat.

One domain is the whole declared router admission authority. Each admitted start
snapshots its mode/config revision and segment ID. Simultaneous runs are only
supported under the reviewed shared admission/config revision, with per-run
segments and owner roster; otherwise new enabled admission fails unsupported
configuration. Coverage is complete only where EVERY potentially admitting run
in that domain is accounted-enabled with known ownership/segment, or provably
quiesced. Union of one enabled run with another disabled run is not complete.
Unknown roster/process owner, admission-policy mismatch or disabled interval is
a gap; no historical union masks it. Traffic during a disabled segment is
unaccounted, not measured zero. Existing starts admitted while enabled finish and
contribute normally after disable, assigned to their original accepted time.

All result classes include `coverage_segments`, `coverage_gaps`,
`coverage_complete`, requested/retained scope, and immutable cumulative
`coverage_epoch` for explicit purge only. Detail/daily exact observed subtotals
may be returned across a gap, but complete-range query requests receive typed
`usage_coverage_unavailable` with segments/gaps, never complete totals/zero.
A UTC day straddling enable/disable/re-enable is marked partial despite whole-day
aggregation. Cumulative preserves all committed contributions before/after gaps,
reports all-history accounting-incomplete and segment bounds; no arbitrary old
window or complete-total promise. Pruning30/365-day data retains compact gap/
segment metadata for cumulative coverage; it never resets cumulative arithmetic.
Empty group is known zero only inside fully proven enabled coverage. Expired
boundary detail, unsupported dimension/field or arbitrary cumulative window
returns `usage_coverage_unavailable`/`usage_granularity_unsupported` (422), with
available granularity. Test enable -> disable with unaccounted requests ->
re-enable, partial UTC day, rollback, overlapping disabled/live run and unknown
owner: all retain prior totals and none advertise false zero/complete coverage.

Default request page50/max200 (reuse diagnostic bounds), grouped page100/max500;
maximum query8192 ASCII bytes before decoding (existing operator-route cap),16 filter fields, eight grouping fields per request,
maximum366 UTC days per detail/daily query. Range cap bounds scanning, not storage
retention. Cumulative has no range and selects retained dimensional groups.
Persist actual monotone detail/daily retention floors on the domain row in the
prune transaction; they survive restart and clock rewind, cannot regress, and
must not be inferred from the minimum remaining row timestamp.
Stable opaque cursor binds snapshot/revision, canonical filters and last ordered
key; cursor cannot change query authority. Truncation provides returned/omitted
or unknown omitted and next cursor; truncated totals never masquerade as full
selected totals. Default query deadline2 seconds, hard max5 seconds. Exceeding
scan/time/row cap yields typed limitation and complete coverage metadata;
indexes follow observed exact dimensions rather than unbounded cubes.

`UsageResult` allowlist: `schema`, `collected_at`, `snapshot_revision`,
`available`, `granularity`, `requested_range`, `covered_range`, `retained_scope`, `coverage_epoch`, `coverage_segments`, `coverage_gaps`,
`coverage_complete`,
`groups/records`, `measured_input/output`, `estimated_input/output`,
`unknown_input/output_requests`, `partial_input/output_requests`,
`not_applicable_input/output_requests`, `requests`, `attempts`,
`unresolved_requests`, `accounting_failures`, `truncation`, `limitations`.
Optional cache/reasoning breakdowns retain their own known/unknown coverage.
Exact arithmetic means observed committed metadata, not unknown upstream work.
`retained_scope` reports actual detail/daily floors and available storage classes;
returned-page subtotals are never full selected totals when truncated. Unresolved
counts describe the requested time scope; unresolved detail has explicit status.

The protected `GET /v1/admin/usage?view=active` projection uses the existing
workload registry with caller/grant, selected route,
phase checking/queued/admitted/dispatched/streaming/finalizing, elapsed time,
collection timestamp, source freshness and accounting status. Terminal cleanup
is idempotent after actual owned work/delivery finishes; unresolved durable
starts belong in history/health, not fake live gauges. MAX_ACTIVE_WORKLOADS1024
stays bounded; omissions explicitly reported. Generic `/v1/workloads` records
remain unchanged and identity-free. The generic `/v1/requests` projection never
serializes the new caller/grant fields. `usage_snapshot()` supplies revision,
immutable admitted entries and bounded omissions; it refuses while any ledger
mutation is in flight. A collector reads that revision again after ledger
collection and refuses/retries changed snapshots. No registry lock spans SQLite.
The front door copies normalized observations on the actual generating worker
after upstream close, before worker completion and delivery finalization.
Relay, purpose, audio and memory record attempts immediately before their actual
transport invocation. Uninstrumented injected adapters retain explicit uncertain
dispatch instead of treating a lazy iterator or first delta as execution proof.
Runtime run/whole-authority injection is explicit: absent or unsupported trusted
authority refuses start. These source seams do not establish T023 bootstrap
coverage or authorize production enablement. No self-service endpoint in v1.

Existing operator authentication is checked BEFORE ledger/active/metric collection;
ordinary device/inference keys and unauthenticated callers fail. Use scoped operator route plumbing with the frozen existing `workloads:read`
scope; private enrollment must independently prove that only approved
administrators and the dedicated scraper hold it. A browser/WebUI admin role is not this authority. An
unverified boundary keeps sensitive query/export disabled. Responses use
`Cache-Control:no-store`; raw datasource DB access is never exposed.

### Implemented administrator HTTP wire contract (T009)

Both exact paths `/v1/admin/usage` and `/v1/admin/usage/metrics` are built-in
GET-only `OperatorRoute` readers. Their strict native `workloads:read` decision
precedes query decoding and collection on every request, including keepalive.
Legacy/auth-off, inference/device credentials, profile roles and forged client
headers provide no administrator authority. Aliases, trailing slashes and suffixes
receive fixed404; other methods fixed405 with `Allow: GET`. All responses/errors
are JSON with `Cache-Control: no-store`, except successful metrics whose fixed
content type is `text/plain; version=0.0.4`. There is no CORS, redirect or body.
Operator query8192 ASCII-byte, response8MiB and four-reader bounds are reused.
The sensitive target also caps parsed aggregate headers at16384 characters;
stdlib retains its request-line/header/count caps and finite connection timeout.

Retained queries omit `view`; fields are exactly `granularity`, `from_utc`,
`to_utc`, `filters`, `group_by`, `limit`, `cursor`, `require_complete`.
`granularity` defaults to detail; detail/daily require both UTC RFC3339-Z bounds
and cumulative forbids them. `filters` is a percent-encoded JSON array of unique
`[dimension, typed_value]` pairs, maximum16. JSON null/bool/integer/string remain
those exact types. `group_by` is a JSON string array of unique native dimensions,
maximum8. `limit` is a canonical positive decimal integer; `require_complete`
is exactly `true` or `false` (default false). Other fields retain native string
syntax. Example decoded query: `granularity=cumulative&filters=[["actor_kind",
"service"]]&group_by=["model"]&limit=100`. Clients must percent-encode JSON.
Duplicate/unknown parameters, bad escapes/UTF-8/JSON and invalid native types
are refused without store/registry reads. Native UsageQuery owns all semantic
bounds, retained classes, dimension enums and cursor validation; HTTP supplies
no roster/run/configuration/deadline authority. Native exact result arithmetic,
coverage/provenance/retention and pagination reach the wire unchanged.

`view=active` permits only `limit` (default50/max200) and the same typed `filters`;
range/group/cursor/complete arguments are rejected. Its closed schema is
`router-active-usage/v1`: `collected_at`, `source_timestamp`, `freshness`,
`available`, `registry_revision`, `accounting_health`, `records`, `truncation`.
Each record contains only `gateway_request_id`, `request_id`, frozen `caller`,
`kind`, `model`, `accepted_at`, `created_at`, `updated_at`, `elapsed_ms`,
`last_activity_ms`, owned `phase`, typed `route`, normalized `tokens` and
`accounting_status=in_progress`. `last_activity_ms` uses the owned RequestControl
activity age plus time since its diagnostic sample, capped at the existing count
bound. It is null before observed streaming activity, for missing/invalid samples
or collection clock rewind; phase transitions and repeated delivery polls do not
reset upstream activity age. Existing validated CallerSnapshot, route and
token serializers retain their closed allowlists; diagnostic session/client
strings are excluded. The phase uses owned RequestControl diagnostics where
available, with finalizing retained until actual owned completion. The bounded
registry snapshot is fenced around native ledger health; busy/changed state
returns503. `freshness=fresh` attests this direct in-process sample, not deployment
or upstream compute freshness. `available` follows ledger health; unresolved and
unknown-run counts remain health/history, not invented live records. Truncation
reports returned, omitted (null when unrepresented), unrepresented and truncated;
filtered omissions remain unknown when the registry overflowed. Generic workload
and request projections never include the sensitive caller/grant fields.

Errors use fixed safe codes: invalid wire/native query400; unsupported granularity,
coverage unavailable, invalid/stale cursor and query limitation422;
accounting/configuration availability503; unexpected failure500. Typed422 errors
include bounded validated coverage ranges, retained floors, fixed gap reasons,
closed coverage segments and health counters when the native query provides them;
no raw query/cursor/path or exception detail is echoed. Malformed trusted error
metadata is omitted. Metrics rejects every query (including bare `?`) before its
callback. `make_server(..., usage_metrics=callback)` is the after-auth bounded
zero-argument bytes seam for T011's actual persisted collector/renderer. Missing
collector is503, never a fabricated counter. The trusted optional
`usage_domain_id` permits incomplete retained reads without a roster;
`usage_authority` remains the owner-only coverage supplier and domain mismatch
refuses. Runtime installation/closed-roster/all-path bootstrap and sensitive
credential isolation remain the separately reviewed T023/T017/T019 gates.

## Persisted telemetry and native Grafana (T011-T013)

`collect_usage_snapshot(store,active_registry,now,series_budget)->UsageSnapshot`
performs one authorized bounded read snapshot; `render_usage_prometheus(snapshot)`
uses an explicit label allowlist. Existing aggregate/buffer metrics retain
existing names/gauge semantics and viewer access. Sensitive export defaults off;
a protected source route/client must be reviewed before activation.

The frozen sensitive routes are `GET /v1/admin/usage/metrics` (Prometheus text
format) and `GET /v1/admin/usage` (authoritative JSON query/ledger drilldown).
Both use injected existing `OperatorRoute` plus `check_scope(...,WORKLOADS_READ)`
(`workloads:read`) before collection; no legacy token or ordinary inference-key
bypass. The private operator must enroll only approved administrators and a
dedicated protected scrape client for this scope. Reuse existing policy/route
validation; minimal `OperatorRoute.content_type` default application/json plus
allowlisted text/plain;version=0.0.4 for metrics avoids another server. Metrics
route rejects query parameters; ledger follows UsageQuery. Current source proves
the route plumbing, not that either proposed route exists yet. Portable datasource
UID is `anvil-router-usage-admin`, native dashboard UID `anvil-router-usage-admin`;
all sensitive panels reference this UID explicitly. Ledger panel data link uses
private configured approved router origin + `/v1/admin/usage`, preserving bounded
filters/granularity. API independently authenticates the administrator; Grafana
login/session never automatically becomes router authority.

Every represented group has exact label keys `actor_kind,actor_id,
binding_revision,end_user_instance,end_user_issuer,end_user_subject,grant_ref,
credential_id,model,outcome,input_applicability,output_applicability,input_source,
output_source,input_partial,output_partial`. Nulls encode fixed `none`; grant_ref
is canonical trusted non-secret grant version reference, not a credential hash.
Only observed combinations; an active group uses fixed outcome `active` and
unknown token provenance. No request/session/attempt/run IDs, names or arbitrary
labels. Purge epoch is a scalar timestamp, not a cardinality-expanding label.

Frozen metric families (prefix `anvil_router_usage_` on EVERY name below):

| Suffix | Type | Extra labels / expansion per group | Meaning |
|---|---|---|---|
| requests_total | counter | none /1 | committed terminal parent requests |
| attempts_total | counter | none /1 | contributing observed upstream attempts |
| tokens_total | counter | direction=input/output /<=2 | count by group's measured/estimated source; unknown/nonapplicable omitted |
| unknown_requests_total | counter | direction /2 | applicable unknown direction count |
| partial_requests_total | counter | direction /2 | incomplete observation/estimate count |
| not_applicable_requests_total | counter | direction /2 | explicit non-token direction count |
| active_requests | gauge | none /1 | actual owned work, including finalizing |
| last_activity_timestamp_seconds | gauge | none /1 | latest known accepted/terminal activity |
| latency_seconds_bucket, latency_seconds_sum, latency_seconds_count | classic histogram | le for bucket /9 | six finite boundaries0.1,1,5,30,120,900 plus +Inf, sum,count; committed request duration |

Latency histogram is persisted terminal accounting metadata with daily/cumulative
buckets, not reconstructed from expired detail; it has no arbitrary percentile
exactness promise. Breakdown cache/reasoning values remain ledger-only in v1.
Group maximum21 series (1+1+2+2+2+2+1+1+9). Emit fixed global gauges (no group
labels) named `snapshot_timestamp_seconds,snapshot_revision,available,
coverage_epoch_timestamp_seconds,coverage_complete,coverage_gap_intervals,
coverage_start_timestamp_seconds,coverage_end_timestamp_seconds,
represented_groups,omitted_groups,series_budget,groups_budget,
unresolved_requests,accounting_failures,owner_unknown_runs,
export_complete,export_bytes,float_precision_safe,freshness_limit_seconds,
retained_detail_start_timestamp_seconds`: exactly20. Reserved exporter maximum
`21*256+20=5396` <=8192 hard series ceiling; budget test counts actual expanded
lines including histogram and fixed gauges. Prometheus's own target up/scrape
metrics are separate fixed scrape overhead, not hidden dimensional expansion.

Default256 groups,8192 series hard cap,2 MiB rendered bytes; deterministic
canonical-key selection and omitted count. Byte cap failure rejects snapshot or
omits whole group with explicit state, never half a group's families. Relevant
omission unknown => all filtered totals incomplete. No ledger erasure on export
omission. Counter totals come from persisted observed contributions, survive
restart/prune, and change purge epoch visibly. Float unsafe beyond2^53 =>
float_precision_safe=0 and native panel exact label removed; decimal ledger
query is authoritative. Active/availability/coverage-gap gauges use one consistent
snapshot; stale stored HTTP200 is still stale via snapshot_timestamp_seconds.

Concrete minimum OSS isolation: separate native Prometheus instance
`router-usage-prometheus`, separate named volume `router-usage-prometheus-data`,
config containing ONLY the sensitive scrape job (15s,5s timeout, protected
`authorization.credentials_file`, TLS validation) and its own self-health if
needed; no federation/remote write into common viewer store. Reuse installed
pinned Prometheus image/native config, default14-day/1 GiB sensitive sampled
retention and provisional0.5CPU/512MiB limit, tested before install. This is
native observability storage separation, not a custom accounting service;
authoritative accounting stays existing SQLite. The inspected common viewer
Prometheus store/datasource cannot enforce label-level read isolation: admin-org
pointing at it would leak sensitive series to existing viewer queries. Therefore
separate store is required for the current OSS arrangement. Do not replace
common aggregate datasource/dashboard UID or its authorized audience.

Sensitive Prometheus publishes no host port, uses native `--web.config.file`
TLS+basic authentication for query HTTP API with dedicated protected credentials,
and is reachable only through declared observability service binding. Grafana
admin-only organization provisioning gives ONLY that org datasource UID
anvil-router-usage-admin, access=proxy, editable=false, approved private backend
URL/ref, secureJsonData credentials loaded via protected installation boundary;
ordinary org has no datasource or credential to this store. Network reachability
alone is not authorization: native Prometheus web auth applies to direct queries,
labels and APIs. The design does not authorize an administrator-free proxy, an external ingress
gate, a network mutation or activation. Private
transport/ports/org IDs/members/TLS/credential references are independently
reviewed install prerequisites. If deployment lacks a safe existing transport
binding or native feature/version, installation remains HOLD; do not invent
network changes. No sensitive Loki records/labels; shared logs stay content- and
identity-free, so no duplicate sensitive Loki store is needed.

This selection follows the existing Infra `modern/observability/compose.yaml`
(one common store) and `modern/observability/grafana/provisioning/datasources/prometheus.yml` (common proxy datasource), and native
[Prometheus scrape configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/),
[Prometheus web TLS/auth](https://prometheus.io/docs/prometheus/latest/configuration/https/),
[Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/),
[Grafana security](https://grafana.com/docs/grafana/latest/setup-grafana/configure-security/).
Source and installed-version tests still must prove exact APIs/config accepted;
source selection is not live protection. Export remains disabled until T013/T017
actual nonadmin direct/backend/Explore/query/labels/cache/service-token denial,
admin readback and existing aggregate viewer parity pass independently.

Native dashboard panels cover active callers, request counts/rates, input/output,
errors, latency, last activity, unknown/estimated/partial/not-applicable, with
supported user/service/grant/model/time filtering. Cumulative represented
subtotals are exact only at stated fresh ledger snapshot with zero relevant
omissions and no accounting gaps. Freshness state is independent of scrape HTTP
200. Default ledger snapshot max-age30 seconds; scrape and dashboard refresh15
seconds; healthy acceptance requires independent observed readback<=45 seconds.
Clock/skew uncertainty is explicit. Stale/failed/incomplete/omitted states show
stale/incomplete/subtotal, never an exact-looking zero. PromQL increase/rate and
Loki summaries are sampled estimates over scrape/log retention, not exact
start-time range accounting. Native link opens supported authorized ledger view.

Grafana OSS isolation must protect actual sensitive datasource: admin-only
organization with backend unreachable to ordinary viewers, or proven equivalent
existing boundary. Separate public aggregate Prometheus/Loki projections remain
available as already authorized. Folder/dashboard ACL alone fails. Test viewer
query API, Explore, labels, cached responses, service tokens and direct backend
URLs; do not widen unrelated account/network permissions. The separate native sensitive Prometheus store is required by the inspected
shared-viewer topology; organization isolation without store separation fails. T013 holds export until
live denied-path proof exists.

## Whole-router managed quiesce/drain (T023)

Reuse existing managed transition/CLI boundary; add an explicit whole-router
scope rather than pretending tier/member drain covers other dispatches.
`quiesce_router(reason,dry_run=True,confirm=False)->QuiesceReceipt` atomically
closes top-level and internal inference admission, records prior admission state
and opaque barrier token. Already accepted requests retain their worker ownership
and finish normally; they may launch declared children under inherited admitted
ownership. No unrelated new parent or detached unowned child may pass barrier.
Check/acquire ownership and barrier under the same lock so drain zero cannot race
a new dispatch. Preserve prior per-tier/member quiesce intentions.

`drain_router(barrier_token,timeout_s=30)->DrainReceipt` reports actual owned
chat/queued/delivery-worker/purpose/audio/memory/internal work; include media job
submission/continuation ownership where same router process can initiate work.
Long-lived remote jobs/controllers require their native owner drain/readback;
absence of HTTP sockets is not proof of no remote owned work. No new inference
through MCP/A2A/background path bypasses barrier. Read-only health/status,
authorized drain queries and existing completion/delivery remain usable.
Timeout configurable1-900 seconds; returns timed_out/nonzero owner counts, no
kill/restart/force cancellation. Default30 seconds is a bounded observation,
not a promise all900-second requests have drained. Transport close/worker
join timeout cannot release drain count while a worker runs.

`readmit_router(barrier_token,dry_run=True,confirm=False)->ReadmitReceipt`
restores only recorded prior accepting states, under serialized management
mutation. Stale/other-owner token or concurrent policy change refuses automatic
restoration. Managed restart/install consumes verified zero-active barrier with
unchanged revision and retains closure until cutover completes. Process exit
cannot turn unresolved live ownership into a false drain proof. T017/T019 prove
all declared owners/routes before restart; active model serves are never restarted
for this feature. Missing owner drain is a hold, not a forced interruption.

First installation bootstrap remains explicit T023/T017 HOLD: source tests of a
new command cannot quiesce the running old version. Before replacing old runtime,
prove a supported existing all-path admission barrier and actual owned-work
drain covering its chat/purpose/audio/memory/internal/MCP/A2A bypasses. Current
tier/member command and instantaneous zero count are insufficient. If no such
supported old-runtime boundary exists, stop installation until capability and
authority exist. No daemon-thread exit, Docker stop, raw shutdown, external
network gate or drain of unrelated controllers substitutes for that proof.

## Concrete module integration ownership

| Owner task | Minimum seam contract | Writer coordination |
|---|---|---|
| T002 | `UsageStore.migrate/backup/restore`, same KeyStore `_connect`; schema compatibility, protected refs and coverage segments | Own schema first; no second connection/security layer. |
| T003 | `authenticate(...,snapshot=True)` captures frozen caller/effective-grant candidate; default Principal preserves complete legacy grants without snapshot serialization; preserve Connect four-field binding and typed `bind_owner(key_id,kind,id,expected_revision)` CAS | Keys/connect files serialized with T002/T010. |
| T004 | `identity.verify_webui(headers,binding,now)->EndUser`; validated config binding selected by authenticated credential ID | Pure metadata verifier; no side-effect permission engine. |
| T005 | Shared frozen `TokenUsage/TokenDirection` and `normalize_usage`; relay/SSE usage metadata propagation | Keep existing `StructuredResult` wire rendering; serialize decision_log edits. |
| T006/T007 | UsageStore lifecycle then queries/prune | Same file, sequential owners; immutable normalized fields from T005. |
| T008 | `_reset_request_correlation` clears snapshot; auth creates it; trusted `_anvil_usage` control object overwrites caller input; durable start before any admitted dispatch | Front door/serve integration owns lifecycle and dispatch; relay body builder must strip private object. |
| T022 | Purpose/memory/audio routing bridge carries snapshot and attempt completion without guessing tokens; actual router-owned internal attempts inherit lineage/grant | Add only inspected in-scope seams, preserving current grants. |
| T009 | Authorized operator-route callbacks `usage_query(query)` and enriched `active_requests` projection | Preserve generic workloads; front_door conflicts with T008/T023. |
| T010/T014 | Existing CLI key-management and client install/preview/readback supported verbs | No recurring script/environment dump. |
| T011 | Sensitive snapshot collector/renderer in existing router telemetry module | Frozen query/provenance contract; no independent exporter. |
| T023 | Whole-router managed transition barrier and owner permits | Serialized with front_door/serve edits; native lifecycle reused. |
| T012/T013/T015 | AI Infra deterministic dashboards/provisioning/tests; private bindings remain private | Native root-set claim and both owner suites, not product syntax only. |

Signatures are integration commitments for later independent review, not claims
that functions already exist. Shared types may live beside existing completion
metadata in `decision_log.py` and frozen identity types in `identity.py`; avoid a
new universal abstraction module. Concrete diff may reuse an existing matching
type when reread proves it has the same invariants. A change to these contracts
requires exact revision re-review before dependent writers continue.

## Requirement, task and evidence mapping

For every row, source evidence is actual claim-bound command proof, exact diff/
commit and independent reviewer verdict. Live evidence is separately observed
post-install request/query/access/recovery proof, never inferred from source tests.
T016 integrates source gates; T018 delivers exact-head PR/CI; T021 audits all rows.

| Requirement | Implementing tasks | Required source checks | Required independent live evidence |
|---|---|---|---|
| R001 |T008,T005,T022,T023,T014 | All chat dialects, purpose input/non-token, internal child dedup and pre-dispatch zero calls | T020 every declared foreground/background/direct/service path; T019 owner drain |
| R002 |T003,T008,T009 | Authenticated actor/key, spoofed body/header/session/IP and keepalive isolation | T020 two users/service matched independently in active/history |
| R003 |T003,T007 | Connect approval/recreation, key rotation/revocation, configured scope digest and immutable history | T020 actual admitted grant matches configured policy; T021 historical preservation |
| R004 |T003,T010,T015 | Typed binding validation/revision, unbound/legacy explicit, key-label spoofing | T020 declared owned clients; missing migration remains incomplete |
| R005 |T004,T008,T014,T015 | HS256 bounded/duplicates/time/instance/signer/no-user/fallback, zero upstream negatives | T020 native foreground/title/tag/background and rejected assertion |
| R006 |T003,T004,T008 | Service authority unchanged, profile admin ignored, optional Connect recreation fence | T020 service grant/end user separate; no management authority leak |
| R007 |T005,T008,T022 | UTC/route/timing/outcome, separate provenance and unknown optional breakdowns | T020 controlled observed backend usage and route association |
| R008 |T005,T022 | Inclusive OpenAI/cache/reasoning, Anthropic read/create, tool/disconnect partial | T020 observed precision; T021 no complete-consumption claim |
| R009 |T002,T006,T008 | Committed start, atomic guarded terminal+aggregates, duplicate/conflict/missing start | T019 migration; T021 restart/retained accounting proof |
| R010 |T006,T008,T023 | Busy/unwritable start zero calls; terminal failure stream survives; live second run/dead proof | T019 safe activation; T021 controlled managed restart/incomplete recovery |
| R011 |T002,T007,T010 | v1/v2 migration/refusal/backup restore, day31 dimensions,365-day coverage, replay/prune | T017 reviewed backup/rollback; T019 migration; T021 retained coverage |
| R012 |T008,T009 | All owned phases, terminal/disconnect cleanup/truncation; generic schema unchanged | T020 independent active/readback and freshness |
| R013 |T007,T009,T010 | All retained dimensions, exact totals/provenance, pagination and typed coverage limits | T020 authoritative drilldown; T021 retained/history audit |
| R014 |T009,T011,T013 | Denial before collection, ordinary keys/nonadmin cannot enumerate | T020 all-user API + sensitive read denial matrix |
| R015 |T011 | Persisted counters, bounded groups/series/bytes, overflow and exact omitted ledger group | T020 restart/omission/freshness independent projection read |
| R016 |T012,T011 | Native panel/PromQL fixtures stale/subtotal/unknown/precision/drilldown | T020<=45-second healthy readback, stale HTTP200 does not fake freshness |
| R017 |T013 | Synthetic roles/query/Explore/labels/cache/backend matrix, aggregate viewer access preserved | T017 boundary before export; T020 actual nonadmin denied paths |
| R018 |T002-T011,T014,T022 | Serializer/log/metric allowlists, no secrets/content/profile/URLs; privacy diff gate | T017 private refs/recipient preview; T020 limited responses/source readback |
| R019 |T014,T015,T017,T019,T020,T023 | Install dry-run/idempotence/recipient inventory/provider preservation/drain | T020 every declared client qualified; T019 exact managed installation |
| R020 |T016-T021 | Independent full focused/full suites, exact-head review/CI and complete matrix | T017 preview; T019 controlled rollout; T020 client/access; T021 recovery/final audit |

| Feature | Tasks | Independent acceptance |
|---|---|---|
| F001 |T001,T003,T004,T008,T010 | Immutable verified identity/effective grant, trust negatives, actual live caller |
| F002 |T002,T005,T006,T007,T008,T022,T016 | Atomic durable lifecycle, honest token semantics, retention/recovery |
| F003 |T009,T010,T007 | Authorized active/exact-history query and limitation/cleanup |
| F004 |T011,T012,T013 | Bounded persisted snapshot, native panels, source isolation/freshness |
| F005 |T014,T015,T017,T018,T019,T020,T021,T023 | Supported adoption, reviewed/merged source, safe drain/install and every client proof |

## Native ownership and evidence delivery

Source work uses the existing Anvil project and enrolled native root-set
workflow. Every request binds reviewed repository/root identities, canonical
origin, expected root-relative files, the complete verification policy and an
immutable request digest. The returned claim worktree, baseline, branch, actor
and session are authoritative; never rename the bound branch, initialize a
replacement project, alter enrollment/policy to evade a gate or adopt another
owner's claim. Preserve unrelated reservations and dirty checkouts.

Native reservations serialize writers sharing an enrolled repository, including
product-only claims. Product primary verification is its current task packet's
command; every secondary owner also runs its complete enrolled policy in its own
claim worktree. These regression policies supplement feature acceptance. Infra
feature work additionally runs its relevant nonempty acceptance suites and the
native dashboard contract suite. T013/T015 introduced test files must execute
independently with nonzero cases; product syntax checks cannot replace owner
proofs. Actual private registry, deployment, request and evidence bindings stay
outside public product source.

Execute bounded CPU/RAM using existing Python environment, packet actor/session/
hook_environment. Capture actual command exit/stdout/stderr with supported
capture-evidence; immutable submit-evidence manifests bind each root baseline,
actual changed paths, exact commit and actual proof references. On uncertain
response inspect same original request/digest, do not fabricate replacement
success. Independent reviewer reads actual diff and reruns actual gates; only
that reviewer creates contracts-review.json with document SHA256/exact commit/
real author+reviewer/requirements/features/no-open-findings and root/drain review.
No author-written reviewer receipt. Enforced human-only gate remains intact.

Task source acceptance, Git delivery and activation are separate: exact-head
independent review and green applicable CI precede ordered merge; dirty/divergent
checkouts stay preserved. Operational preview inventories current owner state,
all clients/recipients, access boundary, capacity, protected backups, schema
rollback and drain. Accounting/forwarding/sensitive exports stay disabled until
their staged prerequisites pass. Managed restart waits for actual all-owner drain;
no model restart/promotion, provider substitution, network change or shared env
inspection. Loss-safe rollback restores reviewed compatible configuration/binary
while retaining newest committed accounting. Missing authority/access/client/
recovery proof blocks completion, not unrelated read-only preparation.

## Bounded validation and acceptance

T001 requires the exact packet command at the final source commit:
`python3 scripts/run_tests.py tests/router/test_front_door_auth.py tests/router/test_keys.py tests/router/test_connect_keys.py -q`.
Run sequentially with existing environment, at most two CPU workers/no parallel
pytest, target <=2 GiB RAM; no live/GPU calls. Test overhead later against baseline:
concurrent starts/finalization must keep one-second store wait bound and report
p50/p95 added admission cost; provisional acceptance target p95<=20ms at C4 with
synthetic concurrent writes, reviewed against actual baseline rather than claimed
measured here. Query caps/series ceilings have runnable boundary tests in their
implementing tasks. Failed resource/capacity gates hold rollout.

T001 remains pending independent exact-source review until its reviewer inspects
the contract, task graph, native bindings and actual command evidence and writes
the claim-bound contract-review artifact. Draft readiness is insufficient.
Later implementation tasks supply source behavior checks; T017-T021 separately
prove operational preview, delivery, drained installation, clients, access
isolation and recovery. No source test substitutes for those live gates.


## Implemented operator CLI binding (T010)

`anvil-serving router usage active|recent|query` reuses bounded no-redirect native
HTTP and the exact T009 wire contract. The default supported command prints exact
protected response JSON without the generic diagnostic projector. The optional
standard `--json` envelope retains existing key redaction, including non-secret
`credential_id`; it does not promise complete identity readback. Counts stay exact
integers. `recent` selects the preceding24 UTC hours of retained detail; neither
it nor an observed subtotal promises complete consumption. Scoped protected raw-token
references and conventional non-secret defaults load from `router-diagnostics.toml`;
shared dotenv fallback is excluded. Fixed API refusal codes and coverage metadata
remain available in command errors.

`router keys bind` calls existing `KeyStore.bind_owner(...,dry_run=False)`; the
optional true preview checks the same writer-locked fresh expiry/revision/ordinary
key predicates and rolls back without writes. Applied output reports the committed
actor revision. It changes neither grants nor historical callers. `router keys
backup|restore` call existing protected UsageStore operations with absent-only
destinations; container paths are checked against the exact owned durable mounts.
Docker inspection retrieves only Id/State/Config.Labels/Mounts. Restore has no
activation, admission, migration, overwrite or force authority. Short examples,
settings and native-envelope limitation are in `docs/cli/router.md`.


## Implemented epoch timestamp metadata (T011)

Explicit accounting migration adds optional `usage_epoch_metadata` in schema v3
without changing credential/Connect formats. New domain creation persists its
actual epoch creation UTC timestamp atomically with the domain/run registration.
Existing domains preserve their UUID and receive an unknown timestamp (`NULL`),
never a guessed backfill from the UUID, file, first request or migration time.
Legacy stores remain readable; migration owns installation of this extension.
The sensitive exporter emits `coverage_epoch_timestamp_seconds NaN` when historical
time is absent or the recorded UUID differs from the current epoch. Other fixed
missing numeric metadata also uses NaN rather than invented zero. Committed
observed subtotals can remain available with explicit coverage gaps. Restart and
pruning retain the recorded epoch/time. No aggregate purge command is introduced;
any future authorized purge must record its new epoch and actual time atomically.

## Implemented native barrier and roster producer (T023 source)

`RouterAdmission` owns one condition lock for all root acquisition, inherited
child references, quiesce, zero recheck and consumption. Native construction
binds one explicit configured potential admitter to the existing protected
admission owner directory with an exclusive process lock. It registers the actual
`RunOwner` and enabled/disabled coverage segment through the existing UsageStore
before creating the listener. `managed_owner_scope` produces bounded authority
from that exclusive native producer, verifies the actual physical owner and every
retained run, and refuses foreign/live/unknown predecessors. Query parameters and
fixtures cannot produce this authority. No database/schema is initialized or
migrated during startup. A two-second bounded authority lease covers the existing
one-second writer wait; collected queries still clamp to actual collection time.

All exposed chat dialect/mode paths retain a handler permit through final flush;
managed chat retains it until the real worker finishes and drain additionally
checks actual thread liveness. Common direct chat/relay, purpose, audio, memory,
and media dispatch seams share that owner. Inherited declared child work retains
its own references; detached work cannot inherit a caller JSON object. Media
retained nonterminal jobs and actual reconciliation thread completion join drain.
Ambiguous submission/recovery and current remote-memory ownership remain UNKNOWN,
never zero. Read-only protocol discovery/status stays available; subscription
completion is counted and consumption prevents new storage/delivery ownership.

Persistent closure is written and fsynced before quiesce returns; persistence
failure leaves closure in place. Readmit checks token, loaded configuration,
actual owner roster and per-tier/member intent revision. It preserves independent
tier/member intentions. Changed policy/roster/owner, consumption and write failure
refuse automatic restoration. Lifecycle commands and the native config installer
consume old-owner verified zero before any backup/replacement/restart. Native
container readback binds that gate to the selected actual container/image and
current loaded config. Successor custody is not reconstructed or automatically
readmitted: its retained closure requires reviewed transfer. First old-runtime
bootstrap, shared/multiple potential admitters and remote native drain without an
owner readback remain operational HOLD. This source is not live closure proof.
