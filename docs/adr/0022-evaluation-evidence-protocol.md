# ADR-0022 — Evaluation evidence protocol

- **Status:** **Accepted** (2026-07-14)
- **Date:** 2026-07-14
- **Relates to:** [ADR-0009](0009-profile-write-back-loop.md) (quality-profile write-back) ·
  [ADR-0021](0021-cli-interaction-contract.md) (CLI interaction contract) ·
  `anvil_serving/preflight.py`, `anvil_serving/benchmark.py`,
  `anvil_serving/benchmark_evidence.py`

## Context

The earlier benchmark command combined capacity replay and quality scoring under
`eval benchmark run`. Its external-suite path made one attempt per item, treated short marker
checks as ranking evidence, carried `context_bucket` as metadata without supplying that context,
and did not retain full visible output, finish reason, or reasoning-channel evidence. Reasoning
models could spend the entire completion cap before producing visible text, which made budget
exhaustion look like an ordinary wrong answer. Qwen-style template controls were also applied to
GPT-OSS even though GPT-OSS uses reasoning-effort controls.

The 2026-07-12 Qwen 1/5, Nemotron 0/5, GPT-OSS external-suite 0/5, and original built-in GPT-OSS
intelligence result are therefore invalid for cross-model ranking or promotion. The deterministic
checks did evaluate the visible text they received; the surrounding protocol did not create
comparable evidence.

## Considered options

1. **Keep the protocol and document larger token caps.** Rejected. A larger undifferentiated cap
   does not prove reasoning-control parity, repeatability, or validator strength.
2. **Use only an LLM judge.** Rejected. It adds cost and non-determinism, can create self-grading,
   and is unnecessary for exact-choice, typed-structure, and tool-contract checks.
3. **Use only deterministic markers.** Rejected for ranking. Loose markers can validate protocol
   shape, but they do not establish semantic quality.
4. **Separate capacity from quality and require protocol-declared evidence strength.** Chosen.

## Decision

### 1. Capacity and quality are separate commands

`eval benchmark capacity` measures latency, throughput, context, and cache behavior.
`eval benchmark quality` runs repeated correctness suites. The old ambiguous `run` form is a
tombstone and never guesses the operator's intent.

### 2. Model-family controls fail closed where incompatibility is known

Qwen-family models use chat-template thinking controls. GPT-OSS uses supported
`reasoning_effort` values `low`, `medium`, or `high`. Known incompatible controls are rejected
before network access; unknown families remain operator-controlled. A requested control is not
comparison proof. Ranking evidence records a `verified` or `supported` control state plus a stable
evidence reference.

### 3. Visible-answer intent and reasoning headroom are explicit

Every quality attempt resolves `visible_answer_tokens` and `reasoning_headroom_tokens`. Their sum
is the API completion cap; the protocol does not claim server-side partitioning. Evidence retains
both values, the combined cap, full visible output, finish reason, reasoning-field presence and
size, and a failure classification. Length or unexpected finishes cannot pass merely because a
marker appeared.

### 4. Quality is repeated and resource-bounded

Quality checks default to three attempts and a 100% minimum pass rate. Tool and session checks are
repeated as well as text checks. Suites are bounded to 100 evals, 20 repetitions per eval, 500
attempts, 65,536 completion tokens per attempt, and 2,000,000 requested quality tokens per run.
Artifacts are atomically written even when the gate fails, then the command returns failure.

### 5. Suite evidence use and validator strength are declared

External suites declare `evidence_use` as `diagnostic` or `ranking`. Ranking requires an
executable validator: `exact_choice` is one full-response anchored regular expression per eval,
or `typed_structure` is a declared `expect_tool` function-call shape. Simple substring or
deterministic-marker checks are not ranking evidence. `independent_judge` remains a reserved
protocol value and fails closed until the runner can execute and record an independent judge.
Malformed or vacuous assertions fail before a request. The accepted regular-expression subset
excludes constructs that create ambiguity or resource risk. `context_bucket` is rejected until
the runner can supply it faithfully.

### 6. Comparisons prove compatibility before reporting a ranking

Comparison-grade quality artifacts require protocol v3, at least three repetitions, ranking-grade
suites, immutable suite/source hashes, endpoint/model and engine/GPU provenance, and verified or
supported reasoning-control evidence. Mismatched or incomplete artifacts produce an incompatible
comparison instead of a misleading leaderboard row.

### 7. Local and controller preflight share one contract

The MCP/controller preflight schema matches local bounds, model-family controls, finish policy,
and explicit safe dry-run behavior. Remote operation deadlines cover each selected per-request
check. Operator manifest paths remain local-only because a path on the command host is not safely
equivalent to a path on the resource owner.

## Consequences

- Existing protocol-v2 artifacts remain historical evidence for their exact recorded runs, but
  protocol v3 is required for new comparison-grade quality claims.
- The invalid 2026-07-12 cross-model scores stay published with a superseded-protocol warning; they
  are not rewritten as if they had been collected under v3.
- Capacity numbers and quality scores are no longer conflated by one command or artifact type.
- More metadata and repetitions make quality runs slower, but failures are diagnosable and
  comparisons fail closed instead of manufacturing certainty.
- Profile promotion remains a separate human gate; evaluation never changes routing trust.
