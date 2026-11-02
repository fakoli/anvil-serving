# Project: Scoped live route reconfiguration

## Outcome and status

An operator can change an explicit chat alias's tier, endpoint, served model,
context envelope, and admission limit without restarting the shared router.
Requests already assigned a route complete or explicitly fail under that
captured configuration; they never re-resolve against a newer route.
Unrelated tiers, streams, and protected resources remain available. Releasing
an old model remains a separate, owner-controlled lifecycle operation after
its affected routes have stopped admitting and its work has drained.

**Status:** Proposed implementation contract, 2026-11-01. No implementation,
deployment, gateway installation, or model qualification is claimed.
**Source baseline:** `269bc88e3cb84bd82d14d170058d1c1e493c71cc`.
This document does not authorize an operator cutover or model promotion.

## Problem in the current implementation

`router reload` currently restarts the container. `install_config` closes the
whole router, drains every owned work family, consumes its ownership barrier,
and leaves the successor closed for a separate ownership review. Native
maintenance exceptions also require exact, digest-bound acknowledgements of
unresolved work; they are not ordinary drained completion. A route-only edit
should not need that owner transfer or a waiver for unrelated remote work.
Those gates remain necessary for actual process and ownership changes.

The existing implementation already supplies most of the relevant seams:

| Source | Current behavior and implementation implication |
| --- | --- |
| [Router management](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/anvil_serving/router_manage.py), `cmd_reload`, `install_config` | Startup-read configuration and all-path owner replacement. Keep these semantics; add a distinct route-only operation. |
| [Route configuration](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/anvil_serving/router/config.py), `Tier`, `RouterConfig`, `load_bytes` | Explicit alias-to-tier mapping and existing validation. Frozen dataclasses contain nested mappings: copy/freeze those too before publishing a request snapshot. |
| [Routing runtime](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/anvil_serving/router/serve.py), `RoutingBackend._generate`, `build_server` | Config, backend map, readiness, concurrency wrappers, metadata, and request config identity are assembled at startup. Replacing only `_config` would mix old and new policy. |
| [Tier admission](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/anvil_serving/router/admission.py), `TierAdmission`, `AdmissionLease` | Scoped quiesce/drain and atomic lease release already exist. Preserve unchanged tier state and counters. |
| [Stream lifetime](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/anvil_serving/router/serve.py), `_AdmissionIterator`, `RouterWorkloadStream` | Tier leases finish at upstream cleanup; delivery has a longer lifetime. Zero tier leases alone does not prove usage terminal completion. |
| [Usage invocation](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/anvil_serving/router/internal.py), `UsageInvocation`; [usage store](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/anvil_serving/router/usage_store.py), `RouteAssociation`, `finalize` | Attempt and terminal route associations must agree. Existing associations have no route revision or served-model identity; extend the versioned persistence contract without rewriting historical facts. |
| [Front door](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/anvil_serving/router/front_door.py), `_handle_transition`, `_admit_usage` | Separate bounded management slots and serialized mutations already exist. Keep auth, request permits, and cancellation ownership. |
| [Readiness](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/anvil_serving/router/availability.py), `_epoch_current_locked`, `invalidate` | Current source already fences probes across invalidation. Reuse that fence; do not infer missing implementation from the older readiness PRD's status. |
| [Model metadata](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/anvil_serving/router/model_metadata.py), `build_model_fingerprints`, `build_model_capabilities`; [capacity](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/anvil_serving/router/model_capacity.py), `build_model_capacity` | Configured fingerprint, capability, and capacity projections must change coherently with the model. Preserve their allowlisted output and configured-versus-observed distinction. |
| [Durable admission](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/anvil_serving/router/serve.py), `_load_admission_intent`, `_durable_admission` | `promotion` intent is not restored; later persistence errors are warnings. A crash-safe route transaction needs a strict, durable hold of its own. |
| [Owner admission](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/anvil_serving/router/admission.py), `RouterAdmission`; [container custody](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/anvil_serving/router/container_owner.py); [maintenance](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/anvil_serving/router/maintenance.py) | Owner generation, roster, barrier, usage writer, and boot configuration digest have separate safety roles. Do not repurpose them as route versions. |

