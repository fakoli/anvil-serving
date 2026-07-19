# Anvil Workbench integration

Anvil Workbench is an optional, separate private-tailnet product. It is not a router component and is not an Anvil State authority.

- Anvil State remains the canonical owner of PRDs, task claims, evidence, and acceptance.
- Anvil Serving remains the local model-routing and operations plane.
- Workbench owns its browser UI, run supervision, redacted transcripts, hash-bound approvals, and delivery orchestration.
- Neo4j is an evidence/lineage projection only. It cannot make an approval, mutate State, or run arbitrary Cypher for an agent.

## Lifecycle

The packaged Compose template runs a published Workbench hub image together with Postgres and Neo4j. It binds the browser endpoint to `127.0.0.1` by default. Publish it through a tailnet-aware identity proxy; do not expose the container port publicly.

This boundary is intentional: **Anvil Serving is the lifecycle and private-exposure plane, not
the Workbench application.** It can start, stop, inspect, and expose the optional container
stack through the configured private edge, while Workbench owns its UI, supervision, approvals,
and delivery logic. That keeps Workbench independently deployable without making every operator
recreate its safe container lifecycle, credentials boundary, and tailnet exposure.

That proxy must inject the configured `WORKBENCH_IDENTITY_HEADER` only after stripping any browser-supplied copy. Workbench defaults to the `Tailscale-User-Login` header and rejects an absent identity; the insecure development override is not for a tailnet hub.

```powershell
anvil-serving workbench up --env-file .\workbench.env --confirm
anvil-serving workbench status --env-file .\workbench.env
anvil-serving workbench logs --env-file .\workbench.env --tail 200
anvil-serving workbench down --env-file .\workbench.env --confirm
```

`up` and `down` require `--confirm`; `down` preserves named database volumes. Use `--dry-run` to inspect the exact Docker Compose command. The environment file supplies owner, approver set, database passwords, and the private Anvil router base URL. The router token remains an environment variable on the Docker host, never a browser value or committed configuration.

## Responses and correlation contract

Workbench-managed agents must use Anvil Serving's `POST /v1/responses` endpoint. The supported stateless subset includes `input`, `instructions`, function tools, function-call outputs, JSON-schema output, standard response objects, and SSE. Stateful response chaining, storage, background requests, hosted tools, and other unsupported fields return an explicit 400 rather than silently falling back to a provider.

The bridge/harness may attach the following compact opaque headers:

- `X-Anvil-Workbench-Run-Id`
- `X-Anvil-Task-Id`
- `X-Request-Id`

Anvil Serving records valid values in route decisions and safe decision summaries. They are not forwarded to model backends and do not contain prompt or transcript data.

When the hub has `WORKBENCH_EMBEDDING_MODEL` (and optionally `WORKBENCH_RERANK_MODEL`), its fixed evidence-search tool calls Anvil Serving's purpose-model `/v1/embeddings` and `/v1/rerank` routes. The resulting Neo4j vector index is a read projection over redacted evidence only; unavailable local retrieval falls back to graph/keyword lookup, never a raw provider call.

## Bridge boundary

Run `workbench-bridge` beside each project. It reads State through the supported CLI/work-packet surface and tails the canonical event log; it never opens or remotely mounts State's SQLite database. The bridge keeps GitHub credentials local. It can plan, claim, edit, test, and submit evidence without an approval, but commits/PR creation, State acceptance, merge, deployments, and model-policy changes require a one-time hash-bound approval.

For Codex, the bridge uses the configurable provider's `wire_api = "responses"` and `http_headers` fields to send run/task correlation to Anvil Serving. It exposes neither model nor GitHub credentials in those overrides.
