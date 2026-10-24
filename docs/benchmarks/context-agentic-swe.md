# Context, agentic, coding, and performance benchmark jobs

Anvil Serving can run five durable evaluation families against an already
served router alias:

- `context` measures independently scored retrieval correctness across token
  buckets and target positions, then reports the first profile-defined drop and
  effective measured context;
- `agentic` measures planning, structured output, tool sequencing, dependent
  results, recovery, and final-answer behavior with deterministic fixture tools;
- `swe` runs a pinned mini-SWE-agent revision and requires the pinned official
  SWE-bench grader before a run can be complete;
- `aider` uses pinned Aider whole-file edits and Polyglot tasks, with the
  upstream test callback executing generated code in contained containers;
- `performance` runs pinned Local Inference Lab or AIPerf against one explicit
  workload cell, preserving native measurements and incomplete results.

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

Detached job execution currently requires Linux process-start identity so
cancellation can verify the owned worker. Other hosts can control the Linux
worker remotely. SWE preparation and planning additionally require POSIX
tooling: the pinned grader imports `resource` and mini-SWE uses POSIX shell
cleanup. Native Windows cannot prepare a local SWE worker.

The controller launches a detached worker after durable submission. That
worker waits for its verified cancellation record before it can claim the run
ID, prepare assets, or send requests. A failed launch handoff stops the child;
a lost parent before handoff leaves the child unable to execute.
The worker claims the run ID exactly once, prepares pinned assets, performs
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

When a frozen paired SWE job reports missing task images, preview and then
prepare only that job's pinned image references:

```bash
anvil-serving eval benchmark swe images --spec SWE_JOB.json --worker WORKER_ID --dry-run
anvil-serving eval benchmark swe images --spec SWE_JOB.json --worker WORKER_ID --confirm
```

Run the command locally on the Linux worker named by the frozen specification. The
specification must bind both `paired_image_ids` and
`paired_image_repo_digests` for every selected instance. The receipt records
the observed local hostname, local Docker socket, Docker storage root, each
selected instance, official tag, immutable registry digest, bounded command
diagnostics, and post-pull identity observation. The command refuses a remote
Docker endpoint or a tag whose local identity or registry digest differs from
the frozen mapping.

Before a measured campaign, verify:

- the worker differs from the model host;
- its platform, architecture, free disk, and container capability satisfy the
  selected profile;
- the credential named by `endpoint.auth_env` exists in the worker process;
- `/models` returns the exact router alias and observed context;
- prepared repositories and images match their immutable revisions or digests;
- the owned evidence directory is writable.

The default repository benchmark topology is isolated. An explicitly selected
`co-resident` topology is also available for endpoint diagnostics and managed
SWE jobs with contained task and grading containers. Its artifacts retain the
shared-host performance and isolation caveat; it is not isolated repository
execution. Set the same actual identity for the worker and model host.

Managed SWE task and grading containers have no network, an 8 GiB memory and
memory-plus-swap ceiling, four CPUs and a 512-process ceiling. The pinned official
grader runs through a source-verified compatibility launcher that guards its
Docker create path, rejects conflicting settings, verifies effective container
limits before startup, and refuses automatic image builds. Prebuilt official
SWE images are required. The official test oracle is unchanged; adapter and
source digests remain in the native plan and result. These bounds do not make
shared-host performance comparable to a separate benchmark worker.

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

New v2 jobs store the admitted `profile_sha256` in their durable specification.
Submission previews return that bound specification. Workers reject missing or
changed v2 profile pins before preparation or model traffic; older v1 jobs keep
their original contract. Aider also verifies the prepared Python environment's
actual bytes before planning and execution, so a stale receipt cannot authorize
changed harness code.

The shipped context profiles execute the deterministic native cases listed in
their profile. RULER and MRCR records are supported as normalized evidence
inputs, but they are not silently substituted for or claimed by these profiles;
an external-adapter profile must explicitly pin and execute them before a run
can report those benchmark names.

Every SWE profile also pins the `princeton-nlp/SWE-bench_Verified` dataset
revision as a prepared adapter. The official grader's repository revision is
not a substitute for the dataset identity.

Both running harnesses receive the same local data directory from that pinned
checkout. The supported snapshot's Parquet bytes are verified before each
stage and retained by digest in the native evidence; an unresolved LFS pointer,
changed content, or extra discoverable dataset file blocks execution.