## First supported slice

Implement one serialized, bounded transaction over existing direct chat tiers
with `metadata_source="configured"`. The installed tier IDs and alias names
remain fixed. Existing aliases may move between those tiers. Supported changed
tier fields are `base_url`, `model`, `context_limit`, `max_output_tokens`,
`context_admission`, `max_concurrency` (positive integer or unset), `health_path`,
`model_identity`, and `tool_support`. Also allow coherent updates to `engine`,
`quantization`, and the existing `params.fingerprint`, `params.capacity`, and
`params.capabilities` sections. Fingerprints include `model_revision`,
`engine_version`, `image_digest`, and `config_fingerprint`; capacity and
capability fields use the existing supported projection/admission vocabulary,
with explicit type, size, range, and cross-field validation at update ingress.
This is an allowlist of those fields, not permission to replace arbitrary
`params` or introduce new routing policy.

A model/endpoint change supplies a complete replacement declaration for those
model-specific metadata sections: replace, explicitly re-attest unchanged
values against the candidate evidence, or clear unsupported/unknown values.
Never silently inherit the previous model's fingerprint, measured claims,
capabilities, or capacity. Configured engine/version/quantization remain
descriptive or telemetry-adapter inputs, never route selection, fallback, or
promotion inputs; configured capacity is not a live measurement. Advertised
tool/media capabilities and enforced admission limits must agree, and declared
resource metadata must match topology without changing resource ownership.

Require exact identity readiness before admitting a changed target. Preserve
the configured dialect, request-body overrides, credentials, and all other
fields in this slice; reject changes to them instead of silently ignoring
them. A same-dialect model/runtime/quantization change is supported when its
complete declared metadata and limits pass these gates.

Reject edits involving changed replica tiers, dynamic upstream metadata,
`max_concurrency="auto"`, tier/alias creation or deletion, purpose models,
audio, memory, or media. Those surfaces continue serving unchanged, including
existing replica and dynamic-metadata tiers outside the affected scope.
The first slice is complete only with persistence, rollback, terminal
accounting, CLI/management integration, and the acceptance cases below.

The affected scope is computed, not trusted from the caller: changed tiers,
both old and new targets of moved aliases, and every alias reaching those
tiers. Show this closure in preview. Reject an asserted scope that omits any
member, or overlaps operator-declared protected aliases, tiers, or resources.
If several route families share an endpoint/resource, route drain is not proof
that the underlying serve is free; the lifecycle owner checks all references.

This deliberately permits a bounded interruption of the affected tier while
its existing requests finish. It promises no restart and no interruption
caused by this operation to unrelated tiers. Simultaneously admitting old and
new generations of the same tier is deferred; it is not needed to solve the
shared-router restart problem.

## Required observable behavior

- **R001 — Explicit choice:** Each normalized caller alias selects one exact
  tier and backend contract. Unknown aliases remain 404. An unavailable,
  quiesced, or invalid target returns the existing explicit failure; no retry
  on another tier, hidden substitution, fallback, or automatic promotion.
- **R002 — One request snapshot:** Bind a request once, after trusted request
  parsing and authorization and before readiness, context checks, or admission
  waits. Capture route revision, effective route digest, alias, tier, concrete
  model, endpoint/backend handle, dialect, context/output policy, and admission
  handle. All later dispatch, streaming, usage, and trace operations use it.
  A concurrent publication cannot combine fields from two generations.
- **R003 — Scoped closure:** Under the same synchronization that binds route
  snapshots, close only the computed scope. A request either binds and becomes
  counted before closure, or is refused afterward. Pre-bound requests may
  finish or encounter the normal quiesce refusal; none escapes the drain count
  or silently re-resolves against the new route.
- **R004 — Complete bounded drain:** Reuse per-tier quiesce/drain and additionally
  account for bound requests still checking, waiting, delivering, or committing
  usage. Report upstream-active and request-terminal counts separately. A
  successful route transaction drain requires both zero and no unresolved
  scoped cleanup/accounting failure. Timeout leaves only the affected scope
  held; it does not cancel streams, unload a model, clear ownership, or force
  an update. Unrelated remote-memory uncertainty is not a scope-wide veto.
