# ADR-0043 Slice 3 implementation report

Date: 2026-09-12. Baseline: `ed7e533305ef64a64f83a4814713b891a0532d96`.

**Status: partial implementation; local activation is blocked. This is not final
ADR acceptance or an implementation-complete claim.** Go code, the runtime
schema, CLI verbs, model serving, and operator qualification harnesses are
unchanged.

## Implemented

- Extended deployment validation to keep local material out of other roles'
  state/ingress directories, EnvironmentFiles, and IdP secret references.
  Existing exact runtime projection remains in use.
- Added local material metadata checks for the consuming UID/GID, traversable
  protected ancestry, no symlinks, regular bounded nonempty files, ownership,
  permissions, and readability. Leaf keys must be gateway-owned, mode 0600,
  and single-link, matching native `readManagedMaterial`. Connector validation
  inspects only its public local root. Python never parses or reads PEM/key
  contents.
- Local connector units order the declared gateway with `After`/`Wants`, without
  `Requires`, `BindsTo`, or `PartOf` propagation. This establishes ordering only;
  the native boot readiness gate remains blocked below.
- Render/up previews identify path selections, role-specific trust references,
  configuration-dependent restarts, concrete runtime actions for unchanged
  unhealthy units, and explicit activation blockers.
  Coordinated-target enforcement runs during preview and again under the
  activation lock, and remains enforced by `_activate`.
- `_up_selected` preserves unchanged stable services; gateway admin readiness
  failure triggers a gateway restart. Changed gateway runtime configuration
  does not restart unchanged healthy Caddy/Authelia units. Known connector
  degradation triggers a restart. Existing CF-only process/admin checks are
  retained; they do not establish admitted-tunnel readiness.
- Rollback restores touched services' running intent and leaves untouched
  services running. Existing configuration ownership, binary binding, locking,
  delayed activation-record publication, and private-state exclusion remain.
- Status/logs decode only Slice 2's closed, credential-free event vocabulary.
  Events are scoped to the current systemd invocation, checked again after
  reading, and separated from unit activity and declared selection. Negative
  observations report degradation; positive or absent events do not claim
  readiness. `_closed_gateway_status` and the native admin response are unchanged.
- Added regression coverage for metadata isolation, target scope, conditional
  boot ordering, unchanged-service convergence, failed admin readiness,
  rollback, blocked local activation/CF recovery, closed event decoding,
  versioned trust references, and omission byte stability. The existing Go
  reader corpus now includes actual rendered JSON for versioned rotation,
  CF recovery retaining the listener, and CF recovery removing the listener.

## Blocking baseline gaps and PRD pushback

The requested Python-only boundary cannot complete the required native gates:

1. `connect/cmd/anvil-connect/main.go` handles `preflight` by reading the runtime
   declaration and calling `transport.VerifyBinary`. It has no argument for a
   paired gateway declaration and does not call
   `ConnectorConfig.VerifyLocalTunnelTrust`. That exported Go method exists in
   `connect/internal/runtime/local_entry.go`, but Python cannot invoke it through
   the installed CLI. Gateway leaf/key validation is private `loadTLS`, called
   at startup; it is also not exposed through native preflight. No unsupported
   flags, dynamically compiled helper, or duplicate Python PKI verifier was added.
2. The closed admin `status` reply contains authority metadata, not live entry
  health or resource registration readiness. `Gateway.EntryHealth` and runtime
  `Events` are in-process methods. Journal events are bounded/rate-limited and
   can suppress a second resource's failure with the same path/reason in one
   second. They cannot prove every resource currently has an admitted registration. PID,
   admin availability, and TCP connectivity are insufficient substitutes.
3. Existing `Type=simple` boot ordering is not a readiness barrier. The CLI
   provides no readiness wait usable by the generated unit. Stop/release/
   replacement sequencing and stale/foreign listener rejection also remain
   unimplemented: they are deferred until the activation gates can complete,
   rather than adding an unreachable transaction path behind the blocker.

Local-affecting `up` transactions, including removing an existing connector's
local selection, now fail before configuration/service mutation and activation
record commit. Preview and staged render remain available and explain why.
An unchanged healthy local deployment also cannot yet be accepted as a no-op.
This deliberately blocks acceptance rather than manufacturing evidence.

