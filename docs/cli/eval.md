# Eval

Use `anvil-serving eval` to validate and benchmark one explicit served model.

## Preflight

Run `eval preflight` before benchmarking a new serve or changed engine recipe.
It checks the explicit endpoint and served-model identifier without changing
the gateway's alias map.

For a tool-call gate after a genuinely long prompt, include `long-tools` and
set the nominal calibration target. The gate succeeds only when the endpoint
reports at least 100,000 actual prompt tokens and returns the expected
schema-valid call:

```powershell
anvil-serving eval preflight `
  --base-url http://127.0.0.1:30000/v1 `
  --model MODEL `
  --checks smoke,json,tools,long-tools `
  --long-tool-ctx 131072 `
  --confirm
```

## Routed client evaluation

Use `eval routed` after direct preflight when a candidate must be exercised
through an existing router alias and the real OpenClaw or Hermes harness. The
command does not install a router profile, change a client default, or promote
the candidate. Before invoking either client, it reconciles the local OpenClaw
and Pi catalogs from the router's authenticated capability contract and
restarts OpenClaw once for a newly observed router configuration hash. Hermes
receives the same context and output limits through the router's standard
`GET /v1/models` discovery response. The agent calls still use
invocation-scoped model overrides and write one fail-closed evidence artifact.

The router identity, readiness, tier configuration fingerprint, router
configuration hash, capacity context, and `/v1/models` discovery context are
checked before reconciliation. Eval then requires the sanitized reconciliation
receipt to name that exact router hash and alias context, re-reads the router,
and refuses client calls if the hash changed during the transaction. OpenClaw
must additionally report that exact context and the requested winner with
`fallbackUsed=false`. Hermes must record the expected provider and model in its
usage artifact. Each client must use its real shell tool to read a temporary
random nonce that is absent from the prompt, then continue with that exact tool
result. A matching answer from a fallback model or an answer that was merely
copied from the prompt is therefore a failure.

```powershell
anvil-serving eval routed `
  --base-url http://127.0.0.1:8000/v1 `
  --model llm.secondary `
  --api-key-env ANVIL_ROUTER_TOKEN `
  --expected-served-model SERVED_MODEL `
  --expected-config-fingerprint CONFIG_ID `
  --expected-router-config-sha256 ROUTER_CONFIG_SHA256 `
  --min-context-tokens 250000 `
  --clients openclaw,hermes `
  --output artifacts/routed-client-eval.json `
  --confirm
```

Use `--dry-run` to inspect the client overrides and credential-variable name
without sending a router request, starting a client turn, or writing evidence.
Run this command on the host that owns the selected client harnesses. The
default Hermes selector is `anvil`; its normalized usage-provider identity is
`custom` and can be changed explicitly with `--hermes-expected-provider`.
`--no-harness-sync` is an explicit diagnostic escape hatch: the router and
real-client gates still run, but eval does not repair a stale OpenClaw/Pi
catalog. A normal qualification should leave reconciliation enabled because
the resulting catalog change makes local client limits truthful to the active
router; it is an exposure synchronization, not a model promotion.

When the operator shell is on another host, dispatch the same command to the
declared gateway owner through its controller:

```bash
anvil-serving eval routed \
  --target host:client-gateway \
  --transport controller \
  --base-url http://100.64.0.10:8000/v1 \
  --model llm.primary \
  --api-key-env ANVIL_ROUTER_TOKEN \
  --expected-served-model SERVED_MODEL \
  --expected-config-fingerprint CONFIG_ID \
  --expected-router-config-sha256 ROUTER_CONFIG_SHA256 \
  --min-context-tokens 250000 \
  --clients openclaw,hermes \
  --confirm