- **R005 — Atomic apply:** Validate and build the entire candidate before
  publication. Publish one immutable configuration/runtime snapshot and one
  revision, or none. Unchanged tier backend objects, admission counts,
  background workers, and readiness observations retain their identity.
  Never rebuild the HTTP server, router owner, credentials, or usage writer.
- **R006 — Stale preview refusal:** Apply exactly the reviewed candidate bytes
  and scope using an unexpired preview bound to the current route revision,
  owner epoch, base-config digest, topology validation digest, protected scope,
  and relevant admission-policy revision. Any drift refuses without mutation.
  A rollback receives a fresh increasing revision, preventing an A-B-A change
  from making an older preview valid again.
- **R007 — Truthful durable state:** Report persisted and active route revisions
  separately if publication is uncertain. Never report success unless durable
  bytes, runtime snapshot, and read-back identity agree. A crash must not revert
  to an old route silently or reopen a transaction-held tier on restart.
- **R008 — Separate custody:** Route revision changes do not mutate the owner
  generation, owner/roster revision, usage domain, usage run, writer lock, or
  boot configuration revision. Route changes are denied while router-wide
  custody closure, consumption, or recovery is pending. Existing whole-router
  barrier previews become stale if the effective route/admission state changes.
- **R009 — Cancellation and accounting:** Preserve cancellation/deadline checks
  in queue waits and relay, close upstream resources, and release each permit
  once, including before the first streamed byte. Retain one usage start and
  one idempotent terminal per admitted request, truthful dispatch status, and
  measured/estimated/unknown token provenance. Never fabricate successful
  termination or zero tokens to make a drain pass.
- **R010 — Readiness and identity:** Freshly probe the candidate's exact model
  at its configured endpoint before readmission. Old cache results and probes
  cannot authorize it, including same-URL model replacement. New discovery,
  capacity, transition status, and fingerprints use one current snapshot per
  read and show its route revision. Fingerprints, engine/version/quantization,
  capacity, and declared capabilities belong to that same snapshot; invalidate
  old observed telemetry when its endpoint/model contract changes. Historical
  requests retain their captured identity after later updates. A configured
  declaration is never relabeled as an observed or measured fact.
- **R011 — Existing security/protocol contract:** Reuse the existing admin
  authorization boundary and management limits. Device/inference credentials
  cannot mutate routes. Preserve scoped caller grants, signed attribution,
  SSE/tool translation, stream errors, deadlines, and supported Responses
  behavior. Store secret references only; avoid raw URLs/credentials in public
  errors or metric labels. Readiness remains separate from qualification.
  Reject CR/LF and other control characters in protocol identifiers, headers,
  and metadata where their declared grammar forbids them. Treat configuration
  paths and opaque IDs as literal data: filesystem APIs or safe argv elements,
  never shell interpolation. Preserve benign spaces and punctuation in valid
  paths; do not replace path/ownership validation with a shell-character ban.
- **R012 — Managed lifecycle and promotion:** Route update never loads, stops,
  unloads, promotes, or changes reservations of a model. Recipes/serves and
  resource owners retain those actions. Configuration changes require the
  applicable human exposure/promotion approval and evidence; successful health
  checks or this feature's acceptance tests do not supply that approval.

## Transaction and persistence design

Use a small stdlib implementation in the existing router seams. A frozen route
snapshot owns a revision and immutable config/backend associations. Requests
retain a strong reference until delivery and accounting finish. Add scoped
completion bookkeeping to the existing request lifetime; do not use sampled
workload visibility as the authority for zero. Keep a narrow lock for snapshot
binding/publication, and reuse the management mutation semaphore for changes.
Network probes, backend waits, drains, filesystem writes, and stream delivery
must never hold the snapshot lock. Lock ordering must be explicit and tested.

1. **Preview:** Read the candidate once through
   [topology validation](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/anvil_serving/router/topology_validation.py),
   reuse the normal parser, enforce the closed live-update allowlist, and
   compute scope and a content-bound expiring preview. Preview is read-only;
   it does not quiesce, probe a model with inference, or write live state.
