# Diagnose one router request

The gateway observes a request's route, admission, upstream relay, completion,
and usage without retaining its prompt or response. Use that evidence to
separate a slow response startup, a long completion, a context rejection, or a
client cancellation before inspecting the selected inference service.

## Follow a request

Save the response's `X-Anvil-Request-Id`. The gateway generates this unique
`req_` identifier; callers cannot choose it. It is present on authenticated
inference responses, including errors after authentication. Requests rejected
before routing may have an identifier without a terminal decision record.

```bash
anvil-serving router diagnose --request-id req_0123456789abcdef0123456789abcdef --json
```

The command uses `ANVIL_ROUTER_URL` (default `http://127.0.0.1:8000`) and reads
the credential from `ANVIL_ROUTER_TOKEN`. `--router-url` selects an explicit
origin; `--auth-env` selects a different credential environment variable.
Credentials are never command-line values. HTTP origins must use a private or
loopback IP; DNS origins require HTTPS. Redirects and proxy environment
settings are ignored to keep the credential on the chosen origin.

This command performs only two bounded metadata GETs: request lookup and current
router status. It never sends a model request, reads payloads, or changes state.
Each response is capped at 128 KiB. `--timeout` sets the socket timeout for
each read, from greater than zero through 30 seconds (default 5); it is not a
total command deadline. Router status can be unavailable while the request
diagnosis remains useful.

The same record is available through authenticated
`GET /v1/requests/{request_id}`. Search bounded router logs for
`gateway_request_id=<id>`. The relay forwards the generated identifier as
`X-Request-Id` to its selected inference upstream, so a supporting engine can use
the same identifier in its logs. Upstream support is engine-specific and must
be verified; sending the header alone does not prove the engine logged it.

`X-Request-Id` remains a legacy caller correlation header. A valid supplied
value is preserved in `request_id`; absent or invalid values use the generated
identifier. `X-Anvil-Workbench-Run-Id` and `X-Anvil-Task-Id` remain bounded
optional lineage fields and are never forwarded upstream. Repeated legacy
caller IDs retain newest-match lookup semantics; use `X-Anvil-Request-Id` for
unambiguous diagnosis. The `req_` plus 32 lowercase hexadecimal characters
namespace is reserved for generated IDs: lookup never falls back to a caller
ID with that shape, including after the generated record is evicted.

## Read the measurements

Chat requests provide the phase, finish, usage-provenance, and output-limit
measurements below. Embeddings, reranking, and audio share the generated
correlation ID and terminal outcome; their phase measurements remain unknown.
Audio retains its measured total latency. Embedding/reranking records have an
unmeasured legacy `latency_ms: 0`; diagnosis reports that value as null.

| Field | What it proves | Limit |
| --- | --- | --- |
| `latency_ms` | Time from router backend entry to terminal relay outcome. | Excludes client upload, network transit to the router, and final client delivery. |
| `readiness_check_ms` | Time this request spent checking readiness, including a cached result. | Does not claim a fresh probe or engine queue duration. |
| `upstream_duration_ms` | Time in the selected upstream invocation and its consumed stream. | Includes transport and client backpressure; it is not pure GPU time. |
| `time_to_first_content_ms` | Time from backend entry to its first nonempty emitted content fragment. | Not universal TTFT: tool-only output can be null; buffered requests observe content only after the response arrives. |
| `finish_reason` | A normalized supported completion reason. | Unknown provider strings never become free text in evidence. |
| `usage` | Prompt/completion counts with independent `prompt_source` and `completion_source`. | `upstream` means reported by the selected service; `estimated` is a local approximation. Neither is a billing guarantee. |
| `output_limit` | Requested and effective limits and whether the router clamped them. | An upstream may impose additional limits. |

An absent measurement is null; a measured sub-millisecond duration may be zero.
Legacy records have unknown provenance. Current-buffer stats aggregate measured
phase samples and normalized finish counts; Prometheus gauges use model aliases,
never request IDs as labels. Eviction and process restart can reduce gauges.
`usage_sources` counts upstream, estimated, and unknown records separately for
prompt and completion totals. Interrupted text output is estimated from the
fragments observed before termination; an unknown count is not proof of zero
engine work. Buffer token totals mix known and estimated values and exclude
unobserved work.

`startup_dominated` means the first content observation occupied more than half
the recorded duration. `completion_dominated` means it did not. These are timing
descriptions, not engine root causes. A `length` finish suggests examining the
output ceiling and reasoning budget; it does not prove the visible answer was
complete. A tool-call finish identifies a protocol transition, not successful
tool execution or model quality.

## Evidence boundaries

The diagnostic envelope is `anvil-router-diagnosis/v1`. `request` contains
allowlisted terminal evidence; `current_router` identifies the process observed
by a separate GET. The latter does not establish which configuration served an
earlier request. A not-found record may still be active, have been evicted,
belong to a previous process, or come from a router without request lookup.

The gateway stores no prompt, response text, tool arguments/results, audio,
transcripts, raw upstream errors, or arbitrary headers in these measurements.
It does not infer intent, replay requests, inspect an engine's logs, or choose
a replacement model. Continue a failure investigation with the owning managed
serve's bounded logs; a router symptom alone is not a root cause.

For optional persisted metadata, the existing server `decision_log_path`
configuration writes rotated JSONL. Those files are private runtime data and
belong in neither repository. This command reads the process's current bounded
buffer; it does not search retained JSONL or provide a historical database.

See [router commands](cli/router.md#diagnose), the
[observability API](THIN-CAPABILITY-GATEWAY.md#router-observability-api), and
[private networking](TAILSCALE-NETWORKING.md).
