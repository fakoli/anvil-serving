# Preserve honest partial router workload results

Status: open; workload-visibility:T002 correction before runtime wiring.

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
