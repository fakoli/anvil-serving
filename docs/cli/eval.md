# Evaluation & benchmarks

[CLI overview](../CLI.md) · [Models & recipes](models.md) · [Model serves](serves.md)

The `eval` family answers three different questions: is an endpoint correct enough to test,
how much traffic can it serve, and does it produce repeatably correct answers? Those questions
have separate commands and separate evidence. Evaluation can create candidate artifacts; it
never promotes a model or changes router policy.

## Choose the workflow

| Goal | Command | Result |
| --- | --- | --- |
| Understand real harness usage | `eval usage` | Usage and role-sizing JSON. |
| Gate a candidate endpoint | `eval preflight` | Functional pass/fail evidence. |
| Measure latency and throughput | `eval benchmark capacity` | Capacity artifact. |
| Measure repeated answer quality | `eval benchmark quality` | Protocol-v3 quality artifact. |
| Inspect or compare local artifacts | `eval benchmark evidence ...` | Normalized summaries and compatibility checks. |
| Build a profile from retained fixtures | `eval bootstrap` | Reviewable candidate profile. |
| Measure tiers with an independent judge | `eval calibrate` | Reviewable calibrated profile. |
| Use external results as priors | `eval benchmark external ...` | Dated advisory evidence. |

The usual candidate path is:

```bash
anvil-serving eval preflight --tier heavy --checks smoke,json,needle,tools --dry-run
anvil-serving eval preflight --tier heavy --checks smoke,json,needle,tools --output preflight.json --confirm
anvil-serving eval benchmark capacity --tier heavy --requests 10 --concurrency 1 --output capacity.json --confirm
anvil-serving eval benchmark quality --tier heavy --suite-file suite.json --candidate-id MODEL --config-id heavy-v1 --output quality.json --confirm
```

Use `--dry-run` first when assembling a new command. It validates the target, workload,
budgets, and output without sending model requests or writing artifacts. In an interactive
terminal, omitting `--confirm` prompts before a live run; automation should pass `--confirm`
explicitly.

## Target an endpoint

Prefer a named serve when it exists:

```bash
anvil-serving eval preflight --tier heavy --dry-run
anvil-serving eval benchmark capacity --tier heavy --dry-run
```

`--tier` resolves the endpoint, served model, engine, GPU role, and context limit from the
serves manifest. Pass `--manifest PATH` when the intended manifest is not the selected project,
operator-home, or packaged reference manifest.

For an ad hoc endpoint, provide both values explicitly:

```bash
anvil-serving eval preflight --base-url http://127.0.0.1:30002/v1 --model MODEL --dry-run
anvil-serving eval benchmark capacity --base-url http://127.0.0.1:30002/v1 --model MODEL --engine vllm --gpu dark-heavy --dry-run
```

Use `127.0.0.1`, not `localhost`, for same-host endpoints. Loopback is relative to the command
host. In a split-host topology, target the resource owner through the controller rather than
assuming that the operator machine owns the endpoint. `eval preflight` has controller parity;
the capacity and quality runners remain local to the endpoint owner.

## Preflight

Preflight is a bounded functional gate that should pass before performance or quality work.

| Check | What it proves |
| --- | --- |
| `smoke` | The model returns a usable short coding answer. |
| `json` | Structured JSON output is usable. |
| `needle` | The requested long-context payload is recovered correctly. |
| `tools` | A shared-prefix batch produces valid tool calls. |

Select checks with `--checks`; the default is all four. `--needle-ctx` accepts up to one million
tokens, `--tool-batch` accepts up to 128 calls, and `--timeout-seconds` is a per-request deadline.
Remote execution expands the operation deadline to cover every selected check.

```bash
anvil-serving eval preflight --tier heavy \
  --checks smoke,json,needle,tools \
  --needle-ctx 128000 \
  --tool-batch 20 \
  --visible-answer-tokens 256 \
  --reasoning-headroom-tokens 4096 \
  --reasoning-evidence required \
  --output preflight-heavy.json \
  --confirm
```