2. **Prepare/hold:** Revalidate the preview under the management guard and
   persist its transaction hold before closing scoped request binding and
   invoking existing tier quiesce. Use a new bounded reason such as
   `route_update`, never the restart-exempt `promotion` reason. A durable hold
   write failure aborts before changing admission. Errors before the durable
   readmission decision in step 5 retain the hold; unrelated traffic continues.
3. **Drain/build:** Wait for scoped upstream and request-terminal completion,
   then build only changed tier runtimes and validate exact candidate
   readiness. Recheck cancellation and preview bindings before committing.
   Account explicitly for this transaction's own recorded hold changes when
   checking admission drift; unrelated changes still invalidate the preview.
   A changed limit cannot recreate a semaphore while its old users survive.
   Ordinary queued requests retain their original backend; update does not
   migrate them, drop them to free space, or let them exceed their deadline.
4. **Persist/publish:** Write the complete committed route state atomically,
   still held; publish the already-built snapshot in one short locked step.
   Retain unchanged runtime objects. No inference work occurs in this step.
5. **Readmit/read-back:** Apply the existing fresh identity gate to changed
   targets. Restore admitting state only where the preview explicitly asked
   for it and the scope was previously admitting; preserve pre-existing
   operator holds. Durable hold removal is the **readmission commit point**;
   then open the corresponding in-memory admission. Failure before that commit
   retains the hold. Failure after it returns `committed/reconciliation-required`
   with durable intent and observed runtime admission reported separately,
   never a false guarantee that the scope remains held. Restart follows the
   committed readmission decision only after fresh exact-identity readiness
   and any independent operator hold checks. Reconciliation retries opening
   that committed revision without creating a new revision or clearing other
   holds. Return success only after runtime read-back agrees with the durable
   decision; do not silently compensate by reinstating an unreviewed hold.

For initial delivery, use one bounded, protected JSON route-state file in the
operator home, configured through a proposed restart-only
`server.route_state_path`. It contains a schema version, immutable installed
base-config SHA-256, current route revision/digest and allowlisted projection,
the immediately previous projection for rollback, and any transaction hold.
Use the existing atomic-write pattern, with file and containing-directory
durability, strict parsing, permissions, ownership, and symlink/path checks.
Bound the artifact and preview payload (proposed maximum: 1 MiB); reject larger
input before constructing runtime state. Retain no unbounded revision history.

The installed configuration remains the boot/custody authority; this new file
is the sole authority for committed live route overrides. Do not write a
second competing effective `router.toml` on apply. Startup loads and validates
the base, requires its digest to match, applies only allowed overrides, and
restores transaction holds before accepting requests. Existing durable tier
intent is combined conservatively: a hold from either authority stays closed.
An incompatible base edit, corrupt/missing previously initialized state, or
unsupported state version is a visible startup hold/error, never an implicit
return to the old base routes. Initial state creation belongs to managed
installation, not a request-time missing-file fallback.

Crash boundaries are part of the contract: before durable commit, restart uses
the old revision and retains any prepared hold; after route commit but before
readmission commit, restart uses the new revision with its hold. After durable
readmission commit, restart follows step 5, including fresh readiness. If the
process remains alive after an uncertain route write/publication, affected
requests stay blocked and status shows both revisions until managed
reconciliation establishes which commit exists. An uncertain readmission
write likewise requires durable read-back before claiming held or admitting;
it does not erase a potentially committed decision.
The route-state record makes the decision authoritative; the existing
best-effort admission-intent callback alone cannot establish this guarantee.

Compatibility requires a one-time managed release/restart to install the new
runtime, protected state mount, and additive usage schema reader. Subsequent
supported route changes do not restart the router. Bootstrap owner hashes
keep their existing meaning. Add `route_revision` and `effective_route_sha256`
rather than changing `configuration_revision` in place. Extend the owner's
existing policy fingerprint to bind effective route state when producing
whole-router barriers, without changing owner identity or run accounting.
Unchanged route content may share a digest across revisions; revisions remain
monotonic and never reset during rollback.

