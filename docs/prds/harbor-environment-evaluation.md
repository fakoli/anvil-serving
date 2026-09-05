# Project: Isolated Harbor environment evaluation

## Summary

Add an optional `environment` benchmark suite that runs a pinned Harbor harness
and one pinned terminal agent against reviewed, independently graded tasks.
Integrate with Anvil's existing durable worker, preflight, run-directory ownership,
cancellation API, and evidence surfaces. Add the missing container ownership and
process-group cleanup mechanism explicitly. Start with a small synthetic task bundle and a real
container-plus-scripted-endpoint compatibility gate before running model evaluations.

## Status and evidence

**Status:** Proposed; compatibility spike required, not an installed integration.
**Priority:** Medium. **Anvil baseline:** `f50dca489780b95d0cd98dee59cf620618c4ccd1`.

Anvil already runs native context/agentic suites and pinned mini-SWE-agent with
the official SWE-bench grader. Harbor's useful addition is reusable terminal or
custom environment tasks outside the SWE-bench-specific workflow. It should not
replace official SWE scoring or become a second job scheduler.

The inspected baseline has run-owned filesystem paths and a detached worker PID,
not benchmark-owned container tracking. `cancel_benchmark_job()` verifies the
worker command and signals that PID; `cleanup_harness_work()` deletes only the
owned work directory. Neither supplies a durable task/verifier-container ledger
or process-group/container cleanup. R016 and T003 below are new prerequisites,
not existing behavior to reuse.

