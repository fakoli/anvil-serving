# Evaluation & Evidence

Evaluation & Evidence answers two questions before you trust a model: does
this endpoint work, and how well does it perform? Preflight checks functional
compatibility. Benchmarks measure capacity, quality, context, agentic, coding,
and multimodal behavior. Every result is kept as evidence you can review and
compare later.

Evidence informs a decision; it never makes one. A passing run does not change
a serve, a route, or a promotion.

<!-- product-journey:evaluation-evidence -->

## Select your retained evidence

The review step reads local JSON artifacts; it does not download the published
benchmark archive. By default it looks for `docs/findings` relative to your
current directory, then `findings` in your operator configuration home. The
checkout's published archive is not bundled in a pip installation. If neither
location exists, the command reports that no evidence is retained yet and
returns an empty index. This is not a passing model evaluation.

To select another existing evidence directory:

```bash
anvil-serving eval benchmark evidence list --root /path/to/retained-results
```

An explicitly selected missing directory is an error. To read the project's
published findings instead, open [Benchmark results](../benchmarks/results.md).

## When to use it

- You are about to route real traffic to a new model or configuration.
- You want to compare two recipes under the same workload.
- You need to publish a result others can check against raw artifacts.

## Go deeper

- [Benchmark results](../benchmarks/results.md) — every published finding as one record, with charts
- [Methodology and evidence rules](../benchmarks/methodology.md)
- [Context, agentic, and SWE jobs](../benchmarks/context-agentic-swe.md)
- [Engine testing](../benchmarks/engine-testing.md)
- [External benchmarks](../EXTERNAL-BENCHMARKS.md) — using published results as priors
- [Finding publication format](../benchmarks/finding-format.md)
- Command reference: [eval](../cli/eval.md)