## Operator interface and recovery

Proposed CLI shapes below are not existing runnable commands. They use the
conventional operator home and protected authentication automatically:

```text
anvil-serving router routes preview --config candidate.toml
anvil-serving router routes apply --preview-id PREVIEW_ID --confirm
anvil-serving router routes status
anvil-serving router routes rollback --revision REVISION
```

`apply` consumes a short-lived, content-bound preview; confirmation is explicit
and never inferred from elapsed time. The preview identifies scope, old/new
model and context, admission effects, protected resources, timeout, rollback
target, and whether readmission is included. The server accepts no arbitrary
filesystem paths from an HTTP caller: transport sends bounded validated bytes
or a server-owned opaque preview ID. Rollback previews by default and applies
through the same exact-preview confirmation path. An identical retry of a
committed operation returns its receipt without draining again or creating a
second revision; a lost reply is resolved by status before retry.

Expose these operations through the existing authenticated management path
and supported CLI first; typed controller/MCP adapters call the same library
operation. Use explicit busy, stale-preview, validation, timeout, and held
states. Cancellation before commit abandons the candidate while retaining a
published hold; cancellation after commit cannot undo it invisibly. Status and
a fresh resume/rollback preview provide recovery without hand-editing state.

For an old model occupying resources needed by its replacement, the sequence
remains scoped quiesce/drain, owner-verified managed unload, managed candidate
load, route preview/apply, and explicit readmission. An update may start from
an already-quiesced tier. No readiness success is required from the old model
after its verified unload. Its replacement must pass the new identity gate.
The route command does not orchestrate these model operations in this slice.

Rollback is another validated route update to the retained prior projection;
it does not resurrect an unloaded model. If the old endpoint is absent or
identity fails, rollback remains held until its owner restores the declared
serve. No automatic rollback following a health failure and no alternate
target selection are introduced. A pre-existing hold is never cleared merely
because an update or rollback succeeded.

## Identity, accounting, and observability

Add route revision/digest to admin route status, discovery metadata, decision
records, request traces, and usage route associations. Capture exact configured
and observed served-model identity and the configured fingerprint digest with
the request, retaining their revision-bound association through terminal
accounting. Operator-only metadata may include the endpoint reference, while
ordinary clients retain their existing
model/alias visibility and filtering. A sanitized response header may expose
the opaque route revision without exposing private topology. Do not use the
current status model to reconstruct an old request's identity.

Existing usage JSON readers reject unknown fields. Plan an explicit additive
schema migration/compatible decoder and upgrade/downgrade policy: old rows
read as route revision/served model unknown, not the current revision; no
historical ledger rewrite. Preserve dispatch-to-terminal association equality.
If terminal persistence fails, preserve the existing accounting failure
signal and unresolved scoped completion; never equate a finished workload
observation with a durable terminal. Emit a bounded route-operation receipt
with old/new revision, actor reference, scope, decision, and state, without
prompts, response bodies, credential values, or private topology in public
artifacts. Revision labels must not create unbounded Prometheus series.

## Acceptance cases

Use deterministic events/barriers, fake transports, temporary protected files,
and loopback HTTP servers. No model or production fleet is a unit-test fixture.
Requirements are not accepted merely by inspecting the implementation.