Miles describes an [experimental Harbor integration](https://github.com/radixark/miles/blob/d2fc97ce581577e255e494801d7568747d5a10d7/docs/user-guide/harbor.md).
Do not copy its training-specific Harbor branch. Directly reviewed upstream:
[Harbor `5c364a538e0af19eb58a53fdb895d7c0f974cef5`](https://github.com/harbor-framework/harbor/tree/5c364a538e0af19eb58a53fdb895d7c0f974cef5),
whose package declares version `0.22.0` and Python `>=3.12`.
These are inspected source identities, not a claim that this combination runs.

Relevant upstream contracts are
[JobConfig](https://github.com/harbor-framework/harbor/blob/5c364a538e0af19eb58a53fdb895d7c0f974cef5/src/harbor/models/job/config.py),
[TrialResult](https://github.com/harbor-framework/harbor/blob/5c364a538e0af19eb58a53fdb895d7c0f974cef5/src/harbor/models/trial/result.py),
and the [mini-SWE-agent adapter](https://github.com/harbor-framework/harbor/blob/5c364a538e0af19eb58a53fdb895d7c0f974cef5/src/harbor/agents/installed/mini_swe_agent.py).
Harbor can skip execution/grading in install-only mode and has an oracle agent
default. The Anvil adapter must explicitly prevent either from masquerading as
an evaluated agent run.

## Goals

- Evaluate terminal/file-manipulation tasks with an independent deterministic verifier.
- Preserve exact harness, agent, task, image, endpoint, and grading provenance.
- Reuse managed jobs and isolate optional dependencies from Anvil's stdlib runtime.
- Classify a task failure separately from harness, environment, routing, or grading failure.
- Give operators bounded preview/start/status/log/cancel/evidence behavior.

## Non-Goals

- RL training, Miles installation, Ray, an embedded Harbor service, or a plugin platform.
- Replacing native agentic tests, official SWE-bench grading, or existing profile hashes.
- Arbitrary public task downloads at job execution time, mutable dataset tags, or custom agents.
- Cloud model fallback, LLM-as-judge grading, regrading imported jobs, or trajectory resume.
- GPU workload scheduling, model lifecycle, promotion, or native Windows Harbor execution.
- A broad Terminal-Bench leaderboard claim from a handful of synthetic tasks.

## Requirements

- R001: Introduce suite ID `environment` through existing durable benchmark
  commands and MCP tools. Reuse preview, owned run IDs, preflight, worker claim,
  status, bounded logs, cancellation, and evidence retrieval. Unknown suites
  must fail explicitly, never fall through to the SWE runner.
- R002: Preserve the v1 profile schema, its exact three-suite requirement, and
  all existing profile bytes/hashes. Add a separately versioned v2 profile
  contract for a new `environment-smoke` profile containing only this suite.
  Version-dispatch validation; do not expand the old global suite set in place.
- R003: Lock Harbor to a full source commit, the installed agent to an immutable
  release/commit with verified dependency lock, tasks to exact files and hashes,
  and all task/base/verifier images to digests. Record the resolved interpreter,
  package inventory, adapter version, and lock hash. Mutable references fail preview.
- R004: Harbor and its dependencies run in an isolated worker environment, not
  Anvil's interpreter. V1 execution requires a registered Linux Docker-capable
  worker with a compatible Python 3.12+ interpreter. A Windows client/controller
  may submit jobs without installing Harbor. Missing capabilities fail preflight.
- R005: Use one allowlisted installed agent: Harbor's `mini-swe-agent` adapter,
  pinned and explicitly configured. Reject oracle, import paths, custom plugins,
  simulated users, skills, MCP servers, and arbitrary extra command/config input.
  Existing Anvil transport policy still applies; do not add a new model SDK client.
- R006: Route model requests only through the explicit Anvil endpoint/model
  contract. Forward the selected supported output/reasoning controls exactly or
  fail preflight if unsupported. Never reinterpret the alias as a cloud model,
  select by engine brand, fall back to another provider, or bypass readiness.
- R007: V1 uses one trial concurrently, one attempt per task, no hidden harness
  retries, and a bounded per-task and whole-job timeout. Preview shows every
  resource/download/time limit and the exact selected task list. Case order is
  stable; no random subset or implicit rerun of failures.
- R008: Execute reviewed single-step tasks in owned containers with bounded
  CPU/RAM/disk/time, no GPU devices, no privileged mode, no Docker socket, no
  host workspace/home mounts, and no host network. Narrowly allow agent traffic
  to the configured model endpoint; no cloud telemetry/upload/publishing.
- R009: Verifier code, expected answers, and result ingestion remain outside
  agent control. Use Harbor's supported separate verifier environment or prove
  equivalent protection in T001. Do not trust a reward file writable by the
  agent as independent grading evidence.
- R010: Mark a trial evaluated only when actual agent execution and terminal
  verifier completion are proven for the expected task checksum and trial ID.
  Exit code zero, environment setup, an agent final answer, install-only mode,
  disabled verification, or a fabricated summary alone is insufficient.
- R011: V1 selects one declared verifier reward key with binary values 0 or 1.
  Missing/null, boolean, non-finite, out-of-range, duplicate, conflicting, or
  unrecognized results produce grading/integrity failure, not reward zero.
  Reward 0 is an evaluated task failure; reward 1 is a task pass.
- R012: Report requested/evaluated/passed/failed/error/cancelled counts separately.
  An incomplete denominator cannot produce a passing suite. Preserve individual
  failure classes and original private artifact hashes; do not collapse all errors
  into model quality or publish arbitrary exception text.
- R013: Retain normalized evidence under owned run paths with exact identities,
  controls, timestamps, task checksums, results, and bounded artifact references.
  Raw trajectories and logs stay in the private operator evidence store, not Git.
  Credentials remain process/file-backed secrets outside persisted configs;
  scrub subprocess output and Harbor-generated manifests before publication.
- R014: Cancellation stops the owned harness process group and only its owned
  containers, then attempts bounded cleanup. A missing process/container is
  idempotent success. Never use a global prune, broad name match, or unvalidated
  recursive deletion. Cleanup failure is visible and cannot become a passed run.
- R015: Do not advertise the adapter as integrated until a real pinned Harbor
  run against a local scripted endpoint proves execution, grading, provenance,
  endpoint confinement, cancellation, and cleanup. Model-quality claims need
  a separate explicitly authorized managed benchmark with a real model.
- R016: Before managed environment execution, implement durable run-owned
  process/container tracking. Label each task/verifier container at creation
  with the run ID, ownership ID, a unique non-secret creation nonce, and role;
  persist exact container IDs and stage metadata under the owned run. Fence new
  creation on cancellation, stop and await the verified owned process group,
  then inspect exact IDs and require matching ownership labels before removal.
  Recover interrupted creation only with the exact persisted label/nonce tuple.
  Preserve cleanup results and fail closed on unknown or mismatched ownership.

## V1 profile and execution contract

The proposed new profile schema is `anvil-serving.benchmark-profile/v2`.
Keep the top-level fields `schema`, `name`, `description`, `content_sha256`,
`adapters`, and `suites`; v2 initially accepts only `name=environment-smoke`
and `suites={environment: ...}`. V1 continues accepting only smoke/scout/deep
with context/agentic/SWE. Do not make optional environment fields mandatory in
historical profiles, and do not index `suites["context"]` when validating v2.

The environment suite uses existing common fields (`cases`, `repetitions`,
`timeout_s`, `scoring`, `requirements`, `adapters`) plus these closed fields:

- `task_bundle`: identity of a packaged, reviewed manifest; not an arbitrary path.
- `agent`: fixed `mini-swe-agent` identifier.
- `max_steps`, `max_completion_tokens`: explicit request/agent limits.
- `max_concurrent_trials`: fixed 1 in v1.
- `max_attempts`: fixed 1; repetitions also fixed 1 in v1.
- `max_trial_seconds`: positive value no greater than the whole-job timeout.

`scoring` is closed to `reward_key`, `pass_value=1`, `fail_value=0`, and
`pass_rate_floor`. For the synthetic smoke profile the floor is 1.0.
Keep framework/agent/task/image references in the existing immutable adapter
lock forms where possible. A new reviewed task-bundle manifest binds task
paths, file hashes, agent/dependency lock, image digests, and verifier policy.
Hash that manifest into profile identity; there must be no unhashed execution input.

Proposed initial limits: 1-3 selected tasks, 32 agent steps/task, 4,096 output
tokens/request, 600 seconds/task, 1,800 seconds/job, and the stricter existing
global job/spec/log/download limits. Declare CPU, RAM, writable storage, and
download ceilings in the reviewed task-bundle manifest and validate them against
worker capability. T001 must choose actual digest-pinned images and validate
their resource limits; do not fill these fields with invented digests or claim
feasibility from a default alone.

Generate an owned Harbor config, then invoke the isolated environment's `harbor`
executable using an argument list and its pinned `run --config` interface, after
T001 confirms the exact CLI. Never concatenate a shell command. Explicitly set
`n_concurrent_trials=1`, `n_attempts=1`, retry count 0, `install_only=false`,
verification enabled, agent identity/version, endpoint/model settings, task list,
and owned output directory. Do not rely on Harbor defaults. Reject unsupported
resume/regrade/multi-step task paths in v1.

The endpoint is host-relative: container loopback does not reach a host service.
Resolve a declared worker-to-router network contract during preflight. Test this
with a container-reachable scripted endpoint. Do not silently rewrite loopback
to a guessed gateway or copy a private fleet address into a public fixture.

## New run-resource ownership contract

Add a bounded stdlib helper, proposed as
`anvil_serving/benchmarking/environment_ownership.py`, rather than pretending
the worker PID file or filesystem cleanup is a container registry. The journal
lives under the existing owned run root, outside its disposable `work` directory,
with schema `anvil-serving.benchmark-owned-resources/v1`. It records the run and
ownership IDs, creation intents/nonces, exact container IDs, task/verifier role,
stage, owned process-group identity, and terminal cleanup outcomes. V1 bounds:
16 containers, 64 KiB journal, 5 seconds per inspect/remove call, and 60 seconds
total cleanup. Exceeding a bound blocks further creation and records a failure.

Use this order:

1. Persist a creation intent and its unique ownership label tuple before asking
   Harbor to create the container. T001 must prove a pinned hook can apply those
   labels to every task/verifier container and return its full ID.
2. Persist the returned full ID before allowing the task's agent work to proceed.
   If the process dies between creation and persistence, reconcile only containers
   matching that intent's complete label/nonce tuple, inspect them, then journal
   recovered IDs. Never adopt by display name, image, or broad run-name prefix.
3. Persist a cancellation/terminal fence so neither a running nor restarted
   worker can create further containers. Stop and await the verified run-owned
   process group with bounded escalation before inspecting/removing containers.
   A PID alone is not proof of group ownership; record and verify launch identity.
4. Inspect each exact ID against its recorded labels before removal. A confirmed
   absent ID succeeds idempotently; transport failure is unknown, not absent.
   Leave mismatched/unverifiable resources untouched and record cleanup failure.
5. Retain the journal and sanitized cleanup evidence after work-directory cleanup.
   Terminal-job cleanup must remain retryable without restarting agent execution.

If the pinned Harbor adapter cannot expose a label/ID hook or enforce the creation
fence, stop at the compatibility gate. Any required narrowly scoped upstream
adapter patch must itself be pinned and reviewed. This PRD does not claim a
general retrofit of legacy SWE container cleanup; preserve old suite behavior.

## Result normalization

Add proposed module `anvil_serving/benchmarking/environment.py`. It orchestrates
the external harness and parses bounded JSON using stdlib; it must not import
Harbor/Pydantic into Anvil. Read the pinned `TrialResult` structure rather than
scraping console output. Validate exact task/trial/checksum membership and one
result per requested attempt before scoring.

Normalized per-trial fields: case/trial IDs, task checksum, execution status,
verifier status, selected reward or null, task passed or null, failure class/code,
observed timing/usage when present, and relative evidence references with hashes.
Use `anvil-serving.environment-suite-run/v1` for the suite result. Extend the
existing cross-suite measured evidence contract through its supported fields;
do not label this dataset `deterministic-native` or `SWE-bench_Verified`.

| Observation | Required classification |
| --- | --- |
| Executed agent + finished trusted verifier + reward 1 | Evaluated, task passed |
| Executed agent + finished trusted verifier + reward 0 | Evaluated, task failed; not a broken harness |
| Agent never ran / oracle / install-only / verification disabled | Invalid evaluation, never passed |
| Missing or malformed reward / verifier crash | Grading error, task result unknown |
| Image/setup/dependency failure | Worker runtime or harness failure |
| Endpoint authentication/readiness/model mismatch | Existing authentication/routing failure class |
| Task checksum mismatch / duplicate or foreign trial | Integrity/harness failure; reject result |
| Timeout or cancellation before terminal verifier | Incomplete/cancelled, not an evaluated zero |

The synthetic task bundle should include simple file/JSON transformation tasks
with trusted deterministic tests, not tasks that merely echo their own expected
reward. The scripted endpoint produces a correct solution in the positive run
and an intentionally wrong solution in a separate negative-control run. Neither
is a model-quality measurement.

## Implementation map

| Existing file | Required seam |
| --- | --- |
| `anvil_serving/benchmarking/profiles.py` | Schema-version dispatch and immutable v2 profile loading |
| `anvil_serving/benchmarking/harnesses.py` | Pinned repository/image preparation and isolated environment reuse |
| `anvil_serving/benchmarking/preflight.py` | Worker/interpreter/container/endpoint capability gates |
| `anvil_serving/benchmarking/worker.py` | Explicit suite dispatch, identities, final evidence and cancellation |
| `anvil_serving/benchmarking/jobs.py` | Durable spec bounds and ownership |
| `anvil_serving/benchmarking/jobs_cli.py` | Suite choices and common start/status/cancel surfaces |
| `anvil_serving/commands/eval.py` | Register the environment command family through existing helpers |
| `anvil_serving/control_plane/mcp/tools/benchmarks.py` | Suite/profile enums, preview, schemas, endpoint controls |
| `anvil_serving/benchmarking/swe.py` | Reference for pinned external agent and independent grader, not a base class to generalize prematurely |
| `anvil_serving/benchmarking/artifacts.py` | Evidence identities, integrity and bounded sanitized failure mapping |
| `anvil_serving/benchmarking/limits.py` | Shared execution bounds |

Proposed files: `benchmarking/environment.py`,
`benchmarking/environment_ownership.py`, a packaged environment-smoke
profile/task manifest, `tests/test_environment_ownership.py`, and
`tests/test_environment_benchmark.py`. Use the package's
existing data inclusion conventions and verify wheel contents. Do not introduce
a generic plugin registry or change native suite behavior to accommodate Harbor.

## Features

### F001: Pinned compatibility and isolated assets

**Requirements:** R002, R003, R004, R005, R006, R007, R008, R009

### F002: Managed execution and independent evidence

**Requirements:** R001, R010, R011, R012, R013

### F003: Cancellation, cleanup, and integration acceptance

**Requirements:** R014, R015, R016

## Tasks

### T001: Prove the pinned Harbor contract before product integration

**Feature:** F001
**Priority:** high
**Likely files:** proposed tests/fixtures/harbor/ and
tests/test_harbor_compatibility.py, tests/conftest.py, reviewed task-bundle
manifest, short compatibility note under docs/benchmarks/

Use an isolated Linux test worker, the exact reviewed Harbor source, a pinned
mini-SWE-agent/dependency set, digest-pinned task/verifier images, and a scripted
endpoint. Confirm CLI/config shape, agent version and controls, network policy,
telemetry opt-out, trusted verifier isolation, result files, and resource limits.
No model, GPU serve, or operator route change is required.
Use a test-only ownership harness to prove label injection and exact container-ID
capture here; T003 makes that mechanism durable in the product before managed runs.

**Acceptance criteria:**

- Retain exact pins and sanitized actual result/config-shape fixtures.
- A correct solution passes and an intentionally wrong solution fails the
  independent verifier; changing an agent-writable reward cannot forge a pass.
- The only model endpoint contacted is the selected scripted endpoint.
- A captured cancellation stops all owned work without deleting unrelated fixtures.
- Every task/verifier container exposes the configured ownership labels and
  exact ID, including creation interrupted before normal result recording.
- If any prerequisite cannot be enforced, stop after the compatibility note and
  mark that requirement blocked; do not weaken the contract or call mocks integration.

**Verification:**

- Record the exact isolated harness commands and lock identity in the compatibility note.
- Run the real positive/negative/cancellation experiments; source inspection is insufficient.
- After adding an explicit opt-in pytest option in `tests/conftest.py`:
  `python scripts/run_tests.py tests/test_harbor_compatibility.py --run-harbor-compatibility -x -q`.
  Without the option, ordinary CI skips external execution. With it, missing
  prepared assets/capabilities fail the gate; an all-skipped run is not acceptance.

### T002: Add v2 profile validation and isolated harness preparation

**Feature:** F001
**Priority:** high
**Dependencies:** T001
**Likely files:** benchmarking/profiles.py, harnesses.py, preflight.py,
packaged environment-smoke profile, tests/test_benchmark_profiles.py,
tests/test_benchmark_harnesses.py, tests/test_benchmark_preflight.py

Keep v1 validators unchanged behind version dispatch. Key the new harness cache
by source/agent/dependency lock, platform, and exact interpreter identity; verify
inventory on reuse. Do not reuse the SWE environment merely because both agents
have similar names. Offline missing assets fail without downloading.

**Acceptance criteria:**

- All old profile bytes/hashes and loading behavior are unchanged.
- Mutable refs, unsupported agents, unknown fields, mismatched task hashes,
  missing Python/Docker, unsafe image/task policy, and bad limits fail early.
- Anvil runtime import does not import or install Harbor dependencies.
- Preview lists selected tasks, images, controls, limits, and resolved capabilities.

**Verification:**

- `python scripts/run_tests.py tests/test_benchmark_profiles.py tests/test_benchmark_harnesses.py tests/test_benchmark_preflight.py -x -q`

### T003: Implement the missing run-resource ownership mechanism

**Feature:** F003
**Priority:** high
**Dependencies:** T002
**Likely files:** proposed benchmarking/environment_ownership.py,
tests/test_environment_ownership.py, benchmarking/worker.py cancellation seam

Implement the journal, creation fence, verified process-group termination, and
exact label-and-ID cleanup described above. Use injected container/process
transports for ordinary tests. Do not reuse run-owned filesystem cleanup as proof
that task containers stopped. Wire terminal-state cleanup retry without rerunning
the benchmark or deleting its evidence.

**Acceptance criteria:**

- Each creation is journaled with run/owner/nonce/role, exact ID, and stage.
- Crash between create and ID persistence is recovered through exact intent
  labels; unrelated or mismatched containers are never adopted or removed.
- Cancellation racing creation fences future work and cleans only verified owned
  resources after the process group stops; PID reuse fails ownership validation.
- Already-absent resources succeed; inspect timeout, removal failure, corrupt
  journal, and exhausted bounds remain visible failures, not clean success.
- Sentinel containers/directories survive; work cleanup retains the resource
  journal and allows a later cleanup retry for failed/cancelled jobs.

**Verification:**

- After creating the test: `python scripts/run_tests.py tests/test_environment_ownership.py tests/test_benchmark_worker.py tests/control_plane/test_benchmark_jobs.py -x -q`
- Extend the opt-in Harbor compatibility test to prove real label/ID capture and
  cleanup; mock container responses alone do not establish hook compatibility.

### T004: Implement the bounded run plan and independent result parser

**Feature:** F002
**Priority:** high
**Dependencies:** T003
**Likely files:** proposed benchmarking/environment.py and tests/test_environment_benchmark.py

Separate plan construction, subprocess execution, and result parsing so each
can be tested without Docker. Use the T001 actual result shape as the positive
fixture. Add corrupted variants; never accept the harness's aggregate summary
without validating each required trial and verifier result.

**Acceptance criteria:**

- Reward 0 is an evaluated failure, while missing/null/NaN/boolean reward is an error.
- Duplicate/foreign trials, task mismatch, missing terminal verifier, install-only,
  oracle-agent output, and modified artifacts cannot produce a passing suite.
- Endpoint credentials do not appear in arguments, configs, normalized evidence,
  or bounded public-facing logs; test with synthetic sentinel values.
- Result reading is bounded and confined to owned directories.

**Verification:**

- After creating the test: `python scripts/run_tests.py tests/test_environment_benchmark.py tests/test_benchmark_artifact_integrity.py -x -q`

### T005: Integrate durable worker, CLI, MCP, and evidence identities

**Feature:** F002
**Priority:** high
**Dependencies:** T004
**Likely files:** benchmarking/worker.py, jobs_cli.py, commands/eval.py,
control_plane/mcp/tools/benchmarks.py, relevant worker/controller/CLI tests

Make `_run_suite` explicitly dispatch every known suite and reject everything
else; its current non-context/non-agentic path is SWE. Update dataset/harness
identity construction for environment runs. Reuse existing durable semantics and
confirmation/preview gates, and update all suite/profile enums together.

**Acceptance criteria:**

- Proposed `eval benchmark environment` verbs behave like other durable suites.
- CLI and MCP preview resolve the same canonical spec and reject the same invalid input.
- A worker error never silently changes suite, agent, alias, or task selection.
- Evidence retrieval reports completeness, independent grading and exact provenance.
- Existing context/agentic/SWE jobs and historical evidence still pass regression tests.

**Verification:**

- `python scripts/run_tests.py tests/test_benchmark_worker.py tests/test_benchmark_jobs.py tests/control_plane/test_benchmark_jobs.py tests/control_plane/test_benchmarks.py -x -q`
- Run current command-manifest/schema/help checks for the new command family.

### T006: Harden cancellation and rerun the real integration gate

**Feature:** F003
**Priority:** high
**Dependencies:** T005
**Likely files:** benchmarking/environment.py, harnesses.py,
tests/test_environment_benchmark.py, worker/controller cancellation tests

Use the new ownership journal, creation fence, and cleanup mechanism from T003.
Exercise cancel during setup,
agent execution, and verification, plus an already-absent container. Bound
cleanup and preserve evidence. An unrelated sentinel container/directory must
survive every cleanup test.

**Acceptance criteria:**

- Cancellation and worker failure cannot leave a success result or retry a task.
- Owned resources are removed when safe; cleanup failures identify remaining
  owned resource IDs privately without broad destructive fallback.
- Re-run T001 through the managed Anvil command path, not just direct Harbor.
- Record actual positive, negative, timeout, and cancellation artifacts and pins.

**Verification:**

- `python scripts/run_tests.py tests/test_environment_benchmark.py tests/test_benchmark_worker.py tests/control_plane/test_benchmark_jobs.py -x -q`
- Run the opt-in real container/scripted-endpoint acceptance test on the supported worker.

### T007: Document boundaries and package the optional integration

**Feature:** F003
**Priority:** medium
**Dependencies:** T006
**Likely files:** docs/cli/eval.md, docs/benchmarks/context-agentic-swe.md,
proposed docs/benchmarks/environment-evaluation.md, packaging/manifest tests

Document supported worker prerequisites, prepare/preview/start/status/cancel,
offline behavior, evidence classification, raw-artifact privacy, and how a
reviewed future task bundle earns an immutable identity. Include the no-promotion
disclaimer and distinguish a scripted integration smoke from a real model run.

**Acceptance criteria:**

- A packaged install contains the profile/manifest and usable CLI/MCP schemas.
- The core package remains stdlib-only and native suites need no Harbor install.
- The handoff maps every requirement to tests or actual integration artifacts.
- No model-quality or public leaderboard claim exceeds the executed task set.

**Verification:**

- `python scripts/run_tests.py tests/ -x -q`
- Run current packaged-wheel and command-manifest checks.
- `python -m mkdocs build --strict`
- `git diff --check`

## Acceptance Criteria

The feature is integrated only when an Anvil-managed run with real pinned Harbor
containers and a scripted local endpoint executes trusted tasks, distinguishes
correct and incorrect solutions, records exact provenance, rejects forged/missing
grades, and cancels without collateral cleanup. That proves the adapter, not the
quality or readiness of any model. Real model qualification remains separate.

## Risks

- Harness/agent drift can change result schemas, controls, or hidden retry behavior.
- Default oracle/install-only paths can create convincing but invalid evaluation artifacts.
- Shared writable verifier state can invalidate grading independence.
- Container networking and credential serialization are part of the integration
  contract, not deployment details to defer until after implementation.
- Strong container resource/egress isolation may require capabilities absent on
  a worker; fail preflight rather than pretending those limits are enforced.

## Assumptions

### A001: One reviewed agent and small single-step bundle are sufficient for v1.

**Rationale:** This proves the new evaluation seam without a general harness plugin system.
**Requirements:** R005, R007, R009

### A002: Source pin inspection does not prove installation compatibility.

**Rationale:** Python, dependencies, task images, networking, and grading must run together.
**Requirements:** R003, R004, R015

## Open Questions

- T001 must resolve actual agent/dependency/image pins and demonstrate isolation.
  These are evidence-producing prerequisites, not permission to choose mutable
  defaults or to begin a production model benchmark.
- Parked: broad Terminal-Bench bundles, multiple agents, multi-step resume,
  distributed trials, arbitrary custom task upload, and live model leaderboards.

## Rollout and rollback

Ship as an optional suite only after the real integration gate. Preparing assets
requires the existing explicit managed action; merely installing Anvil does not
download Harbor/images. Removing/disabling the new suite leaves old profiles and
evidence intact. Do not delete shared caches during rollback. Worker installation,
real model evaluation, publication of sanitized findings, and model promotion
are separately authorized actions.
