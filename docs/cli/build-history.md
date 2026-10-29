# Bound Buildx history

Read record metadata from one explicitly bound Docker builder:

```bash
anvil-serving host docker-image build-history --plan build-history.json
```

The JSON plan requires `schema` = `anvil-serving.build-history-plan/v1`, `context`, `builder`, `expected_engine_id`, `expected_buildx_revision` (the full 40-hex revision), `started_after` and `started_before`. Timestamps are UTC RFC 3339; the strictly open window may span at most seven days. Optional `ref` selects an exact full `builder/node/record-id` returned by a prior listing. The builder must be a running single-node Docker-driver builder on that context; engine and Buildx version must match before and after the read.

This command supports the producer's closed list-record JSON shape and fails on unsupported output. The producer includes a build name in its JSON; the command reads it transiently and discards it before returning or retaining results. It returns full record reference, coarse status, exact creation/completion timestamps and step counts. It does not invoke history inspect, request logs or attachments, or return build inputs or environment. In this producer, `Completed` is a coarse list status, not proof of build success: some external-error conditions are not represented by that field. Missing records, changed binding, invalid timestamps or inconsistent fields fail closed. A record does not prove builder quiescence, exclusive ownership, daemon cleanup, or absence of other builds.
