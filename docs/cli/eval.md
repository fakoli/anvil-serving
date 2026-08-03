# Eval

Use `anvil-serving eval` to validate and benchmark one explicit served model.

## Preflight

Run `eval preflight` before benchmarking a new serve or changed engine recipe.
It checks the explicit endpoint and served-model identifier without changing
the gateway's alias map.

## Benchmark

| Command | Purpose |
| --- | --- |
| `eval preflight` | Run functional compatibility checks against an endpoint. |
| `eval benchmark context` | Run durable context-degradation jobs on a registered worker. |
| `eval benchmark agentic` | Run deterministic agentic and recovery jobs. |
| `eval benchmark swe` | Run pinned mini-SWE-agent plus the official SWE-bench grader. |
| `eval benchmark capacity` | Measure throughput and latency. |
| `eval benchmark multimodal` | Run a hash-pinned image/video/mixed-media corpus. |
| `eval benchmark quality` | Run a repeatable quality suite with retained evidence. |
| `eval benchmark external` | Import and compare advisory external benchmark priors. |
| `eval usage` | Summarize local evaluation usage. |

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
