# Diagnose repeated reasoning closure after the split-top-p correction

Status: open; bounded diagnostic, no promotion.

One pinned patched GLM FP4 request produced 137 completion tokens, with reasoning-end token 154,842 at zero-based positions 64 and 126 and no token-0 output. The first forced boundary survived; the full reasoning-limit gate failed because closure repeated. The visible content contained the correct arithmetic tuple with duplicated answer/prose, so its exact-output oracle also failed. Two fresh serving-worker dispatch receipts observed the patched split-mask path; they do not identify individual output tokens or prove the cause of the later repetition.

The exact source, forwarded controls, response/token IDs, native failed verdict and independent review are retained in the [dated finding](../docs/findings/2026-10-25-sampling-upstream-tolerance.md) and [evidence index](../docs/findings/2026-10-25-sampling-upstream-tolerance-evidence/README.md). The earlier all-masked/token-0 symptom did not recur in this one request. That is consistent with a narrow mechanism correction, not general causal proof or complete reasoning-control acceptance.

Next step: inspect the pinned MTP/rejection-sampling, thinking-budget state transitions and reasoning-parser paths offline against the retained token sequence. Keep model generation, budget enforcement and response parsing as separate hypotheses until source or trace evidence distinguishes them. Any further live experiment needs a separately frozen, reviewed bounded plan and advance notice before touching the running model. Do not adapt thresholds, retry this closed attempt, change runtime policy or promote on this evidence.

The separate [restoration JSON smoke issue](sampling-restoration-json-smoke.md) remains open and must not be conflated with this candidate's repeated-close failure.

## 2026-10-26 actual GPU trace

The single instrumented request returned the exact answer in 75 completion
tokens, with one close at index 64. Both TP ranks observed the corresponding
budget-kernel store of token 154842 with logit `1e9`. No second close reproduced,
so its historical force-versus-sample attribution remains unknown.

The frozen analyzer also correctly refused full-sequence reconciliation: both
workers committed the exact 75-token API prefix followed by token 1654 after
the API stop token 154827. Preserve this failed gate; do not silently trim the
trace or turn prefix agreement into the original full-sequence pass. The
[trace finding](../docs/findings/2026-10-26-marker-gpu-trace.md) retains the raw
observations and their limits.

Before another run, investigate request retirement and pipeline overlap offline.
A revised contract must bind the client-visible terminal boundary separately
from any later worker commits, reject differences before that boundary, and
account for every suffix step and both ranks. Add a captured post-stop regression
case. Any repetition study needs prospective request/seed bounds and independent
review; keep the current nonreproduction and unknown verdict unchanged.
