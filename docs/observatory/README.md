# Anvil Observatory

Observatory extends the packaged Anvil dashboard with current fleet/serve
observations, scoped Prometheus charts, canonical workload visibility, reviewed
configuration candidates, owner operations and retained evidence. Grafana
remains a contextual deep link. The web application is outside the inference
request path and has no Docker socket or GPU access.

The default dashboard stays read-only. Start the opt-in composition with
`anvil-serving dashboard serve --observatory-config /absolute/private/config.json`.
Choose a free loopback port; the legacy default may already belong to a controller.
The Dockerfile's `dashboard` target packages the same command and static assets.
Python runtime dependencies remain the standard library.

## Private configuration

The JSON schema identifier is `anvil-observatory/config/v1`. Required fields are
`origin` (one exact HTTPS origin), `base_path` (leading/trailing slash), `users`,
`authentication.grafana_url`, `prometheus_url`, `inventory`, and an absolute
`state_path` for the private SQLite intent journal. Optional fields are `operate`
(false by default), `controller`, `workload`, `grafana_url`, `build`, `evidence`,
`strip_prefix`, and an explicit isolated-test `fixture` marker. Keep actual
network identity and operator paths in private configuration.

Each user has a stable `id`, Grafana `username`, descriptive `role`, explicit
`resources`, and exact `actions`. A role name never grants an action. `*` in a
resource grant must be an explicit operator choice; action wildcards are not
accepted. A separate configured owner credential and supported owner binding
are also required for every action. Grafana is consulted at login through its
bounded authenticated user API; its password is not retained by this application.

`inventory.hosts` defines logical IDs, display names, verified platforms,
`metric_host` labels and physical GPU UUID mappings. `inventory.serves` declares
one metric identity per serving instance, its host, model/engine, assigned GPU
IDs and aliases. These are declarations, not live readiness/ownership proof.
The metrics adapter retains unreachable nodes and never turns absent data into
zero. A TP=2 serve contributes one throughput series while both cards remain
visible. The [facade contract](contracts.md) defines projections and routes.

Controller bindings use stable logical resources with closed typed actions;
only the server resolves paths and endpoints. The controller must expose the
actual tool and pass expected-node verification. Configure canonical workload
reads independently with their exact existing authorization policy and a
separate `workloads:read` credential; broad legacy tokens are not a substitute.

## Browser security and publication

Keep the backend on loopback and publish only through the existing private HTTPS
proxy. Do not enable public Funnel or replace unrelated edge mappings. The
facade validates the exact Host and Origin. It does not trust forwarded proxy
user names. Session cookies are random, Secure, HttpOnly, SameSite=Strict and
scoped to `base_path`; they intentionally do not use the root-only `__Host-`
prefix. Mutations require a session-bound CSRF token and an exact JSON body.

Both root and prefix deployments are supported. The prefix may be preserved or
stripped by the trusted proxy (`strip_prefix`). Packaged assets and hash deep
links resolve relative to the published shell. No arbitrary URL, file path,
PromQL, command or controller tool can be submitted by the browser.

Only harmless display preferences are retained client-side. Drafts, previews
and web intent correlation remain private server state. Owner credentials,
prompts/responses, process environments and raw upstream exceptions are never
part of an application session or public evidence.

## Operation and recovery semantics

Validate a private draft, review the exact effect and candidate, then confirm
once. Previews expire after120seconds and bind baseline, candidate, resource,
action and policy digests. Applying re-reads the owner; the owner still enforces
cross-client conflict protection at execution. A successful command and a
verified resulting state are separate outcomes.

Each confirmation has one durable intent key. Browser refresh, a dropped HTTP
response or web restart retrieves its correlation; it never replays a mutation.
An ambiguous owner outcome remains `outcome_unknown` until an owner lookup can
resolve it. A transient verification read failure remains resumable. Closing
the operation drawer stops presentation only, not the backend operation.

Back up the SQLite journal with SQLite's backup API and private configuration
before changing an installed artifact. Journal schema1 is additive and checked
at startup; an unsupported version fails closed. Keep active/unresolved records
and evidence. Terminal correlation history is bounded to30days/10,000records;
linked evidence is preserved independently. Restoring the website never undoes
an already completed serving change. Restore inference through the recorded
owner-managed baseline and independently verify admissions/identity afterward.

For a local upgrade, preserve the prior wheel/image, configuration digest,
journal backup, exact edge mount and current serving baseline. Validate the
installed artifact outside the checkout, start read-only, verify access and
metrics, then enable the explicitly granted operation path. Record real live
acceptance separately from synthetic test evidence. Do not publish a release or
promote a model merely because the console has been deployed.

## Verification

The [bounded runtime candidate workflow](runtime-candidates.md) records a matched
functional request before and after a router revision, restores the exact prior
revision, and retains failed or interrupted outcomes. Recovery from the operation
drawer derives the run identity from the saved operation; the browser cannot
choose an arbitrary owner run.

Run the existing observability/workload security regressions and focused
Observatory suites with `python scripts/run_tests.py`. Browser journeys live
under `tests/ui/observatory/`; use their pinned development harness. Exercise
duplicate confirmation, stale candidates, lost transport, owner conflict,
failed verification, rollback, canonical workload scopes, root/prefix assets,
mobile/reflow, keyboard and available assistive technology. A scan alone is
not an accessibility compliance claim.

The design follows the [Prometheus HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/),
[OWASP session guidance](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html),
and the [WAI modal dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/).