The artifact records full visible output, finish reason, reasoning-channel metadata, the
requested budgets, and the accepted finish-reason policy. A matching marker does not rescue a
truncated or unexpected completion.

## Reasoning controls and budgets

Use the control the served model family actually supports:

| Model behavior | Use | Do not use |
| --- | --- | --- |
| Qwen-style chat-template thinking | `--thinking-mode enabled|disabled` | `--reasoning-effort` |
| GPT-OSS reasoning effort | `--reasoning-effort low|medium|high` | `--no-thinking` or Qwen template controls |
| Endpoint exposes no control | `--thinking-mode unsupported` | Claiming that thinking was disabled |

Known incompatible combinations fail before requests are sent. Unknown model families remain
operator-controlled rather than being guessed into a protocol.

`--visible-answer-tokens` records the answer allocation and
`--reasoning-headroom-tokens` records additional reasoning allowance. The endpoint receives their
sum as one completion cap; these fields document intent and do not claim hard server-side channel
partitioning. Use `--reasoning-evidence required|forbidden` when the presence or absence of the
reasoning channel is part of the gate.

For comparison-grade quality evidence, record a verified or supported control with
`--control-status` and `--control-evidence`. A merely requested control is retained as
`requested_unverified` and cannot support a ranking claim.

## Benchmark

Choose `capacity` when measuring serving behavior and `quality` when evaluating model answers.
Both workflows retain bounded evidence artifacts, but their results are intentionally not mixed.

### Capacity benchmark

`eval benchmark capacity` measures endpoint behavior, not answer quality. It records latency,
aggregate throughput, request completion, context, and optional shared-prefix burst behavior.

```bash
anvil-serving eval benchmark capacity --tier heavy \
  --requests 60 \
  --concurrency 5 \
  --ctx-tokens 8192 \
  --max-tokens 256 \
  --output heavy-capacity.json \
  --confirm
```

Set `--ctx-tokens 0` to sample the measured usage distribution. `--max-model-len` can override
endpoint discovery; the runner clamps requests below that limit with `--margin`. `--burst` adds a
bounded shared-prefix phase. Short mixed-prompt aggregate throughput is not a controlled decode
rate, so retain established long-generation results with their exact recipe when that is the
metric being compared.

### Quality benchmark

`eval benchmark quality` runs built-in or external suites through evaluation protocol v3. It
defaults to three attempts per check and requires every attempt to pass. Change that policy with
`--eval-repetitions` and `--eval-min-pass-rate`, and keep it identical across candidates.

```bash
anvil-serving eval benchmark quality --tier heavy \
  --suite-file suite.json \
  --candidate-id MODEL \
  --config-id heavy-v1 \
  --visible-answer-tokens 256 \
  --reasoning-headroom-tokens 4096 \
  --control-status verified \
  --control-evidence evidence/reasoning-control.json \
  --source-recipe configs/serve-recipes.toml \
  --output heavy-quality.json \
  --confirm
```

Built-in suites are selected with `--suite chat,context,tool,session,intelligence,voice`.
`--suite-file` alone runs only the external suite; combine both flags deliberately when one
artifact should include both.

### External suite contract

A suite is explicit about how its result may be used:

```json
{
  "suite": "planning-regression",
  "evidence_use": "ranking",
  "validator_strength": "exact_choice",
  "evals": [
    {
      "id": "case-1",
      "prompt": "Return only A, B, C, or D.",
      "visible_answer_tokens": 16,
      "reasoning_headroom_tokens": 1024,
      "checks": [{"name": "choice", "matches_regex": "^B$"}]
    }
  ]
}
```

`evidence_use` is `diagnostic` or `ranking`. Ranking suites must use an executable strong
validator: `exact_choice` requires one `matches_regex` check anchored with `^` and `$` for every
eval, while `typed_structure` requires `expect_tool`. Loose substring or marker checks remain
diagnostic. `independent_judge` is reserved but rejected until the runner can record and verify
an independent judge invocation. Every eval needs a non-empty `prompt` or `messages` plus a real
text or tool assertion. `context_bucket` is rejected because the runner cannot yet provide that
context faithfully; include the bounded context in `messages` instead.

