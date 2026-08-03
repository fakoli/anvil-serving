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

Before a measured campaign, verify:

- the worker differs from the model host;
- its platform, architecture, free disk, and container capability satisfy the
  selected profile;
- the credential named by `endpoint.auth_env` exists in the worker process;
- `/models` returns the exact router alias and observed context;
- prepared repositories and images match their immutable revisions or digests;
- the owned evidence directory is writable.

SWE-bench evaluation images are normally Linux x86-64. An Apple Silicon worker
must prove that its configured container runtime can execute the selected
image; host architecture alone is not proof. An incompatible runtime is a
preflight failure, not a model failure.

## Versioned profiles

Profiles are content-addressed JSON under `configs/benchmarks/`. A result is
interpretable only with its profile SHA-256 and adapter identities.

| Profile | Context buckets | Positions × repetitions | Agentic scope | SWE Verified instances | Intended use |
|---|---|---:|---|---:|---|
| `smoke` | 8K, 32K | 3 × 1 | tool sequence and one recovery fixture | 1 | Wiring and short functional gate |
| `scout` | 8K, 32K, 131K, 262K | 5 × 2 | adds structured edit and debug loop | 5 | Find likely failure regions before a deep run |
| `deep` | 8K through 640K in seven buckets | 7 × 3 | adds context recovery | 25 | Expensive degradation and repository campaign |

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
    "advertised_context": 650000
  }
}
```

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

Agentic artifacts score protocol, reasoning, result incorporation, recovery,
history, and final answer separately. Parser failure, reasoning-budget
exhaustion, model behavior, and infrastructure failure remain distinct.

SWE artifacts retain the prompt/dataset identity, trajectory hash, request IDs
when exposed, token counts, duration, exit status, prediction hash, pinned agent
harness, and pinned grader. A trajectory or prediction without an official
grader report is `incomplete`, even when the agent exited normally.

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
