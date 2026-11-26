# Evidence index — 2026-11-26 ERNIE and Inkling runtime evaluation

This directory retains public-safe request receipts, aggregate measurements,
and configuration class only. It excludes prompts, completions, reasoning
traces, task bodies, endpoints, host identities, local paths, credentials,
hardware identifiers, and private deployment assignments. The original native
envelopes are withheld because they contain those protected fields.

The artifact manifest is closed. Native raw request/capacity envelopes are
withheld and the `raw-run-evidence` role is therefore explicitly missing. The
retained rows below are derivative, public-safe attachments to the decision
summary; they support bounded interpretation but are not native raw evidence.

| Artifact | Purpose | Boundary |
|---|---|---|
| `summary.json` | Current non-decision and bounded aggregate observations | No qualification or promotion conclusion |
| `raw-request-receipts.json` | 180 anonymous row-level request receipts | Request/event hashes, timings, token counts, and outcomes only; original native envelopes withheld |
| `c8-c16-capacity-receipts.json` | Candidate-only C8/C16 timing projection | Public-safe aggregate cells bound to private source hashes; per-cell cache counters retained without a cold/warm inference |
| `quality-scope-receipts.json` | Scorer sensitivity and bounded agentic/Aider/SWE scope | Public-safe counts and source hashes; SWE is incomplete |
| `long-context-receipts.json` | Bounded C1 long-context timing and completion projection | Public-safe actual-token and timing rows bound to source hashes |
| `ernie-fixed-60-summary.json` | Derived fixed-set paired aggregate | No prompt/answer text; not a quality tie or speed ranking |
| `ernie-long-context-c1.json` | C1 long-context observations, including one 999,990-token lookup | Single-target evidence; not broad reliability |
| `ernie-c16-summary.json` | Natural and length-controlled C16 aggregates | Descriptive cells, not a general capacity or speed claim |
| `inkling-original-summary.json` | Original mixed-precision C1/24K fixed-set aggregates | 16,384-output result is bounded; no matched Swift control |
| `inkling-q8-summary.json` | Q8 bounded short protocol aggregate | Q8 quality run ended OOM; no Q8 fixed-set result |
| `restoration.json` | Sanitized retained-primary restoration receipt | Continuity evidence, not a candidate decision |
| `identity.json` | Public runtime and hardware class | No image digest, host, route, or private variant data |
| `workload-manifest.json` | Sanitized workload controls | Does not disclose the fixed corpus |
| `coverage-and-gaps.md` | Measured scope and missing gates | Explicitly prevents over-interpretation |

`raw-request-receipts.json` binds each arm to the SHA-256 of its private input
envelope and preserves the evaluated request controls without preserving
request content. It is a public-safe projection, not a native benchmark
envelope. The source registry records the observation date and decision impact.
No external benchmark or model-card claim is used as local evidence.
