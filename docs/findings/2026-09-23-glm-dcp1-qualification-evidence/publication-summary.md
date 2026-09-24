# GLM DCP1 r11 publication summary

<!-- benchmark-publication-summary/v1 -->

The exact DCP1 batch-2,048/C4/0.97 r11 recipe is selected and deployed after
accepted direct, production-routed, and native-client checks. The final solver
review, client/control-plane review, dashboard receipt, successful Pages run,
and public HTTP/browser readback are retained. The exact r10 recipe remains the
rollback.

## Canonical facts

- **Configuration:** DCP1, batch 2,048, C4, GPU-memory utilization 0.97.
- **Functional evidence:** direct and production-routed preflights each passed
  28/28; 13 client semantic paths passed.
- **Capacity:** 825,268 KV tokens; this is 41.73% below retained DCP2's
  1,416,244 KV tokens. Four full 327K windows are unsupported.
- **Client caveats:** five Pi paths, Pi Web, and two secondary Hermes vision
  responses returned fenced JSON; Mac OpenClaw also added explanatory text
  before correct JSON. Semantic paths passed, but strict-format compliance did
  not. Pi Web's message-count metadata was zero despite a retained successful transcript.
- **Decision boundary:** publication verified. This is a mitigation,
  not a root-cause fix, crash-rate study, or speed winner.
- **Evidence:** [finding](../2026-09-23-glm-dcp1-qualification.md) ·
  [evidence index](README.md) · [artifact manifest](artifact-manifest.json).

## Claim ledger

| Public claim | Conditions | Evidence |
| --- | --- | --- |
| r11 direct and production-routed function passed | 28 checks each, fixed protocol | `r11-direct-preflight.json`; `r11-production-routed-preflight.json` |
| Fleet client semantics passed | 13 native paths, exact nonce/card oracle; strict format failed on retained paths | `client-acceptance-summary.json` |
| Client/control-plane review accepted with caveats | fleet changed=0 and seven read-only mounts | `client-control-plane-independent-review.json` |
| Dashboard imports verified | nine source-matched runs, 99 metrics, nine authenticated cards, repeat imports byte-identical | `promotion-publication-receipt.json` |
| Full-window C4 capacity is bounded | 825,268 KV; no four full 327K windows | `final-qualification-independent-review.json` |
| DCP1 is not a speed or root-cause claim | performance-ineligible/limited evidence retained | `routed-admission-capacity.json`; `final-qualification-independent-review.json` |

The [publication closure receipt](publication-closure-receipt.json) records the
merged public page's HTTP 200, Chrome readback, and successful Pages run.
