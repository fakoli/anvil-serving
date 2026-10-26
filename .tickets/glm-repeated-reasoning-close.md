# Diagnose repeated reasoning closure after the split-top-p correction

Status: open; bounded diagnostic, no promotion.

One pinned patched GLM FP4 request produced 137 completion tokens, with reasoning-end token 154,842 at zero-based positions 64 and 126 and no token-0 output. The first forced boundary survived; the full reasoning-limit gate failed because closure repeated. The visible content contained the correct arithmetic tuple with duplicated answer/prose, so its exact-output oracle also failed. Two fresh serving-worker dispatch receipts observed the patched split-mask path; they do not identify individual output tokens or prove the cause of the later repetition.

The exact source, forwarded controls, response/token IDs, native failed verdict and independent review are retained in the [dated finding](../docs/findings/2026-10-25-sampling-upstream-tolerance.md) and [evidence index](../docs/findings/2026-10-25-sampling-upstream-tolerance-evidence/README.md). The earlier all-masked/token-0 symptom did not recur in this one request. That is consistent with a narrow mechanism correction, not general causal proof or complete reasoning-control acceptance.

Next step: inspect the pinned MTP/rejection-sampling, thinking-budget state transitions and reasoning-parser paths offline against the retained token sequence. Keep model generation, budget enforcement and response parsing as separate hypotheses until source or trace evidence distinguishes them. Any further live experiment needs a separately frozen, reviewed bounded plan and advance notice before touching the running model. Do not adapt thresholds, retry this closed attempt, change runtime policy or promote on this evidence.

The separate [restoration JSON smoke issue](sampling-restoration-json-smoke.md) remains open and must not be conflated with this candidate's repeated-close failure.
