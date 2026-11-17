# Independent interim quality review

Reviewed 2026-11-02, read-only. Scope: the frozen campaign `PLAN.md`, the
DFlash finalist strict artifact and telemetry summary, completed context,
agentic, multimodal, stability, and C2-overlap validation artifacts. The coding
pilot was still running; no result is inferred. No model request or lifecycle
action was performed.

## Verdict: no campaign promotion; a separately declared limited practical role is possible later

The DFlash finalist strict cell is a real scoped deterministic-gate failure.
It precludes promotion under the frozen plan as written, which requires *all*
scoped deterministic gates to pass. Passing transport, identity, telemetry,
counter reconciliation, OOM, and capture-completeness checks do not turn a
strict output/control failure into a pass. A later successful coding pilot does
not change that conclusion.

This does not establish that the model is unusable in every role. After the
coding floor and remaining parent-owned resource/performance gates are met, a
human may make a new, explicitly limited practical-role decision for workloads
that neither require exact word count nor a byte-position response canary and
that tolerate visible reasoning/content separation. That is not a retroactive
change to this campaign's promotion gate, must be named as a restricted
non-strict role, and must exclude strict-format, exact-prefix, schema-sensitive,
or tool/action workflows that depend on this contract. Do not label it
"promotion" or claim the campaign was fully accepted.

## Strict evidence

The sealed 32K/C2 DFlash cell delivered six responses with exact counter
reconciliation, no OOM event, telemetry cadence coverage, and complete raw
captures. All six nevertheless failed the strict 128-word/canary oracle:

| Request | Observed code words | Extra words | Own marker | Foreign markers | Marker at start |
| --- | ---: | ---: | ---: | ---: | --- |
| 0 | 169 | 1 | 1 | 0 | false |
| 1 | 166 | 1 | 1 | 0 | false |
| 2 | 138 | 1 | 1 | 0 | false |
| 3 | 132 | 1 | 1 | 0 | false |
| 4 | 111 | 1 | 1 | 0 | false |
| 5 | 144 | 1 | 1 | 0 | false |

Thus this is not cross-request leakage: each response contained its own marker
once and no foreign marker. It is also not a normalization-only discrepancy:
all six violate the exact start condition and the requested word count varies
from 111 through 169. Preserve this result and the comparison-arm result when
available. The fact that the baseline family also misses the strict contract is
comparative context only; two failures do not satisfy the gate. Do not weaken
the validator or create a retry-to-pass sequence.

## Completed evidence that remains valid but bounded

- Native context completed 36/36 deterministic retrieval cases: 18 at 8192
  and 18 at 245760 requested tokens. This is single-request context evidence,
  not a general concurrent-full-window claim.
- The separate C2 near-full overlap validation passed with actual inputs
  229218 and 229246, 16384 output allowance each, two active samples during
  107.15 seconds of client overlap, no OOM, and 4167 MiB observed GPU0
  minimum free memory. It is a two-request admission/stream-overlap result, not
  scheduler-batch or two-full-262144-window evidence.
- Fixed-oracle agentic passed 18/18; multimodal passed 12/12 plus the ordered
  eight-image corpus 2/2. Those support capability within their stated frozen
  assertions, not exact output formatting.
- The amended repeat and unique stability runs completed all three rounds under
  `contender-stream-active/v1`; the one-round C2-long scenario also passed.
  They remain diagnostic client-stream evidence, explicitly not throughput or
  scheduler-overlap proof.

The coding pilot remains a required independent quality gate with the frozen
floor of at least 4/5. No no-spec SWE duplicate is required merely to interpret
the DFlash/no-spec *capacity* comparison; it would not repair the strict
contract failure.
