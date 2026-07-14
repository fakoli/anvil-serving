# Evaluation & benchmarks

[CLI overview](../CLI.md) · [Models & recipes](models.md) · [Router](router.md)

The `eval` family provides independent quality gates, benchmark execution, retained
evidence, and external-evidence workflows. Evaluation records evidence; it never
silently promotes a model or router policy.

## Commands

| Command | Purpose |
| --- | --- |
| `eval usage` | Analyze recorded usage. |
| `eval preflight` | Gate an endpoint before promotion. |
| `eval planning` | Run planning evaluation. |
| `eval bootstrap` | Bootstrap a quality profile. |
| `eval calibrate` | Produce a reviewable calibrated profile. |
| `eval benchmark run` | Benchmark an endpoint. |
| `eval benchmark evidence list` | List retained local artifacts. |
| `eval benchmark evidence show` | Show a normalized artifact summary. |
| `eval benchmark evidence compare` | Compare artifacts and flag workload mismatches. |
| `eval benchmark external ...` | Import, normalize, report, export, and compare external evidence. |
| `eval benchmark external notebook ...` | Record and render model-bakeoff notebook runs. |

## Preflight

Preflight is the live quality gate for a candidate endpoint:

```bash
anvil-serving eval preflight --base-url http://127.0.0.1:30000/v1 --model MODEL --confirm
```

The confirmation acknowledges that the command calls a live model endpoint. The gate
is independent from the model being evaluated; do not use a candidate to validate its
own correctness.

## Benchmark

```bash
anvil-serving eval benchmark run --help
```

Record the endpoint, served model name, host topology, hardware, engine, quantization,
context, concurrency, and failures with each run. A benchmark may emit a candidate
recipe for review, but does not promote it.

## Benchmark evidence

```bash
anvil-serving eval benchmark evidence list
anvil-serving eval benchmark evidence show ARTIFACT
anvil-serving eval benchmark evidence compare BASELINE CANDIDATE
```

Comparison flags incompatible workloads instead of presenting unlike runs as a valid
head-to-head result. Raw artifacts remain the evidence source; narrative findings link
to them rather than duplicating their contents.

## External benchmarks

| Command | Purpose |
| --- | --- |
| `external init` | Initialize external-evidence storage. |
| `external sources` | List configured sources. |
| `external fetch` | Fetch and import evidence. |
| `external import` | Import saved evidence. |
| `external list` | List normalized evidence. |
| `external report` | Render a report. |
| `external export` | Export normalized evidence. |
| `external compare` | Compare local evidence. |

```bash
anvil-serving eval benchmark external sources
anvil-serving eval benchmark external list
anvil-serving eval benchmark external report
```

External evidence must retain source URL, observation or publication date, age class,
evidence type, and hardware/engine relevance. Older community reports are recipe leads,
not promotion evidence, unless current official sources or local runs corroborate them.

## Bakeoff notebook

```bash
anvil-serving eval benchmark external notebook add --help
anvil-serving eval benchmark external notebook list
anvil-serving eval benchmark external notebook render
```

Use the notebook commands for a durable comparison record across multiple bakeoff runs.

## Profiles and planning

```bash
anvil-serving eval usage --help
anvil-serving eval planning --help
anvil-serving eval bootstrap --help
anvil-serving eval calibrate --help
```

Bootstrap and calibration produce reviewable profile data. Calibration is evidence for
a human decision, not automatic policy promotion.

## Related references

- [Benchmarks](../BENCHMARKS.md)
- [Published findings](../findings/README.md)
- [Model serves](serves.md)
- [Models & recipes](models.md)