| Profile | Context buckets | Positions × repetitions | Agentic scope | SWE Verified instances | Intended use |
|---|---|---:|---|---:|---|
| `smoke` | 8K, 32K | 3 × 1 | tool sequence and one recovery fixture | 1 | Wiring and short functional gate |
| `scout` | 8K, 32K, 131K, 262K | 5 × 2 | 9 cases: planning, reasoning, structured output, sequential/parallel/dependent tools, recovery, debugging, and context recovery | 5 | Find likely failure regions before a deep run |
| `pilot-v2` | 8K, 32K | 3 × 1 | same cases as smoke, with enforced budgets | 1 | Bounded contract pilot |
| `deep` | 8K through 640K in seven buckets | 7 × 3 | the scout matrix plus long-session retention | 25 | Expensive degradation and repository campaign |

`smoke`, `scout`, and `deep` retain their v1 bytes and behavior. Select
`pilot-v2` explicitly to use the v2 budget contract:

```bash
anvil-serving eval benchmark context --profile pilot-v2 --dry-run
```

Each v2 suite binds five limits into the profile hash, admission evidence,
and execution result:

| Field | Meaning |
|---|---|
| `max_model_calls` | Total model transport attempts, including failed attempts and retries; context calibration also counts |
| `max_completion_tokens` | Maximum requested completion tokens per attempt |
| `deadline_s` | Suite execution allowance; job `timeout_s` can shorten it |
| `max_retries` | Extra transport attempts per logical model request |
| `min_free_disk_bytes` | Required free disk reserve, checked before preparation and execution; not a disk usage quota |

The product of calls and per-attempt tokens is a theoretical ceiling, not
observed usage. Missing token counts stay unknown. Native suites perform no
retries; SWE observes retries at its pinned HTTP transport boundary. Exhausted
budgets stop further model calls and retain an incomplete suite result; the
worker records a failed job with the partial observations available.

Native deadlines use a monotonic clock, limit each request timeout to the
remaining allowance, and detect an overrun when the request returns. A socket
timeout is not a hard wall-clock process deadline. SWE also uses its bounded
external runner. Asset preparation is separate from suite execution. Unknown
suite names are rejected before new durable submission or worker preparation;
historical records remain readable. V2 jobs reject unsupported parameter keys
and overrides that widen token, repetition, or deadline limits. V2 context
case IDs, token buckets, and positions must be subsets of the profile matrix;
smaller output caps and case limits can narrow a run.

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
`enabled` or `disabled` instead. Agentic, SWE, and Aider jobs require these controls
to be mutually exclusive. Context jobs can combine them when the selected
model supports both, and accept `clear_thinking` only with enabled thinking.
All five suites forward the selected controls and record them in evidence.

`parameters.temperature` and `parameters.top_p` are optional sampler controls
for context, agentic, SWE, and Aider jobs. Temperature must be finite from 0 through
2; top-p must be finite, greater than 0, and at most 1. Context and agentic
jobs retain the existing omitted-control behavior: temperature 0.0 and no
`top_p` request field; their request controls record the effective and sent
values. For SWE, sampler fields are added to the generated LiteLLM
`model_kwargs` only when a sampler control is supplied. Legacy SWE artifacts
without sampler evidence retain their exact legacy defaults as unknown; they
must not be treated as temperature 0 or as comparable to a recorded sampler.
Aider forwards supplied sampler controls through its pinned model client and
records the effective request controls. Omitted controls retain upstream defaults.

`case_limit` is useful for the first smoke only; omit it for the complete
profile matrix. For SWE, replace the context parameters with
`"instance_ids": ["owner__repo-NNN"]` and provide exactly the profile's
declared count.

For a paired SWE comparison, also supply `parameters.paired_image_ids`, mapping
every selected instance ID to its previously observed full `sha256:` image ID.
The keys must match `instance_ids` exactly. Preserve these cached official
instance images throughout both runs. Paired mode checks the cached tag and
retains image IDs and repository digests before the agent, before the grader,
and after grading; a missing or changed identity leaves the run incomplete.
It never pulls a missing task image. The grader also rejects pulls and creates
its containers by the validated immutable image ID, verifying the created
container before startup. The image-policy file is digest-bound to the plan.

