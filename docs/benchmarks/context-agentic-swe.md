# Context, agentic, and SWE benchmark jobs

Anvil Serving can run three durable evaluation families against an already
served router alias:

- `context` measures independently scored retrieval correctness across token
  buckets and target positions, then reports the first profile-defined drop and
  effective measured context;
- `agentic` measures planning, structured output, tool sequencing, dependent
  results, recovery, and final-answer behavior with deterministic fixture tools;
- `swe` runs a pinned mini-SWE-agent revision and requires the pinned official
  SWE-bench grader before a run can be complete.

These are evaluation jobs, not model lifecycle commands. Submission, preflight,
execution, cancellation, evidence retrieval, and owned cleanup do not start,
stop, reroute, or promote a model.

Agentic long-session cases are true incremental conversations: the worker sends
each scripted turn, retains the endpoint's actual reply, and requires strictly
increasing reported prompt tokens. Prefilling one synthetic transcript is not
an endurance measurement.

## Worker boundary

Run repository workloads on a registered, isolated benchmark worker. The
worker reaches the model through an Anvil router URL such as
`http://100.64.0.10:8000/v1`; it does not run untrusted repositories on the
model host. Put the real endpoint, worker registration, and credential
environment reference in the private operator configuration. Public evidence
uses the generic address above.

The controller launches a detached worker after durable submission. That
worker claims the run ID exactly once, prepares pinned assets, performs
read-only preflight, executes the selected suite, and writes partial or
terminal evidence to the owned run directory. Status, bounded cursor logs,
cancellation, and artifact retrieval survive the initiating CLI request.
Routine operation uses the CLI or controller tools; it does not require SSH or
operator-issued Docker commands. The managed SWE adapter may use the worker's
container runtime internally.

The model client runs in the worker process. Each SWE task container is started
with `--network none`, so an evaluated task cannot fetch a solution over the
network or reach the endpoint directly. Image and pinned-asset preparation happen
before the task container starts.

Before a measured campaign, verify:

- the worker differs from the model host;
- its platform, architecture, free disk, and container capability satisfy the
  selected profile;
- the credential named by `endpoint.auth_env` exists in the worker process;
- `/models` returns the exact router alias and observed context;
- prepared repositories and images match their immutable revisions or digests;
- the owned evidence directory is writable.

The default repository benchmark topology is isolated. An explicitly selected
`co-resident` client topology is permitted for endpoint-only context or agentic
diagnostics, but its artifacts must retain that topology as a performance and
isolation caveat; it must not be presented as isolated repository execution.

SWE-bench evaluation images are normally Linux x86-64. The managed adapter
selects `linux/amd64`; an Apple Silicon worker must prove that its configured
container runtime can execute that image under emulation. The immutable plan
records an absolute container-runtime executable so detached workers do not
depend on shell `PATH`. Host architecture alone is not proof. An incompatible
runtime is a preflight failure, not a model failure.

The worker environment must also select its active container context. The
managed adapter resolves Docker before asset preparation, including standard
Docker Desktop locations when a macOS launch service has a minimal `PATH`.
Mini-SWE-agent runs with a run-owned empty global-config directory so a user's
global `.env` cannot silently inject a stale `DOCKER_HOST`, credential, or
other ambient setting. Explicit worker-process container settings remain in
force. Credential values are trimmed at the child-process boundary to prevent
CRLF or surrounding whitespace from changing authentication; values remain
secret and never enter artifacts.

Pinned source checkouts are not installed into Anvil Serving's stdlib-only
runtime. Asset preparation creates a separate Python venv keyed by the exact
mini-SWE-agent and grader revisions plus platform and interpreter identity. It
records the full resolved package inventory and verifies that inventory on
reuse. The immutable run plan accepts only the platform's canonical venv
executable; on Unix its normal symlink must resolve to the exact interpreter
that created the environment.

## Versioned profiles

Profiles are content-addressed JSON under `configs/benchmarks/`. A result is
interpretable only with its profile SHA-256 and adapter identities.

The shipped context profiles execute the deterministic native cases listed in
their profile. RULER and MRCR records are supported as normalized evidence
inputs, but they are not silently substituted for or claimed by these profiles;
an external-adapter profile must explicitly pin and execute them before a run
can report those benchmark names.

Every SWE profile also pins the `princeton-nlp/SWE-bench_Verified` dataset
revision as a prepared adapter. The official grader's repository revision is
not a substitute for the dataset identity.

| Profile | Context buckets | Positions × repetitions | Agentic scope | SWE Verified instances | Intended use |
|---|---|---:|---|---:|---|
| `smoke` | 8K, 32K | 3 × 1 | tool sequence and one recovery fixture | 1 | Wiring and short functional gate |
| `scout` | 8K, 32K, 131K, 262K | 5 × 2 | 9 cases: planning, reasoning, structured output, sequential/parallel/dependent tools, recovery, debugging, and context recovery | 5 | Find likely failure regions before a deep run |
| `deep` | 8K through 640K in seven buckets | 7 × 3 | the scout matrix plus long-session retention | 25 | Expensive degradation and repository campaign |

SWE instance IDs are always explicit, ordered, unique, and equal in count to
the selected profile's `instance_limit`. The adapter never silently replaces a
missing or broken smoke instance with another task.

## Canonical unattended workflow

First inspect the immutable plan. A dry run performs no endpoint request,
artifact write, asset download, or model lifecycle action:

```powershell
anvil-serving eval benchmark context --profile smoke --dry-run
anvil-serving eval benchmark agentic --profile smoke --dry-run
anvil-serving eval benchmark swe --profile smoke --dry-run
```