To finish Slice 3, the native executable needs a bounded integration of the
existing trust verifier under service identities, plus authoritative readiness
contracts usable by managed activation and boot.
Those changes require relaxing the explicit no-Go scope; none were made here.
After those contracts exist, implement and fault-test the blocked transaction
paths, including stale/foreign reverse listeners and failed trust recovery.

Rotation must use new versioned certificate/key/root paths in the manifest.
Tests show these references change the reviewed generation while preserving
unaffected output. External material is never overwritten or copied into the
rendered tree. This slice does not detect in-place material replacement or
prove prior trust remains valid; native validation is still required.

Rollback never copies installation state, epochs, generations, leases, or
credential databases. Invalid prior trust must fail recovery once native gates
are available; that live recovery behavior is not claimed by these tests.

## Validation

The worktree root is mode 0755. The requested wrapper command was attempted:

```sh
python scripts/run_tests.py tests/connect/ -x -q
```

It stopped before pytest because this execution sandbox exposes `/` and `/data`
as owned by UID 65534 instead of root or the effective user. Its trusted-ancestry
gate was not modified or bypassed inside the wrapper. Direct pytest was run
separately for diagnostic coverage.

Final focused direct run: **142 passed, 2 skipped**, including actual Go runtime
reader parity and both legacy and isolated omission-byte checks:

```sh
GOCACHE=/tmp/adr0043-go-cache python -m pytest \
  tests/connect/test_render.py tests/connect/test_manage.py \
  tests/connect/test_manage_isolation.py -x -q \
  --basetemp=/tmp/adr0043-slice3-deadlines
```

`git diff --check` passed. Ruff was unavailable in the supplied virtualenv and
on PATH; lint was not run. The Go source diff is empty.

Source/test diff SHA-256 relative to the baseline (the six modified Python
files, excluding this summary):
`1d912a15ada5b530101e5a7095ed425ee7703d6d489d7de7734650955feec198`.

Independent Sol-high review identified and prompted fixes for new public
connector addition with no prior JSON, concrete preview restart reporting, and
aggregate status/preview latency. Each status, logs, and preview observation
pass now shares a five-second deadline and returns unknown/unavailable or a
concrete `check` action after it. Regression tests
also cover invalid invocation IDs, journal unavailability, and invocation
rollover. Targeted re-review found no remaining issue in those fixes; the final
focused run above passed after re-review. Review remains HOLD on the known
missing native acceptance gates.

Broader direct run: **543 passed, 4 skipped, 10 failed, 1 error**, before the final
preview-budget regression test was added. Nine failures and the setup error
were socket `EPERM` in existing isolation, qualification, and migration tests.
The installed-wheel test failed fetching build requirements due sandbox
DNS/network restrictions after redirecting its read-only default cache to
`/tmp`. These are not passing full-suite results.

The requested `go test ./internal/runtime ./internal/config` was attempted from
`connect/`. With a writable `GOCACHE` under `/tmp`, `internal/config` passed;
`internal/runtime` failed on existing loopback-listener tests with socket
`operation not permitted`. A focused runtime run selecting `TestLocalTunnel`,
`TestLocalEntry`, `TestGatewayPublicFailure`, and `TestConnector` passed. The
Python cross-language corpus independently ran the actual Go reader parity test.

No live service activation, CF recovery, latency qualification, or production
deployment was performed. Operator PRD items 5–7 and all explicit exclusions
remain outside this change.

## Completion

Date: 2026-09-12. This section supersedes the partial status and native-gate
blockers above. Slice 3's requested implementation and regression coverage are
complete; full-suite and live acceptance are still pending the gates below.

- Native `preflight` now invokes `VerifyLocalTunnelTrust` for a local connector
  using `--input GATEWAY_FILE`, and validates gateway leaf/key material through
  the same `loadTLS` implementation used at startup, including separation from
  existing inner/backend authorities. Each role runs under its consuming UID;
  connector preflight never opens the gateway leaf key. Missing or invalid
  existing authority material fails normal preflight and recovery. Initialization
  uses native declaration validation before creating gateway authorities; `up`
  then requires full material preflight. Native initialization is not activation.