| Case | Observable pass condition |
| --- | --- |
| A01: protected parallel stream | Hold a stream on Tier B while changing Tier A endpoint/model/context/limit. B completes identical content/tools/usage; B's backend/admission identity and router PID/owner/run stay unchanged. New A requests use only the committed target. |
| A02: boundary race | Pause A after route binding but before readiness/admission, then quiesce/update. It finishes or receives an explicit old-generation refusal; it never dispatches to the new backend or disappears from drain accounting. |
| A03: drain timeout | Hold A's upstream open past the bounded drain. No commit or unload occurs; A remains held; B keeps admitting. Existing stream is not cancelled by the updater. |
| A04: saturated queue cancellation | Fill A's concurrency gate, queue another request, begin update, and cancel/expire the queued request before any upstream byte. It never dispatches, releases all leases once, records the correct terminal, and allows drain to progress. Repeat with disconnect during streaming and before first iterator advance. |
| A05: complete terminal lifetime | Finish upstream but pause delivery or ledger commit. Tier active count may be zero, but scoped request-terminal count remains nonzero. Inject terminal-store failure: update remains held, accounting stays truthful, and B is unaffected. |
| A06: stale/parallel previews | Competing updates, expired preview, altered candidate, admission drift, topology drift, protected-scope change, and A-B-A rollback all reject stale apply. A lost success reply is discoverable and idempotent. |
| A07: invalid or excessive scope | Invalid alias, wrong model identity, forbidden field, replica/dynamic target change, invalid context/output cap, protected resource overlap, oversized body, or hidden sibling alias changes produce no partial mutation. |
| A08: readiness race | Release an old successful probe after publication or replace a model at the same URL. Neither can make the new model ready. Only the current exact identity admits; B's cache remains intact. |
| A09: crash and filesystem faults | Inject failure before/after hold write, commit rename, directory sync, pointer publication, and specifically between durable hold removal and runtime admission opening. The last case reports committed/reconciliation-required; restart follows committed readmission only after fresh exact identity and independent hold checks. Other cases observe the defined old/new revision and hold, without silently admitting a stale route. Reject corrupt/truncated state, symlinks, permissions failure, missing initialized state, and base-config drift. |
| A10: rollback | Restore the retained projection as a new revision with the original model identity; preserve unrelated traffic and prior holds. Missing old model blocks readmission rather than selecting a substitute. |
| A11: protocol/auth matrix | Messages, Chat Completions, and supported Responses retain streaming tools, tool-result continuation, SSE errors, deadlines, scoped credentials, and usage semantics across update. Inference keys cannot mutate routes; unauthenticated calls remain denied. |
| A12: custody isolation | Route-only update preserves owner generation, roster, writer lock, run/domain, and boot revision. Whole-router closure rejects concurrent route update, stale whole-router previews cannot apply, and unrelated remote-work UNKNOWN neither disappears nor blocks a valid scoped update. |
| A13: shared serve references | A serve referenced by another tier or route family cannot be declared safe to unload from A's drain alone. Owner checks reject release until every affected reference is covered; protected resources remain untouched. |
| A14: schema compatibility | Historical usage records remain readable as unknown revision; new dispatch/terminal associations agree under cancellation, duplicate finalize, and recovery. An incompatible downgrade refuses cleanly with the preserved ledger intact. |
| A15: coherent model metadata | Swap two synthetic same-dialect models with different revisions, engine versions, quantization, context, capacity, and capabilities. New discovery/fingerprint/capacity/admission results describe only the new snapshot; missing declarations become explicit unknowns, stale observations are discarded, invalid or contradictory limits reject atomically, and an old request's captured model/revision/fingerprint association remains unchanged. Engine labels never alter the selected alias or trigger fallback. |
| A16: input boundaries and interface parity | Submit CR/LF/control characters through identifiers, metadata, and header-producing fields; malformed inputs fail before mutation. Exercise valid config paths containing spaces and shell-like punctuation as literal filenames, with no shell execution or unintended path access. CLI, admin HTTP, controller, and MCP enforce identical scope, authorization, preview expiry, confirmation, idempotency, held/committed states, and error semantics through the shared operation. |