Task image checks are observations at stage boundaries, not a lock against an
unrelated process retagging Docker images while tasks run. Keep cache writers
quiescent during paired runs. An omitted map preserves the legacy unpaired
behavior and produces no paired image observations; never infer immutable image
coverage from those historical results or from a mutable `latest` tag alone.

Submit to the registered worker through the controller and return after the
job is durable:

```bash
anvil-serving eval benchmark context submit --spec JOB.json --target host-role:benchmark-worker --transport controller --detach --confirm
```

Submission and preflight accept either `--spec JOB.json` or `--spec-json JSON`,
never both. A file is read and validated on the calling machine before its JSON
is sent to the controller; the worker does not need that caller-side path.
Both forms use the same bounded parser and suite validation. MCP callers use
the equivalent inline `spec_json` argument.

Retry the original specification to retrieve the same durable job. The run ID,
owner, timestamp, and other fields must remain unchanged. A retry retains the
original admitted profile pin even if the installed profile has since changed;
it does not start another worker. A new job validates the currently installed
profile before admission, and its worker checks that pin before traffic.
If the initial worker launch fails, the queued job remains visible and
submission reports the launch error.
Cancel that job and use a new run ID for a new attempt; resubmission is not a
worker restart command.

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

Use the equivalent `agentic`, `swe`, `aider`, or `performance` command family for the other suites.

## Aider Polyglot

Inspect the profile and prepare its exact sources, Python environment, and
test image on the worker:

```bash
anvil-serving eval benchmark aider --profile pilot-v2 --dry-run
anvil-serving eval benchmark aider prepare --profile pilot-v2 --ownership-id pilot --run-id aider-preparation --confirm
```

Use the same durable submission, status, logs, cancellation, and artifact
commands with the `aider` suite. The initial prepared selection contains one
eligible task per language: C++, Go, Java, JavaScript, Python, and Rust.
Selection order and exclusions are retained. An explicit `instance_ids` list
may narrow that prepared selection; it cannot introduce an unprepared task.
Each task gets at most two upstream attempts with test feedback. The pilot
caps the suite at 12 transport attempts, 16,384 completion tokens per request,
and one hour of execution. Preparation time is recorded separately.

Preparation must prove that every selected starter fails and its reference
solution passes the same upstream test command in the exact prepared image.
Missing or failed oracle evidence blocks model traffic. The image receipt
freezes its base digest, build inputs, resolved package inventory, and final
image ID. Mutable package resolution during preparation means that a future
rebuild is not promised to be byte-identical.

Test containers have no external network, credentials, GPU, or host socket
access. Container-local IPC is allowed for the pinned toolchains. They use
a read-only root filesystem and a read-only source bind at `/input`; special
files, including sockets, are rejected before mounting. Tests run on a copy in
`/task`, a 4 GiB tmpfs limited to 65,536 inodes. `/tmp` has a separate 1 GiB,
16,384-inode ceiling. Containers have 8 GiB memory and memory-plus-swap limits,
four CPUs, and at most 512 processes. Scratch state is discarded with the
container; nothing is copied back over the host-owned transcript or results.
The adapter verifies these settings and container ownership before starting
tests. Cancellation stops the owned worker, then verifies and removes its
owned test containers. An unverified stop or container leaves cleanup deferred.

The pinned upstream callback controls original-test restoration, test command
selection, timeout feedback, and output normalization. The bridge verifies its
source hashes and changes only where the subprocess executes. Plans, selected
inputs, raw results, per-attempt feedback, and partial evidence stay under the
owned run's `aider/` directory, outside disposable `work/`. Task IDs are relative
paths; artifact filenames use their hashes. Neither a successful process exit
nor a completed job alone establishes coding quality: inspect scored attempts
and retained failures.

## LIL and AIPerf performance

Prepare the pinned Python environments on a Linux benchmark worker:

```bash
anvil-serving eval benchmark performance prepare --profile pilot-v2 --ownership-id pilot --run-id performance-preparation --confirm
```

Use the same durable submission commands with `suite: "performance"`,
`profile: "pilot-v2"`, and a job timeout no greater than 1,800 seconds. Its
parameters select one finite workload cell, for example:

```json
{"runner":"lil","workload":{"input_tokens":0,"output_tokens":256},"concurrency":4,"request_count":16,"cache_condition":"basic","temperature":0,"top_p":1}
```

