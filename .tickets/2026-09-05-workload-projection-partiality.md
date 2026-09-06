# Preserve honest partial router workload results

Status: resolved in source; workload-visibility:T002 accepted before runtime wiring.

Independent tests reproduced two source-projection defects. A canonical
terminal record with a timestamp 31 seconds ahead of collection caused final
SourceResult validation to raise, withholding a separate healthy active record.
The producer must validate candidates independently and quarantine malformed or
future records with a fixed partial-source error. Remote wire validation stays
strict at the source boundary.

With one represented and one saturated request, a sink blocked after terminal
append caused two returned records plus one numeric omission: a false total of
three. A second probe at 31 seconds showed the represented stale record filtered
out but the anonymous stale request still counted as a matching omission.

Anonymous phase counters cannot supply exact per-request freshness. Preserve
bounded memory and exact internal active counts, but report unknown omission
for potentially matching anonymous requests. Track completion-in-flight with
phase counters only and mark matching queries partial until completion clears
them. Never retain another terminal store, unbounded request/timestamp map or
registry lock across a slow sink to manufacture stronger evidence.

Regression gates include exact future-skew boundaries, healthy peers, stale
saturation, blocked and failing sinks, provable filter exclusions and cleanup.
This correction is observational only; runtime integration and live deployment
remain separate tasks.

Accepted revision `ec843eb6333ca6188b218c70a08b4e04c72171bf` includes the
reviewed correction `95505cca` and the retained unaccepted implementation
checkpoint. The post-commit gate passed 76 tests and Ruff. An independent
in-memory removal of per-record source validation failed both future-skew
regressions as expected. Anvil evidence `EV2830AB39` and acceptance proof
`workload-visibility-T002-E000583` record this source-only disposition.
