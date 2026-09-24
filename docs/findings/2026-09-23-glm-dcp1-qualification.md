# GLM-5.3-Flash DCP1 qualification in progress

The DCP1 continuation is an active, unpromoted qualification of the pinned
GLM-5.3-Flash EXL3 checkpoint on two RTX PRO 6000 Blackwell Max-Q cards. It
tests the single DCP2-to-DCP1 change after the retained DCP2 mixed-load fault;
it does not establish a root cause, crash rate, or general hardware maximum.

## Retained interim observations

- The frozen DCP1 batch-2,048/C4 configuration reports 825,268 logical KV
  tokens. Three 201K unique-prefix and three repeated-prefix 8K-anchor rounds
  passed, as did one 310K/C2 overlap. The earlier 201K/4K third-anchor result
  exhausted its completion budget without a visible answer and remains a
  failure; the later 8K probes do not replace it.
- The separate quality screen passed 12/12 and the agentic final gate passed
  18/18. A natural-output diagnostic retained 18,016 visible tokens and its
  end marker, but it is not a SWE or application-correctness result.
- The frozen five-task SWE scout completed official grading at 4/5 resolved,
  meeting its absolute at-least-3/5 gate. Its CPython/package environment
  differs from the retained 3/5 baseline, so the pair cannot support a causal
  DCP1 or score-improvement claim. The earlier composite gate remains a
  retained diagnostic failure because it correctly records that mismatch.
- The separate 64K diagnostic reached its declared completion ceiling with a
  native `length` failure. Independent review found the retained visible text
  coherent through truncation, which satisfies the diagnostic's predeclared
  continuation rule without waiving the failure. Its one-attempt visible yield
  is materially below the matched incumbent (24,123 versus 70,772 characters;
  three versus eleven complete chapters); the cause is unknown.
- Isolated routed preflight passed 28 observations across eight functional
  families, including 20 tool-batch observations. In a separate offered-eight
  nominal-32K diagnostic, six served responses met the identity, own-canary,
  and strict-output contract while two requests received bounded HTTP 503
  admission timeouts. The native exit-1 result remains performance-ineligible;
  the corrected, versioned gate joins all eight gateway IDs to terminal rows
  with the same configuration and closes only the configured C4
  dispatch/admission boundary. It makes no C8 or sustained-queue claim.
- Independent review recommends controlled promotion only for the exact DCP1
  batch-2,048/C4/0.97 recipe under the retained authorization. Its 825,268 KV
  tokens are 41.73% below retained DCP2's 1,416,244, so four full 327K windows
  are unsupported. r11 direct and production-routed preflights each passed
  28/28 checks, with 13 native-client semantic paths passing. Several retained
  client responses failed strict format (fenced JSON or Mac prose), so this is
  not a strict-format fleet pass. This remains a
  mitigation, not a root-cause fix, crash-rate finding, or speed claim.
- Dashboard publication inputs are verified (nine source-matched runs, 99
  metrics, and nine authenticated cards), but the public post remains pending
  merge and deployed readback.
- The batch-4,096 cells were strict-correct but did not produce a comparable
  estimator; batch 8,192 started below the campaign's conservative C2 reserve
  and received no dependent requests. Batch 2,048 remains selected for this
  campaign.
- The offered-concurrency-8 diagnostic passed seeds 801 and 1801 at 16/16,
  then seed 2801 failed 1/16 strict/canary. The engine stayed healthy, but the
  entire dependent C8 comparison stopped. Offered C8 is outside the declared
  production C4 admission boundary and does not qualify C8.

## Decision boundary

The campaign remains active and `no-promotion`. Remaining gates include the
native-client acceptance, restoration and independent
promotion review. The existing selected route and
rollback remain governed by their prior evidence; this finding makes no route
or lifecycle change.

The selected public-native artifacts, redaction receipts, and explicit gaps are
in the [active evidence index](2026-09-23-glm-dcp1-qualification-evidence/README.md).