Build the portable job specification in the private operator workspace. This
example is intentionally generic; set a unique run ID and current timestamp,
and use the real worker-side credential variable name without placing its value
in JSON:

```json
{
  "schema": "anvil-serving.benchmark-job-spec/v1",
  "run_id": "deepseek-context-smoke-001",
  "ownership_id": "deepseek-campaign",
  "suite": "context",
  "profile": "smoke",
  "endpoint": {
    "base_url": "http://100.64.0.10:8000/v1",
    "model": "llm.primary",
    "auth_env": "ANVIL_ROUTER_TOKEN"
  },
  "worker": {"id": "benchmark-worker"},
  "submitted_at": "2026-08-03T12:00:00Z",
  "timeout_s": 7200,
  "parameters": {
    "model_host_id": "model-host",
    "case_limit": 1,
    "advertised_context": 650000,
    "reasoning_effort": "xhigh",
    "temperature": 1.0,
    "top_p": 0.95
  }
}
```

`reasoning_effort` is optional and may be `none`, `minimal`, `low`, `medium`,
`high`, `max`, or `xhigh` when the selected model supports it. For model
families that use chat-template thinking controls, set `thinking_mode` to
`enabled` or `disabled` instead. Agentic and SWE jobs require these controls
to be mutually exclusive. Context jobs can combine them when the selected
model supports both, and accept `clear_thinking` only with enabled thinking.
All three suites forward the selected controls and record them in evidence.

`parameters.temperature` and `parameters.top_p` are optional sampler controls
for context, agentic, and SWE jobs. Temperature must be finite from 0 through
2; top-p must be finite, greater than 0, and at most 1. Context and agentic
jobs retain the existing omitted-control behavior: temperature 0.0 and no
`top_p` request field; their request controls record the effective and sent
values. For SWE, sampler fields are added to the generated LiteLLM
`model_kwargs` only when a sampler control is supplied. Legacy SWE artifacts
without sampler evidence retain their exact legacy defaults as unknown; they
must not be treated as temperature 0 or as comparable to a recorded sampler.

`case_limit` is useful for the first smoke only; omit it for the complete
profile matrix. For SWE, replace the context parameters with
`"instance_ids": ["owner__repo-NNN"]` and provide exactly the profile's
declared count.

Submit to the registered worker through the controller and return after the
job is durable:

```powershell
anvil-serving eval benchmark context submit `
  --target host-role:benchmark-worker `
  --transport controller `
  --spec-json $spec `
  --detach `
  --confirm
```

Observe and retrieve the run through Anvil:

```powershell
anvil-serving eval benchmark context status --run-id deepseek-context-smoke-001 `
  --target host-role:benchmark-worker --transport controller
anvil-serving eval benchmark context logs --run-id deepseek-context-smoke-001 `
  --cursor 0 --limit 100 --target host-role:benchmark-worker --transport controller
anvil-serving eval benchmark context artifact --run-id deepseek-context-smoke-001 `
  --target host-role:benchmark-worker --transport controller
```

The terminal artifact contains digest-bound stage paths. Retrieve a referenced
stage through the same controller rather than reading the worker filesystem:

```powershell
anvil-serving eval benchmark context artifact --run-id deepseek-context-smoke-001 `
  --path evidence/0-context.json `
  --target host-role:benchmark-worker --transport controller
```

Cancellation first records partial evidence. It terminates a worker only when
its process command can be verified against the owned run and then removes only
that run's `work/` directory. When process identity cannot be verified, active
cleanup is deferred instead of risking another process. Shared caches and
retained evidence survive:

```powershell
anvil-serving eval benchmark context cancel --run-id deepseek-context-smoke-001 `
  --target host-role:benchmark-worker --transport controller --confirm
```

Use the equivalent `agentic` or `swe` command family for the other suites.

## What each artifact means

Context artifacts distinguish:

- advertised context: a source claim;
- configured context: the served endpoint's declared limit;
- attempted buckets: only lengths actually sent;
- effective context: the highest attempted bucket that still satisfies the
  versioned pass-rate and relative-drop policy;
- capacity: whether a request completes;
- quality: whether the independently hidden answer remains correct.

Agentic artifacts score protocol, applicable reasoning, result incorporation,
recovery, history, and final answer separately. Tool-result final answers may
use natural language as long as every deterministic marker is present. The raw
private artifact retains the visible answer and per-turn measurements so a
formatting miss cannot be mislabeled as a reasoning failure. Parser failure,
reasoning-budget exhaustion, recovery failure, final-answer failure, and
infrastructure failure remain distinct.

SWE artifacts retain the prompt/dataset identity, trajectory hash, request IDs
when exposed, token counts, duration, exit status, prediction hash, pinned agent
harness, pinned grader, and exact reasoning/thinking request control. A
trajectory or prediction without an official
grader report is `incomplete`, even when the agent exited normally. When
provided, sampler controls and their effective request policy are retained;
missing legacy sampler fields remain unknown.

The common evidence envelope labels a record `measured` or `external_prior`,
records ordered stage references with SHA-256 hashes, and preserves one of
`completed`, `incomplete`, `failed`, or `cancelled`. Failed and incomplete runs
retain useful stage evidence but cannot make completed-run assertions.

## Publication and decision boundary

A dry run, fixture test, or unpublished private smoke does not create a dated
finding. For a real measured campaign, apply the full publication matrix:
dated finding and index, run catalog, model dossier, and measured-hardware page.
Publish references to sanitized raw artifacts, not credentials, private
network identity, prompts, or reasoning text. Keep external priors structurally
separate from locally measured runs.

Every result carries the same boundary: benchmark evidence does not authorize
model promotion; promotion is a separate human decision.
