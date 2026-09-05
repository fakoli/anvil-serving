# Dashboard workload authority and bounded canonical reads

Date: 2026-09-05
Status: design closed for implementation; source and acceptance pending

## Ground truth

`observability/dashboard/app.py::create_dashboard_server` delegates to
`observability/api.py::create_server`. Its existing token is a legacy telemetry
credential, and loopback telemetry may be unauthenticated. Generic query routes
are therefore not a valid workload authority boundary. They also echo exception
text and use a telemetry-specific serializer. The workload panel must not reuse
those paths or collect fleet inventory locally.

The existing `control_plane/authorization.py` already supplies immutable scoped
policies, bounded credential loading, legacy-credential collision refusal and
`check_scope(..., WORKLOADS_READ)`. The canonical fleet HTTP reader supplies
expected-node identity, literal endpoint restrictions, canonical receipt-clock
validation and one bounded health/query budget.

## Closed design

Use two scope checks with the same presented per-client workload credential:
the dashboard checks its explicit local policy before query parsing, clock or
network access, then forwards that authorized credential through the canonical
fleet reader to the explicitly configured controller. The controller independently
requires `workloads:read`. Do not provide a server-owned outbound credential that
could lend broader access to a dashboard client. The operator must provision the
intended client in both policies; mismatches fail closed. Legacy telemetry and
workload credentials stay separate, including in the browser.

Introduce a bounded workload HTTP service beside the observability API. It owns
fixed errors, strict seven-field URL query parsing, authorization-before-read,
four non-waiting concurrent upstream slots, canonical serialization and injected
clocks/readers for hermetic tests. It passes a new one-entry environment mapping
containing only the authorized presented credential to the existing fleet reader;
the fixed internal reference is not an operator environment fallback. No dotenv,
topology search, cached fleet snapshot, local collector or mutation path exists.

The observability API reserves `/v1/workloads` before legacy telemetry auth,
static or generic query routes. It accepts only the dedicated service type,
serializes only that service's canonical bytes and emits fixed failures if the
service is absent or throws. It must never call telemetry redaction on canonical
workload data or expose a generic callback override for this reserved path.
GET is the only operation; mutation methods remain refused. Responses retain
no-store, nosniff and same-origin browser restrictions.

Dashboard startup adds three independent, optional explicit values: controller
URL, expected controller node, and workload authorization-policy path. All are
needed to enable workload reads. Load one immutable policy with the same explicit
environment used by the server and reject overlap with the legacy telemetry
credential. Bad or missing workload configuration disables only workload reads;
it never invents an empty complete fleet or breaks existing telemetry startup.
No credential or endpoint is rendered into the page.

The workload tab has a separate password input and connect/disconnect controls.
Keep its credential only in memory; never use session/local storage, a URL,
bootstrap HTML, a cookie or the telemetry token. Fetch only the fixed same-origin
workload path with seven reviewed filters. Poll at most once per five seconds,
only while visible and connected, without overlapping calls. Abort on disconnect,
filter changes or an eight-second client deadline, and discard superseded results.
Clear records after errors/disconnect so an old result cannot look current.

Render known canonical fields with text nodes, retaining source and collection
times, original per-node/source errors, status and exact/unknown omission counts.
Differentiate active, terminal, stale, partial, unavailable and truncated in text,
not color alone. Empty partial/unavailable results are not idle. Do not use hardware
samples or DOM reconstruction to produce records, recency or fleet completeness.

## Delivery slices

1. Pure bounded HTTP service and tests.
2. Reserved observability API path and tests.
3. Explicit dashboard startup wiring and tests.
4. Accessible no-build workload panel and browser behavior tests.

Each slice requires its own scoped implementation packet. The owning PRD will be
revised only after active claims finish; formal acceptance remains consolidated.
No live policy, credential, deployment or model mutation is authorized by this
design record alone.
