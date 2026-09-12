# Router reliability controls

Start a pause investigation with `anvil-serving router diagnose --active --json`.
Use [request and session diagnostics](ROUTER-DIAGNOSTICS.md) to connect a request
to retained metadata and the owning serve's logs.

## Waits, streaming and cancellation

The following `[server]` settings are seconds. These defaults apply to chat
requests in OpenAI Chat Completions, Responses and Anthropic Messages dialects:

```toml
[server]
admission_timeout_s = 30
startup_timeout_s = 300
idle_timeout_s = 60
total_timeout_s = 900
heartbeat_interval_s = 15
```

Admission has a bounded wait. Expiry returns HTTP 503 with `Retry-After: 1`
before streaming headers are committed. Startup bounds the wait for upstream
activity after dispatch; idle bounds subsequent gaps. The total deadline is
absolute and never resets. After streaming headers, errors use the dialect's
terminal error frame and close the stream. The router never retries a partial
completion or substitutes another model.

SSE comment heartbeats keep an admitted, silent stream alive. They do not count
as upstream progress or reset a deadline. A downstream disconnect interrupts
the upstream transport and releases admission after the worker exits. Expected
disconnects produce one bounded event instead of a Python traceback. Upstream
failures remain separate events. Embedding, reranking and audio routes retain
their existing controls; these chat settings do not change those contracts.

## Client concurrency budgets

Use the existing scoped authorization policy with `inference:use` credentials.
Keep credentials in protected files or environment references. For example:

```toml
[server]
auth_env = "ANVIL_ROUTER_TOKEN"
authorization_policy_path = "/etc/anvil-serving/authorization.json"
client_limits = { desktop = 2, automation = 1, _legacy = 2 }
```

The policy uses the existing version-1 schema, with client entries containing
`id`, `scopes` and either `credential_env` or `credential_file`. A configured
budget counts active and waiting chat requests for the credential's client ID.
Exhaustion returns HTTP 429 with `Retry-After: 1`; it does not wait in another
queue. Unlisted clients retain the global admission limit. `_legacy` groups
callers sharing the legacy router credential. Client-supplied headers cannot
choose this identity. Caps prevent a configured client from consuming the
whole pool; this is not weighted scheduling or a per-client reservation.

`inference:use` grants chat and model discovery access, not management or
diagnostic access. The separate `workloads:read` scope grants request diagnostics.
The legacy operator credential retains its existing authority. Scope files
load at startup; changing them requires a managed router restart.

## Engine metrics

Routing uses declared endpoints, capabilities and dialects. The metrics adapter
recognizes these public Prometheus metric families without changing selection:

| Engine | Normalized observations when exposed |
| --- | --- |
| vLLM | Running/waiting requests, KV utilization, prompt/completion counters, prefix-cache hits and queries. |
| SGLang | Running/waiting requests, token/KV utilization, generation throughput, cache hit rate and available counters. |
| llama.cpp | Processing/deferred requests, KV utilization, prompt/prediction counters and throughput. |

Separate scheduler replicas are aggregated; tensor/pipeline-rank copies are
deduplicated. Missing or conflicting measurements remain unknown. Counters
are cumulative engine observations and can reset after restart. They are not
per-request token usage. Metrics support is covered by fixtures for all three
engines; a deployment still needs a live check against its installed engine
version and enabled metrics. SGLang metadata prefers `/server_info`, with the
deprecated `/get_server_info` retained only as a compatibility fallback.

## Retention and optional traces

Set `server.decision_log_path` to a private persistent mounted file for request
history across router recreation. Rotated files are the source of retained
history; no separate database or model payload store is introduced.

`server.trace_export_url = "http://127.0.0.1:4318/v1/traces"` enables optional
OTLP/HTTP JSON export to an explicitly configured private IP collector. URLs
cannot contain credentials. Redirects and proxy environment settings are
disabled. Export uses a bounded queue, one background worker, and no retries;
collector failure never blocks inference. Transport timeouts bound individual
blocking operations, not the whole export when a collector trickles headers.

Spans contain allowlisted request IDs, session/client IDs, route, timing, token
counts and outcome. They contain no prompt, response, tool payload, credential,
or raw upstream error. These are standalone request spans correlated by IDs;
W3C parent trace propagation is not implemented.
