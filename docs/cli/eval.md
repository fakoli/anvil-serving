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
| `eval benchmark capacity` | Measure throughput and latency. |
| `eval benchmark quality` | Run a repeatable quality suite with retained evidence. |
| `eval benchmark external` | Import and compare advisory external benchmark priors. |
| `eval usage` | Summarize local evaluation usage. |

## Benchmark evidence

Run preflight before a benchmark and retain artifact identity, endpoint, served model, hardware,
engine, quantization, context, concurrency, failures, and caveats. Evaluation never changes a
direct alias or serve automatically.

## External benchmarks

External benchmark records are advisory priors. Keep their source and snapshot
provenance separate from locally recorded preflight and benchmark evidence.
