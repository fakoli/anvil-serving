# Establish controller identity and access before coordinated deployment

Status: open
Priority: P1 deployment gate
Date: 2026-09-05

## Observation

Read-only deployment preflight received HTTP 401 from the default loopback
controller status target and HTTP 403 from both declared controller transports.
The required credential is present in the current process environment; checking
the user-local and selected operator-home credential files did not supply a
replacement. This is not established to be a missing-credential problem.
A retry with the existing no-proxy/no-redirect transport returned the same 403,
so proxy interference is not an established root cause either.

The local operation catalog exposes controller status but no controller log
operation. Controller services are not members of the model-serve manifest,
so `serves logs` is not an appropriate controller diagnostic surface. The
connected MCP catalog is unavailable in this session; the checkout's CLI and
local operation contracts were verified before fallback. A narrow read-only
listener/container inspection is necessary to identify the owning component.
The missing bounded controller-log surface is a confirmed product gap that
must be implemented before this becomes a repeatable operational workflow.

These observations do not establish the responding service's expected node
identity, build version, authorization policy, or controller/router parity.
CLI version agreement and router reachability alone do not prove those facts.
Raw identities, endpoints, credentials, and private evidence remain outside the
public repository.

## Required fix-forward investigation

1. Obtain the authoritative managed service status and bounded startup/request
   logs through the product's existing operations surfaces.
2. Distinguish the expected controller from another listener or intermediary;
   distinguish invalid authentication from authorization or origin rejection.
3. Record the root cause and implement any product defect through supported
   CLI/controller surfaces with regression tests. Do not bypass a denial by
   silently broadening authority or weakening authentication.
4. Re-run the original status probe and verify exact node/build identity.
5. Before coordinated deployment, establish manifest-derived mount closure,
   same-selected-model recovery, endpoint version parity, and real-client
   acceptance. Do not represent a merge or a successful health response as
   deployment completion.

## Closure evidence

Bounded follow-up established that all three external status targets return
`Microsoft-HTTPAPI/2.0` text responses, with no corresponding request in the
controller's structured logs. The current process credential equals the
controller container credential (comparison only; neither value was exposed).
The default listener belongs to the Windows system process. Container
configuration declares a loopback port binding, but its live network port
inventory has no corresponding published mapping. Thus the observed 401/403
responses come from another listener, not the owning controller; the precise
forwarding/publication failure and its durable correction remain unresolved.

Pending: managed diagnostic surface, forwarding/publication root cause,
durable correction, regression results, and successful identity-checked
status/deployment verification. Do not stop or reconfigure the unrelated
Windows listener merely to reclaim a port. No live route, model serve,
controller, or private configuration has been changed for this investigation.

## Diagnostic implementation review

The approved [bounded diagnostics PRD](../docs/prds/controller-diagnostics.md)
has seven tasks covering capture, projection, CLI/MCP, both server and client
permissions, scaffold parity, documentation and actual local diagnostic proof.
Its capture foundation is accepted in
`b64687aae2c809633a1196534466c73e0abf75b4` after independent review and
10 passing post-commit tests plus Ruff. Corrections cover child/reader cleanup,
unsupported macOS classification, shared capture bounds, hostile post-spawn
failures, deadline precedence, exact result types and truncation invariants.
An independent negative control raised the byte cap by one and made the
262145-byte overflow regression fail. A passing four-test happy-path gate had
not established those lifecycle properties. Fixed inspection/log projections
are accepted in `96b961d985ed7eb3228eea109760fd188a134846` after independent
eight-angle review, privacy/boundary negative controls, 19 post-commit tests and
Ruff. Configured versus observed bindings stay separate, log reads use only the
verified immutable controller ID, and raw content is discarded. CLI/MCP wiring
and actual local-daemon validation remain pending; this does not resolve
deployment access. No raw diagnostic output or credentials are added here.
