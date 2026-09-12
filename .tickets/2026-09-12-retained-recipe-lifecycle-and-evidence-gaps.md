# Retained recipe lifecycle and benchmark evidence gaps

Status: serialized-campaign implementation reviewed; general release held.

## Observed failure

The RTX 5090 incumbent was label-owned by recipes but absent from the current
recipe registry and serve manifest. Unload would remove its exact restart
configuration. An isolated bakeoff therefore lacked a managed retain/restart
path. Raw Docker mutation was not used as a workaround.

## Fix-forward

Add guarded `models recipes stop` and `start`, retaining immutable container
identity, model/revision/recipe/registry/image fingerprints. Missing registries
remain recoverable through label discovery; ambiguity fails closed. Bound all
mutation and rediscovery subprocesses, preserve uncertain timeout outcomes,
require declared Docker health, and reject native KV offload restarts that need
preparation. HTTP-only recipes use managed unload/load instead.

Independent deterministic regression: seven new tests reproduced failures
before the fix; the focused model/lifecycle/CLI suite then passed 356 tests.
Subsequent review added exact chunked active-owner inventory, native process
and complete GPU telemetry guards, a narrow declared baseline-client allowance,
MCP stale-preview binding, and generated contract updates. The expanded
focused lifecycle/model/MCP/command set passed 253 tests before the final
absent-baseline regression; final full-suite results are recorded below.
Real stop retained the incumbent; real restart and post-run functional checks
completed successfully in the dated campaign restoration record.

## Remaining evidence/product follow-ups

- Worktree source is v1.2.1 but the local installed version reports 1.0.0.
  Keep exact commit and verified module source in each campaign; packaging
  metadata reconciliation is separate from runtime performance.
- The session lacks Anvil controller tools; the documented worktree CLI is the
  verified execution surface. New CLI lifecycle parity with operation contracts
  and controller tools is implemented in typed `recipe_manage` stop/start.
- Independent review accepts the bounded path for this one-operator serialized
  campaign, but holds general merge/release: there is no shared host lock across
  recipes, serves, and external lifecycle actors. Add a host-wide managed
  lifecycle lock and barrier-controlled race tests before general release.
- A declared baseline compute PID is not a process-identity attestation. For
  this campaign recheck its Windows executable identity and process start time
  immediately before preview/apply, and preserve the existing camera utility.
  A reusable general-release allowance must bind immutable process identity.
- `eval benchmark evidence show` flags unused built-in suites and absent chat
  timing on an external-suite-only artifact, although those suites were not
  selected. Preserve native records and label the normalized warnings; do not
  invent timings or mark unselected suites executed.
- The old `eval benchmark run` spelling in AGENTS.md is not a registered verb;
  focused help identifies `eval benchmark capacity` as the supported surface.

No route or promotion is authorized by this ticket. Campaign-specific human
authorization is recorded separately in the evidence bundle.

## Additional Windows regression fixes

The real Pi smoke exposed two existing Windows defects: Node's CSPRNG aborted
without `SystemRoot` in the isolated test environment, then socket-only
`select()` could not poll subprocess pipes. Preserve just `SystemRoot` in the
fixture and use bounded stdlib `PeekNamedPipe` on Windows. A real-child pipe
test failed before the transport fix; six focused tests including actual
Pi 0.85.1 native history/fork/resume passed. Independent Sol review accepted
the bounded change; concurrent polling remains outside the existing contract.

A later full run exposed a test-only HTTP framing ambiguity: a request-ID
header ending in zero matched the chunk terminator before the error body.
The deterministic segmented-socket reproduction failed before repair; all 19
request-runtime tests pass after restricting that marker to the HTTP body.
No production router code changed. Final full regression passed: 8,117 tests,
391 skipped in 381.30 seconds. Ruff, 22 documentation/evidence-consistency
tests, strict MkDocs, Markdown links, skill/template validation and full CLI
reference audit also passed. Subsequent changes are publication provenance,
verification records and byte-preserving recipe checkout attributes only;
focused integrity gates recheck those independently.

Exact incumbent restoration is now complete and independently reviewed in the
dated evidence bundle. General release remains held on the explicitly listed
shared-locking and immutable baseline process-identity gaps; a green test suite
does not remove that hold.
