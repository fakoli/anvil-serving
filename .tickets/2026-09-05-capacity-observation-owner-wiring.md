# Expose current capacity from the existing admission and pressure owners

Status: closed implementation design; source tasks T011 and T011.1 pending.

The capacity surface already projects one atomic replica admission snapshot,
but omits member ceilings/state and current normalized pressure. The runtime
owns ReplicaPressureCache, while build_model_capacity currently receives only
admission and the legacy metrics provider. Calling snapshot from visibility
would schedule refreshes and would conflate an observation with collection.

Add a non-scheduling peek with the existing freshness/overdue/closed behavior,
bounded capacity-only projection and fixed Prometheus gauges. Keep historical
selection scores in the existing decision serializer; current output must not
rerank or invent current eligibility. T011.1 explicitly wires the existing
cache through the real server path and proves read-only behavior over HTTP.

Keep legacy direct/round-robin JSON and metrics compatible. Missing ownership
or telemetry is unavailable/unknown, never zero. No deployment, model or route
change is authorized or performed by these source tasks.
