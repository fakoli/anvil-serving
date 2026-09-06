# Expose current capacity from the existing admission and pressure owners

Status: T011 and T011.1 implemented candidates; consolidated acceptance pending.

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

T011 candidate 959f9f5e22b8452fec06f8bdf9403ca04e56241d passed52 focused
tests and Ruff after commit (EV8D90ECD9). T011.1 candidate
b985275c0de9ebf3971c3e5377517e352744f1b9 passed67 focused tests and Ruff
after commit (EV905CD04C), including real fixture HTTP owner/auth checks.
These are local source/regression results, not deployed scheduler evidence.
