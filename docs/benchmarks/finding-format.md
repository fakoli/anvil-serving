# Publish benchmark findings without losing the evidence

A benchmark finding should be easy to understand at a glance without becoming
less precise. Anvil Serving uses a **result card** in the dated finding and a
separate **publication summary** beside its raw artifacts. Both are derivative
views: the dated finding and retained artifacts remain the evidence.

## Publication-ready finding v1

Use this format for every local `functional`, `capacity`, or `quality` result.
Research-only, release/readiness-only, and failed-load findings can use the
result card; a publication summary is optional unless a compact public
communication is required.

### Result card

Place `<!-- benchmark-result-card/v1 -->` near the top of the finding, after
the date, scope, and decision. Keep the card compact enough to screenshot. It
contains:

1. one sentence naming the bounded local outcome;
2. a two-column setup table with exact model, measured hardware, runtime,
   managed recipe, measurement path, warm/cold state, context, concurrency,
   evidence labels, and decision;
3. three to six headline measurements, each with conditions;
4. why the result matters;
5. the most important caveat or retained failure; and
6. links to the evidence manifest and publication summary.

The full narrative follows the card and retains exact identity, method,
distributions or ranges, failures, next experiments, raw links, and the
promotion boundary. Do not force the full report into the screenshot.

### Publication summary

Put `<!-- benchmark-publication-summary/v1 -->` in a companion Markdown file
beside the raw artifacts. Include:

- canonical facts that posting copy must not contradict;
- an X/short-post variant;
- a Reddit title and body;
- accessible alt text for a result-card screenshot; and
- a claim ledger mapping each public claim to a finding section and, when
  applicable, its raw artifact.

Use the repository templates at
`skills/anvil-serving-benchmark-docs/templates/finding.md` and
`skills/anvil-serving-benchmark-docs/templates/publication-summary.md`.

For an existing finding, a complete retained artifact is the preferred input.
Do not restart a model or rerun a benchmark solely to create publication copy.
The artifact must still provide the immutable model/runtime identity, recipe,
hardware, configuration, measurement path, workload, sample statistics,
failures, decision, and raw links. A missing field remains a visible evidence
gap.

## Patterns adopted from peer repositories

Reviewed 2026-08-27. These projects solve different benchmark problems, so
their numbers are not comparison data for Anvil Serving. Their reporting
patterns inform this format:

| Project | Useful communication pattern | Anvil Serving adoption |
|---|---|---|
| [vLLM serving benchmarks](https://github.com/vllm-project/vllm/blob/main/docs/benchmarking/cli.md) | Names the endpoint, dataset, prompt count, concurrency, sampling, detailed-save mode, latency percentiles, and JSON output. | Put traffic shape and sample statistic beside every headline metric; link the request-level artifact. |
| [SGLang benchmark guide](https://github.com/sgl-project/sglang/blob/main/docs/developer_guide/benchmark_and_profiling.md) | Separates online serving, single-batch server, offline engine, and kernel-level tools because they measure different layers. | Add a measurement-path field and never present client-observed endpoint throughput as a kernel rate. |
| [llama.cpp `llama-bench`](https://github.com/ggml-org/llama.cpp/blob/master/tools/llama-bench/README.md) | Emits Markdown for people plus CSV, JSON, JSONL, or SQL with build commit, CPU/GPU, backend, model, batching, KV, and statistical spread. | Pair the readable result card with machine-readable evidence and an exact configuration fingerprint. |
| [MLPerf submission structure](https://github.com/mlcommons/inference/blob/master/tools/submission/submission_structure.md) | Separates system description, scenario configuration, measurements, performance, and accuracy artifacts. | Keep setup, method, capacity/performance, quality, and evidence provenance distinct even when the result card is compact. |
| [lm-evaluation-harness interface](https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/interface.md) and [Hugging Face evaluation results](https://huggingface.co/docs/hub/eval-results) | Retains exact model/task/generation arguments and optional per-sample logs; structured scores can carry source attribution and notes. | Preserve failed samples and scoring details, then map each public claim to its source in the claim ledger. |

The main lesson is not to make the report longer. It is to expose a compact
configuration fingerprint, label the measurement layer, show distribution or
sample size, and keep one click from every headline claim to raw evidence.

## Platform copy limits

X documents a 280-character standard post limit. This project targets no more
than **260 literal characters**, including the canonical evidence URL, leaving
room for small edits; 280 is the hard validation limit. X Premium longer posts
are optional and do not change this default. Recount immediately before
posting because platform behavior can change. See the official
[X character-counting documentation](https://docs.x.com/fundamentals/counting-characters)
and [posting help](https://help.x.com/en/using-x/how-to-post).

Reddit communities can configure their own post requirements, including title
length. This project targets a **120-character title** and requires checking the
target community at posting time. See Reddit's official
[community-settings documentation](https://support.reddithelp.com/hc/en-us/articles/15484546290068-Community-settings).

## Claim controls

- Start with `Local result` or otherwise identify the measured hardware.
- Keep model revision, engine/quantization, context, concurrency, and reasoning
  mode close to the number when they affect interpretation.
- Name the statistic and sample: p50, range, attempts, corpus, or single probe.
- Name the measurement path: direct or routed online endpoint, offline engine,
  or kernel microbenchmark, including warm/cold state when it changes the
  interpretation.
- Distinguish effective prefill (client-observed prompt tokens divided by
  time-to-first-output) from an engine kernel prefill rate.
- State the most important miss, failure, or untested boundary in the post or
  immediately attached result card.
- Do not say `best`, `fastest`, `strongest`, or `production-ready` without an
  exact, evidence-backed comparison universe, matched baseline, delta
  calculation, and decision boundary.
- Do not crop failures, conditions, axes, or units out of a screenshot.
- Do not include credentials, private endpoints, personal paths, prompts,
  response bodies, or other operator-private data.

## Publication checklist

1. Re-run the structural tests and link checks against the committed finding.
2. Copy from the checked-in publication summary rather than rewriting numbers
   by hand.
3. Recount title/post characters and check the destination's current rules.
4. Attach a readable result-card screenshot with the checked-in alt text.
5. Link the canonical finding, not an expiring artifact or mutable dashboard.
6. Answer discussion questions from the full evidence, preserving failed cases
   and the original comparison boundary.

A compact publication is a presentation layer, not a new benchmark result. If
discussion reveals an error, publish a linked erratum or new finding instead of
silently changing the historical measurement.
