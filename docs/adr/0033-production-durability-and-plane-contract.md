# ADR-0033 — Production durability model, plane contract, and controller-RPC fleet direction

- **Status:** Accepted
- **Date:** 2026-08-07
- **Relates to:** ADR-0002, ADR-0012, ADR-0014, ADR-0018, ADR-0030, ADR-0031, ADR-0032;
  `.tickets/2026-08-06-controller-token-not-persisted.md`,
  `.tickets/2026-08-07-local-transport-runtime-equality-friction.md`

## Context

The control plane and data plane are already separate processes with a documented doctrine:
the router (`anvil-router`) relays model traffic and never manages containers; the controller
(`anvil-controller`) dispatches allowlisted management tools and never relays model responses
(ADR-0014). Docker Compose `restart: unless-stopped` is the only supervisor, and the codebase
is stdlib-only by rule.

What is not production-grade is **state custody**. Recorded evidence:

- The 2026-08-06 overnight host restart left a healthy controller container that no client
  could use: the bearer token existed only in the environment of a shell that no longer
  existed. The CLI side already has a file-backed `.env` fallback chain
  (`anvil_serving/serves.py`); the controller's `resolve_auth_token` does not.
- A hard kill leaves `status='running'` rows in the controller's sqlite idempotency store.
  Nothing reconciles them at boot, so a replayed idempotency key answers
  `202 operation_running` until the 24-hour retention lapses, and the underlying action is
  neither re-driven nor compensated.
- Neither server handles SIGTERM. `docker restart` — the only "reload" mechanism, because
  router configuration is startup-read — kills in-flight SSE streams mid-frame and cuts off
  controller tool subprocesses.
- Router quiesce/admission intent is process memory. A crash during an eviction re-admits
  tiers an operator deliberately quiesced, guarded only by health probing, which measures
  ability, not intent.
- Evidence evaporates: the `DecisionLog` is an in-memory ring without timestamps, the
  controller audit stream is stderr-only, and chat requests emit no log line at all.
- Same-host transport selection requires host **and** runtime equality
  (`anvil_serving/targets.py`), so a native operator shell driving its own host's Docker
  containers resolves to a controller transport — which auxiliary hosts do not declare. The
  recorded workaround is a per-invocation `--command-runtime` override.
- The fleet has outgrown the two-host reference topology. Real deployments add auxiliary GPU
  hosts and additional always-on, model-free operator seats. Each host carries its own
  partial topology file, resource roles now legitimately repeat across hosts (`host`,
  `realtime-proxy`), and every client seat shares one static controller token.

Constraints that bound the option space: Python standard library only; Docker Compose as the
supervisor; fail-closed defaults; metadata-only logging; the deliberate non-components list
(no fallback chains, no classifier, no policy engine, no plugin discovery); public/private
repository boundary per ADR-0032.

## Considered options

1. **Same-host operation.** (a) Relax the locality rule so a native shell operating its own
   host's Docker runtime is local; (b) deploy a loopback controller on every host and permit
   unauthenticated loopback binds. Option (b) adds a running process, a token, and a compose
   entry per host to work around what is a locality-classification bug, and still fails when
   the controller itself is down — the exact bootstrap case. **Chosen: (a)**, with (b)
   retained as a documented opt-in.
2. **Quiesce durability.** (a) Trust the existing fail-closed health+identity readmission
   gate; (b) persist quiesce intent. Health cannot represent intent (a quiesced-for-eviction
   tier is healthy), so (a) is sufficient for readiness but not for operator intent.
   **Chosen: (b)**, opt-in, alongside a resume-path re-quiesce in the promotion flow.
3. **Decision durability.** (a) Emit decision lines to stderr and lean on the container log
   driver; (b) timestamped append-only JSONL on a router state volume. **Chosen: (b)**
   (operator decision): records gain timestamps and a bounded file sink; aggregate views
   remain snapshot-scoped, not historical.
4. **Metrics continuity.** (a) Persist counters across restarts; (b) expose process start
   time so scrapers detect resets. Persisting counters fakes semantics the bounded ring
   genuinely does not have. **Chosen: (b)**.
5. **Multi-host maturation.** (a) Build a fleet registry/relay now; (b) record the direction
   and ship only data-model groundwork. The operator's standing instruction — fix what
   exists before going deep — and the two-GPU-host reality favor **(b)**.

## Decision

### 1. Durability model

Every class of runtime state has exactly one authoritative home. Anything not in this table
is process memory and is deliberately lost on restart.

| State class | Examples | Must survive | Authoritative home |
|---|---|---|---|
| Identity and secrets | router token, controller token | host reboot | File-backed reference in the operator home (`$ANVIL_SERVING_HOME/.env` chain, gitignored, ADR-0032). The process environment is an override, never the only copy. |
| Desired state | serve manifests, router config, topology | host reboot | Git-tracked operator config; the `anvil-router-cfg` volume holds the *installed* copy, mutated only through the promote pipeline (ADR-0012). |
| Operation ledger | idempotency records, leases | crash mid-operation | Controller sqlite store on its durable volume, plus **boot reconciliation**: orphaned `running` records with stale leases are marked `failed` with a typed `operation_interrupted` error. An interrupted operation is never silently re-executed. |
| Transaction journals | promotion journal, role locks | crash mid-promotion | `~/.anvil-serving/operations/` and `locks/`; recovery is the explicit `--resume` verb (ADR-0018), which re-asserts quiescence before continuing. |
| Operator intent | tier quiesce/drain state | router restart | Opt-in persisted intent file on the router state volume. The file restores only the quiesced side; readmission always passes the fail-closed health+identity gate. |
| Runtime derived state | availability results, admission counters, in-flight requests | nothing | Process memory. Restart is the reset; readiness gates are the guard. |
| Supervision | which containers run, restart policy | host reboot | Docker itself is the ledger. No pidfiles, no service registry, no self-healing daemon. |
| Evidence | decision records, controller audit | best effort across restart | Timestamped, bounded, metadata-only JSONL sinks on the state volumes (router decisions, controller audit). Evidence, never a routing feedback signal. |