- Native admin status now includes a bounded, credential-free per-entry snapshot:
  listener health and admitted registration counts for every declared resource.
  One Gate lock snapshots registrations across resources and paths, excluding
  cancelled or no-longer-authorized admissions. Python closed-decodes the added
  fields; event history, PIDs, and TCP success cannot establish readiness.
- Local connector `ExecStartPre` uses the existing native binary's `preflight`
  with paired declarations and `--socket LOCAL_ADDRESS`. Its five-second wait
  reuses `transport.VerifyLocalEntry` for exact TLS 1.3, SNI, dedicated trust,
  and HTTP/1.1 negotiation. `After`/`Wants` retain gateway ordering without
  `Requires`, `BindsTo`, or `PartOf`. The gateway's healthy sibling entry is not
  stopped by a failed connector boot check. Registration readiness is checked
  after connector startup, when registrations can exist.
- Activation checks native trust before dependent startup and admitted readiness
  before publishing the activation record. Path changes stop the owned connector,
  wait for its registrations to disappear, and exclusively bind-check old and
  replacement reverse addresses before replacement. Stale registrations and
  foreign listeners fail closed; no arbitrary process is killed. Gateway-only
  changes preserve unchanged connectors and wait for active owned public/local
  connectors to reconnect during local-affected transactions.
- Healthy unchanged local `up` converges without service mutations. Public
  recovery can retain an unavailable local listener. Status distinguishes
  declared selection, unit activity, events, and native admission readiness.
  Rollback stops failed owned processes, restores prior public configuration and
  running intent, revalidates prior trust under prior unit identities, and checks
  restored admission. Invalid prior trust reports recovery failure and retains
  recovery artifacts; private authority/credential state is never copied.
- The deployment schema and native verb set are unchanged. Preflight reuses the
  existing argument names. Bundle construction still builds
  `./cmd/anvil-connect`, and the manager zipapp still bundles the actual stdlib
  Connect Python modules. Rendered JSON continues through the actual Go readers;
  actual native admin responses now also pass through Python's closed decoder.

Validation:

- Final focused Python run: **171 passed, 2 skipped** across `test_manage.py`,
  `test_manage_isolation.py`, and `test_render.py`, including omission-byte
  stability, rendered-JSON reader parity, and native-status decoder agreement.
- The required root command `python scripts/run_tests.py tests/connect/ -x -q`
  was rerun with the supplied project virtualenv. It still stops before pytest:
  this sandbox exposes `/` and `/data` as UID 65534, violating the wrapper's
  trusted-ancestry contract. No trust check was weakened or bypassed in the wrapper.
- A broader direct diagnostic run, before the last three regression cases,
  recorded **570 passed, 4 skipped, 10 failed, 1 error**. Nine failures and the
  setup error require sockets denied with `EPERM`; the wheel build cannot create
  its lock in the read-only default cache. This is not a passing full suite.
- From `connect/`, `go build ./...` succeeded using a writable Go cache under
  `/tmp`. `go test ./internal/...` was executed and failed on existing socket
  fixtures with `operation not permitted`. The default Go cache is read-only in
  this sandbox; changing only the cache location allowed compilation.
- Focused native tests passed for `TestLocal`, `TestEntry`, `TestRegistration`,
  and `TestPreflight` across runtime, tunnelgate, and the shipped command. These
  cover exported material validation, paired CLI inputs, closed native status,
  registration/disconnection/revocation, and bounded entry behavior. Native bind
  qualification remains skipped where sockets are unavailable. The tunnelgate
  entry/registration tests also passed with `-race`.
- Python compilation and `git diff --check` passed. Independent Sol review found
  no remaining code blocker after fixes for shared TLS verification, atomic
  registration snapshots, initialization, and gateway-only reconnection gating.

Remaining gates and pushbacks: the sandbox prevents claiming the required full
Python/Go gates passed. Run them in the intended trusted, socket-capable test
host. PRD items **5–7** still require the dedicated live public/local/public
qualification, pinned transport and independently counted origin requests,
restart/rotation/failure/stale-registration/revocation/renewal-loss faults,
measured deadlines, explicit recovery evidence, sanitized artifacts, and final
independent acceptance review. No live activation, deployment, measured latency,
production recovery, or final ADR acceptance is claimed here. No remaining
implementation-scope pushback is identified.