Extend existing focused tests in
[transition integration](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/tests/router/test_transition_integration.py),
[admission](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/tests/router/test_admission.py),
[streaming relay](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/tests/router/test_streaming_relay.py),
[usage failure matrix](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/tests/router/test_usage_failure_matrix.py),
[usage migration](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/tests/router/test_usage_migration.py), and
[availability](https://github.com/fakoli/anvil-serving/blob/269bc88e3cb84bd82d14d170058d1c1e493c71cc/tests/router/test_availability.py). Add one focused route
update test module rather than duplicating suites. Include a deliberate
negative control that swaps in a new backend mid-request and proves the
generation assertion fails. Use the current repository test/check commands;
run required router, command-surface, schema, and documentation checks once
after implementation stabilizes.

Live acceptance is separate: an explicitly approved managed rehearsal proves
the affected model change, simultaneous protected-tier continuity, exact
identity/accounting, restart recovery, and rollback on installed binaries.
Record route revisions and owner identity before/after. Mock tests do not
prove hardware continuity, model qualification, or human promotion approval.

## Delivery phases and effort

Estimates are engineering days for one experienced implementer plus independent
review, not measured delivery promises. They assume current tests and managed
ownership surfaces are healthy; operational access and approval waits are extra.

| Phase | Deliverable and exit gate | Estimate |
| --- | --- | --- |
| 1 | Capture immutable request route state; isolate changed tier runtime construction; preserve protocol behavior. A01-A02, A04-A05 pass before exposing mutations. | 2-3 days |
| 2 | Strict preview/apply, scope closure, durable holds and state, restart recovery, rollback, coherent model metadata and usage compatibility. A03, A06-A10, A12-A15 pass. | 3-5 days |
| 3 | Supported CLI/admin and controller/MCP wrappers, observability, focused regression/negative controls, independent concurrency/security review. A11, A16, and complete requirement matrix pass. | 2-3 days |
| 4 | One-time managed install, approved synthetic and real-client rehearsal, restart/rollback receipts, operator documentation. Live evidence is distinct from source completion. | 1-2 days plus model load/drain and approval time |

**First production slice:** approximately 8-13 engineering days including the
managed rehearsal. Schema or custody incompatibility discovered in phase 1
requires an explicit revised estimate; do not bypass those gates to preserve a
date. A source-only prototype without crash/terminal tests is not this slice.

Later, add alias/tier creation/deletion, dynamic metadata and auto limits, or
replica member updates only for a concrete requirement (roughly 2-5 additional
days per bounded family, subject to its own concurrency review). Overlapping
old/new generation admission is a separate capacity/lifecycle feature and is
not estimated here.

## Restart-only boundary and alternatives

Listener bind/port/TLS, inbound authentication and authorization configuration,
upstream credential-reference changes in this first slice, process-wide limits,
storage paths/schema ownership, usage enablement/domain/writer/roster,
owner/controller bindings, mounts, runtime code, and installation of the new
route-state path remain restart-only. Existing dedicated key-management actions
retain their own semantics; this route API does not replace them. Unsupported
route changes use existing managed configuration/custody operations with their
exact preview, all-path drain, recovery, and human gates intact.

Bifrost's [v2.4.0 transport release](https://github.com/maximhq/bifrost/releases/tag/transports/v2.4.0)
has a scoped [`UpdateProvider` implementation](https://github.com/maximhq/bifrost/blob/e869c3f9adc96ea53c38694a21bb04a8e1085487/core/bifrost.go#L4329),
and its [configuration store supports SQLite](https://docs.getbifrost.ai/deployment-guides/config-json/storage).
Its queue handoff includes a queue-full cancellation path; that is a specific
behavior to test, not evidence of lossless request continuity. LiteLLM documents
[database-backed model changes without restart](https://docs.litellm.ai/docs/proxy/model_management),
with file-defined models retaining separate ownership. These are design priors,
checked 2026-11-01, not local acceptance evidence or drop-in replacements for
Anvil's auth, cancellation, accounting, and resource-owner contracts.

This draft specifies the existing stdlib router option. External gateway
evaluation or replacement remains a separate bounded architectural decision;
Bifrost has not been rejected by this specification. No gateway installation
or evaluation is performed by this documentation task. If an external gateway
is selected, its adapter boundary must satisfy the same preview/apply/status/
rollback contract plus captured per-request identity and drain evidence.
Do not build an adapter interface before another implementation is required.

## Decisions for implementation review

This draft chooses drain-before-apply, a separate protected route-state file,
fixed tier/alias sets, and existing configured direct tiers for the first
slice. No operator decision is needed to finish this specification. Before
implementation acceptance, independent review must settle the exact additive
usage schema compatibility policy and confirm that scoped terminal persistence
can be observed authoritatively without weakening current accounting. If either
cannot be achieved within the existing seams, revise the implementation plan;
do not substitute global restart, best-effort accounting, or hidden fallback.