The initial adapter accepts finite request counts, no warmup, and the `basic`
cache condition. Duration runs, cache manipulation, and positive LIL input
padding are rejected before traffic. LIL's zero input target still sends its
native prompt; it does not mean a zero-token request. AIPerf accepts its native
synthetic input target and uses a prepared offline `o200k_base` tokenizer.
Neither target is proof of the served model's measured input length or matching
tokenizer. Requested concurrency and workload remain configured values.

The pilot caps transport attempts at 128, completion requests at 4,096 tokens,
and execution at 1,800 seconds, with no retries. The guard observes actual
HTTP sends across native workers, including failed attempts, and denies changed
aliases, destinations, redirects, and administrative requests. Its validation
and ledger add pre-dispatch overhead. A bounded response observer checks SSE
validity and explicit completion while forwarding unchanged bytes to the native
parser, with a 64 KiB limit per event. That receive-side work is included in
observed timings; measurements describe this managed harness. Missing usage
remains an unknown metric, while
malformed or truncated streams cannot make a completed-run assertion.
The adapter neither resets nor proves server cache state. It omits the exact
source-verified unconditional LIL warmup block, which otherwise sends a request
even when native warmup options are zero. This instrumentation difference is
recorded; native response parsing and measurement remain upstream-owned.

Preparation pins the upstream source and resolved dependency inventory in
separate environments. Planning and execution check actual environment bytes,
modes, links, and exact upstream transport seams. An empty owned working
directory and HOME prevent ambient dotenv files from configuring the runner.
Unsupported platforms and unverified runtime contracts fail before traffic.

Raw JSON, CSV, JSONL, process outcomes, request observations, and normalized
metrics remain under `performance/`, outside disposable `work/`. Retention is
bounded to 512 files, 512 directories, 32 levels, and 64 MiB. Missing native
output, failed requests, malformed streams, and cancellation remain visible.
Native latency units and percentiles are preserved. AIPerf first-token and
first-visible-output timings remain separate. LIL chunk intervals are not
relabeled token intervals; token rates remain unknown when the native result
cannot distinguish server usage from chunk-count fallback. A completed
performance run measures transport behavior, not coding quality or kernel time.

## Retained SWE evidence and regrading

SWE runs preserve their exact plan, generated configuration, predictions,
trajectories, paired image identities, and official grader output under
`evidence/swe-canonical/`. Its manifest records relative paths, sizes, hashes,
and missing inputs. Retention is bounded to 512 regular files, 512 directories,
32 directory levels, and 64 MiB;
unsafe or oversized evidence leaves the work directory in place. Retained
destinations are immutable. A missing prediction or grader report remains
incomplete evidence even when the available files were retained successfully.

Explicit cleanup requires a terminal durable job and verified absence of its
owned worker process group. Queued, active, and unverified jobs cannot be
cleaned. Cancellation uses the same evidence retention after its existing
identity-checked worker stop. Shared caches and historical results remain
available. SWE cleanup also requires a matching canonical-manifest reference
in terminal job evidence; missing custody evidence preserves the work directory.

New SWE jobs may select `parameters.prompt_contract: "disclosed/v2"` to put
the configured step budget, completion-token cap, and patch submission
requirement in the agent's instructions. Omission selects `legacy/v1` and
preserves the historical generated configuration. Compare results only with
their recorded prompt contract; disclosure changes the evaluated workload.

To grade an existing run again, submit a new SWE job with the same owner,
profile, and endpoint identity and these parameters:

```json
{"operation": "regrade", "source_run_id": "original-swe-run"}
```

The source must be terminal, its terminal evidence must bind the canonical
manifest, and its retained plan, predictions, and pinned grader must verify.
Historical runs without that evidence cannot be regraded through this command.
Replay currently requires the matching installed profile and adapter bytes and
the original pinned grader cache location; drift fails closed before grading.
Regrading accepts no request or selection overrides. It
uses the existing submission, status, cancellation, and artifact commands,
writes a new score under the new run, and leaves the original evidence
unchanged. The endpoint fields remain provenance in the job specification;
regrade preflight and execution make no endpoint or agent calls and do not
load model credentials into the grader. Grader replay caps captured output at
1 MiB and rejects oversized or malformed reports; official grader errors never
count as graded or resolved instances.

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
