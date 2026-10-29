# Bound Buildx history

Read record metadata from one explicitly bound Docker builder:

```bash
anvil-serving host docker-image build-history --plan build-history.json
```

The plan is a JSON object with `schema` set to `anvil-serving.build-history-plan/v1`, `context`, `builder`, `expected_engine_id`, `started_after` and `started_before`. Timestamps are UTC RFC 3339; the strictly open window may span at most seven days. An optional `ref` selects one complete 25-character Buildx record ID for detailed inspection. Context and builder must name a running single-node Docker-driver builder on that context; the engine ID must match before and after the read.

The command returns selected record ID, status, timestamps, duration and, for an exact selected reference, completed and total step counts. It uses fixed output templates; it does not request build inputs, environment, logs, provenance attachments or arbitrary fields. Missing records, unsupported output, changed binding and invalid fields fail closed. The server-side time filters are strict and the returned timestamps are checked independently. A record is not proof of global builder quiescence, exclusive ownership, daemon cleanup, or absence of other builds. The local Buildx version must support history commands and the documented fields; unsupported behavior is an error.