**Graceful shutdown is part of the model.** Both servers install SIGTERM/SIGINT handlers
that stop accepting, drain in-flight work within a bounded budget, flush evidence sinks, and
exit; compose files set a `stop_grace_period` above the drain budget. This is what makes
"restart as reload" a legitimate mechanism rather than accepted data loss.

### 2. Plane contract

| Plane | Owns | Never does |
|---|---|---|
| Data plane (`anvil-router`) | alias→tier routing, admission, availability, decision evidence | execute lifecycle actions; call the controller |
| Control plane (`anvil-controller`) | tool dispatch, idempotency, audit, (future) multi-host relay | relay model responses; act as a shell relay (ADR-0014) |
| Lifecycle driver (`serves` verbs) | compose state, GPU reservations, promotion transactions | route traffic |

The complete list of allowed coupling points between the planes:

1. `POST /v1/admin/transition` on the router (bearer-authenticated) for quiesce/drain/readmit;
2. router config installed into the `anvil-router-cfg` volume via validate → atomic write →
   reload → rollback (ADR-0012);
3. `docker restart` of the router as the config-activation mechanism;
4. controller → `python -m anvil_serving.cli` subprocess dispatch for tool execution.

Any new coupling point requires a new ADR. Forbidden regardless: either plane holding the
other's secrets; config mutation outside the promote pipeline; the controller in any model
data path.

### 3. Transport and topology maturation

1. **Locality rule.** In `_select_transport`, an execution target is local when it is on the
   command identity's host and either the runtimes match or the identity runtime is `native`
   and the execution runtime role is one a native shell genuinely operates (today: `docker`).
   The asymmetry is deliberate — a docker-runtime identity does not operate native execution.
   The loopback-controller alternative is retained only as an operator opt-in
   (`--allow-unauthenticated-loopback` remains restricted to loopback binds and off by
   default).
2. **Per-host resource-role scoping.** Resource roles are unique per host, not globally.
   Owner resolution with multiple global matches filters by the command identity's host; it
   never guesses among remote hosts, and the explicit `--target` spelling always overrides.
3. **Multi-host transport is controller RPC, not SSH.** Confirmed from ADR-0014: SSH remains
   bootstrap/recovery only. Groundwork shipped now, data model only: transports may declare
   `expected_node`, the controller can assert a node identity on `/health`, and the client
   verifies it fail-closed before dispatch.
4. **Deferred, with a named trigger:** fleet registry, relay, per-client tokens, and token
   rotation move to a follow-up ADR. The trigger condition is already visible — multiple
   always-on client seats share one static controller token — and per-client identity is the
   first item once multi-host RPC work begins.

### 4. Fleet topology consistency

The canonical fleet topology lives in the private operator repository (ADR-0032). Per-host
`operator-topology.toml` files are derived views of it — the same custody model as router
config: git-tracked source, installed copy. Command identity (`command_host`,
`command_runtime`) is the only per-host difference; the host/resource/transport inventory is
shared, and every host in the fleet is declared, including model-free operator seats that
run only clients. Drift between a host's installed view and the canonical topology is
detected by validation and reported, never auto-fixed: cutover of a live host's file remains
a reviewed operation.

### 5. What does not change

The idempotency store design (WAL, leases, tombstones); the operation allowlist model; bind
safety; one alias → one tier with no fallback; stdlib-only; Compose as supervisor; the MCP
2026-only controller with the Mini TypeScript bridge (ADR-0031); the deliberate
non-components list — which now also names **fleet registry service** and **background
reconciler daemon** explicitly.

## Consequences

- Follow-up implementation, in order: file-backed token resolution shared between CLI and
  controller; boot reconciliation of orphaned operations; SIGTERM drain in both servers plus
  compose `stop_grace_period`; router state volume with opt-in quiesce-intent and decision
  JSONL sinks; controller audit file sink; the locality rule and per-host role scoping;
  `expected_node` groundwork; canonical-topology drift validation.
- ADR-0012 is extended: restart-as-reload is now paired with graceful drain. ADR-0018 is
  extended: quiesce intent may be persisted, and promotion resume re-asserts quiescence.
  ADR-0014 is extended: controller RPC is the confirmed multi-host transport. ADR-0030/0031
  gain the token persistence contract. No prior ADR is superseded.
- `docs/ARCHITECTURE.md` gains a Durability model section; `docs/CONFIGURATION.md` gains the
  token persistence contract.
- New durable artifacts (decision JSONL, audit JSONL, intent file) are metadata-only by
  construction and live on private volumes; they are operator state under ADR-0032 and never
  tracked publicly.
- Deferred items carry a recorded trigger, so the follow-up ADR starts from a decision, not
  a rediscovery.