```

Controller dispatch sends only the credential environment-variable name. The
owning controller must expose the typed `routed_eval` operation and its
topology transport must allow `eval-routed`. Live evidence defaults to the
owning host's private `~/.anvil-serving/evidence/routed-eval/` directory;
remote `--output` values are confined to that tree. This transport performs
the same router-hash, catalog, real-client, and no-fallback gates as a local
run; it is not a reduced remote smoke.

## Benchmark

| Command | Purpose |
| --- | --- |
| `eval preflight` | Run functional compatibility checks against an endpoint. |
| `eval routed` | Reconcile router-derived client limits, then verify one alias through real OpenClaw and Hermes turns. |
| `eval benchmark context` | Run durable context-degradation jobs on a registered worker. |
| `eval benchmark agentic` | Run deterministic agentic and recovery jobs. |
| `eval benchmark swe` | Run pinned mini-SWE-agent plus the official SWE-bench grader. |
| `eval benchmark capacity` | Measure throughput and latency. |
| `eval benchmark multimodal` | Run a hash-pinned image/video/mixed-media corpus. |
| `eval benchmark quality` | Run a repeatable quality suite with retained evidence. |
| `eval benchmark external` | Import and compare advisory external benchmark priors. |
| `eval usage` | Summarize local evaluation usage. |

The multimodal runner admits at most four images and one video per corpus case
by default. When qualifying a recipe with higher engine-side media limits,
raise only the recorded corpus ceilings with `--max-images-per-request N`
(maximum 64) and `--max-videos-per-request N` (maximum 16). Both selected
ceilings are retained in the evidence artifact; they do not change the serving
recipe or router policy.

Capacity runs use a deterministic context plan by default. Keep the seed,
request count, concurrency, context policy, completion cap, and endpoint recipe
identical when comparing candidates:

```powershell
anvil-serving eval benchmark capacity `
  --base-url http://127.0.0.1:30002/v1 `
  --model MODEL `
  --engine vllm `
  --gpu dark-heavy `
  --requests 60 `
  --concurrency 20 `
  --seed 0 `
  --output artifacts/capacity.json `
  --confirm
```

The capacity artifact records the requested context distribution, the sampling
seed, engine/hardware target, completed and failed requests, sanitized failure
classes, and how output tokens were counted. Measurement protocol `capacity-v3`
uses exact `usage.prompt_tokens` and `usage.completion_tokens` to retain
per-request and aggregate TTFT, effective prefill rate, generation duration,
decode rate, mean inter-token latency, E2E latency, and token counts.
Effective prefill includes queueing, scheduling, prompt processing, and
first-token work; it is not a kernel-only measurement. When exact usage is
unavailable, token-derived rates are null and content-chunk rate is retained
only as a diagnostic.

Quality runs require an explicit built-in suite or an externally authored suite
file plus stable candidate and configuration identities:

```powershell
anvil-serving eval benchmark quality `
  --base-url http://127.0.0.1:30002/v1 `
  --model MODEL `
  --candidate-id MODEL `
  --config-id vllm-heavy-v1 `
  --suite-file suites/quality.json `
  --output artifacts/quality.json `
  --confirm
```

Use `--dry-run` to resolve and validate either workload without probing the
endpoint or writing an artifact. Flags take precedence over a referenced serves
manifest, which takes precedence over the bundled reference manifest. A direct
target requires both `--base-url` and `--model`.

The context, agentic, and SWE family root is the plan/dry-run surface; each
family also provides explicit `prepare`, `preflight`, `submit`, `status`,
`logs`, `cancel`, and `artifact` operations. Submission is
durable and launches an isolated worker process; `--detach` returns after that
launch. Use the controller transport for a registered remote worker rather
than SSH. The complete specification, profile costs, evidence semantics, and
examples are in [Context, agentic, and SWE benchmark jobs](../benchmarks/context-agentic-swe.md).

## Benchmark evidence

Run preflight before a benchmark and retain artifact identity, endpoint, served model, hardware,
engine, quantization, context, concurrency, failures, and caveats. Evaluation never changes a
direct alias or serve automatically.

A nonzero exit means the requested workload did not complete or satisfy its
gate. A stream without visible content is a failed request; it is not rewritten
as a successful zero-TTFT completion.

## External benchmarks

External benchmark records are advisory priors. Keep their source and snapshot
provenance separate from locally recorded preflight and benchmark evidence.