Protocol v3 rejects malformed, vacuous, ambiguous, or resource-exhausting suites before network
access. Current bounds are 100 evals, 20 repetitions per eval, 500 aggregate attempts, 65,536
completion tokens per attempt, and 2,000,000 requested quality tokens per run. Its regex language
is a conservative deterministic subset, not arbitrary Python regular expressions.

Each attempt retains the full visible answer, finish reason, reasoning field and size, requested
budgets, validation result, and a failure classification. Tool and session checks are repeated too.
An artifact can be diagnostic even when the command exits nonzero; the runner writes the evidence
first so failed gates remain inspectable.

## Benchmark evidence

```bash
anvil-serving eval benchmark evidence list
anvil-serving eval benchmark evidence show heavy-quality.json
anvil-serving eval benchmark evidence compare baseline.json candidate.json
```

Comparison fails closed on workload mismatches. Quality ranking requires protocol v3, at least
three repetitions, ranking-grade suite declarations, immutable suite/source hashes, model and
engine/GPU provenance, and verified or supported reasoning-control evidence. Raw artifacts remain
the evidence source; narrative findings link to them instead of copying their contents.

## Usage, bootstrap, and calibration

### Usage

```bash
anvil-serving eval usage --logs-dir ~/.claude/projects --out-dir .anvil/usage --dry-run
anvil-serving eval usage --logs-dir ~/.claude/projects --out-dir .anvil/usage \
  --analysis-timeout 300 --confirm
```

Usage writes its paired JSON outputs only after both child analyses succeed. A failure preserves
the previous pair, including a failure during the second replacement. Each analyzer has a bounded
deadline, and recursive log discovery rejects symlink traversal, oversized files, oversized lines,
and excessive file, directory, or aggregate-byte counts.

### Bootstrap

```bash
anvil-serving eval bootstrap --eval-data ./eval-data --out ./profile.json --dry-run
anvil-serving eval bootstrap --eval-data ./eval-data --out ./profile.json --confirm
```

`--eval-data` and `--out` are required. Existing output is refused unless `--overwrite` is
selected; replacement creates a numbered backup.

### Calibration

```bash
anvil-serving eval calibrate --config router.toml \
  --eval-data ./eval-data \
  --out ./candidate-profile.json \
  --endpoint fast-local=http://127.0.0.1:30001/v1 \
  --endpoint heavy-local=http://127.0.0.1:30002/v1 \
  --dry-run
```

Every measured local tier needs an exact `TIER=URL` confirmation matching the selected router
configuration. Calibration uses an independent judge and writes only a candidate profile. It does
not promote the profile or change routing trust.

## External benchmarks

```bash
anvil-serving eval benchmark external sources
anvil-serving eval benchmark external list
anvil-serving eval benchmark external report
anvil-serving eval benchmark external compare --help
anvil-serving eval benchmark external notebook render --help
```

External evidence is advisory. Retain the source URL, publication or observation date, age class,
evidence type, hardware/engine relevance, and decision impact. Community posts are recipe leads,
not promotion evidence, unless current official sources or local runs corroborate them. See the
[external benchmark reference](../EXTERNAL-BENCHMARKS.md) for storage and import details.

## Migration

| Removed command | Replacement |
| --- | --- |
| `eval benchmark run` | `eval benchmark capacity` or `eval benchmark quality` |
| `eval planning` | `eval benchmark quality --suite-file PATH` |

Removed forms fail with the exact replacement instead of silently choosing a workload.

## Related references

- [ADR-0022: evaluation evidence protocol](../adr/0022-evaluation-evidence-protocol.md)
- [Benchmark methodology](../benchmarks/methodology.md)
- [Benchmark results](../BENCHMARKS.md)
- [Published findings](../findings/README.md)
- [Operator playbooks](../OPERATOR-PLAYBOOKS.md)
