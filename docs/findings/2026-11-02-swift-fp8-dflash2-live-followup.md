# Swift FP8 + DFlash2 — routed observational follow-up

This closed, single-profile follow-up made no model, runtime, configuration, or
lifecycle change. It is **not** a capacity qualification, a speculative-decoding
comparison, or a promotion record. The earlier matched campaign's strict
128-word/canary result remains **0/6** and unchanged.

## What was observed

The routed chat family passed smoke, JSON, tools, streamed tools, and tool-result
continuation checks (9/10 observations). The remaining Responses API check was
blocked with HTTP 403 by existing credential scope; credentials were not changed.

One independently labelled retrieval probe returned its needle correctly in
30.337 seconds. Native usage reports **108,178 actual prompt tokens** and 81
completion tokens (66 reasoning), rather than the nominal 128K request label.
The original 224K nominal request was rejected at router admission with HTTP 413
before upstream execution. The retained size analysis records a 322,585-token
router input estimate plus 4,608 output budget for that rejected request.

The planned 64K/C1 summary cell completed six streams, but all six failed the
opening-canary contract. Each stream had exactly its own marker, no foreign
marker, a terminal `stop`, and a complete capture; none put the marker at the
required opening position. They are therefore **0/6 performance-eligible**.
The C2 and 128K capacity cells were skipped under the frozen disposition rather
than retried or used to manufacture a passing result. Their timing fields are
not published as latency or throughput measurements.

## Continuity and limits

Before/after continuity checks found the candidate workload and both protected
workloads unchanged and running, with zero recorded OOM events; router custody
was unchanged. This is a point-in-time continuity check, not whole-fleet
acceptance or cadenced resource telemetry.

The single profile has no matched no-speculation control, isolated traffic
population, or engine-counter attribution. Consequently this finding makes no
DFlash2 speed claim, concurrency claim, capacity claim, quality ranking, or
promotion claim. It preserves the failed and skipped evidence for a later,
separately authorized investigation.

See the [sanitized evidence index](2026-11-02-swift-fp8-dflash2-live-followup-evidence/README.md).
