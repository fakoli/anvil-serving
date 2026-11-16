# Evaluation & Evidence

Evaluation & Evidence answers two questions before you trust a model: does
this endpoint work, and how well does it perform? Preflight checks functional
compatibility. Benchmarks measure capacity, quality, context, agentic, coding,
and multimodal behavior. Every result is kept as evidence you can review and
compare later.

Evidence informs a decision; it never makes one. A passing run does not change
a serve, a route, or a promotion.

<!-- product-journey:evaluation-evidence -->

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
