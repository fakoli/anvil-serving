# Serves and eval

`anvil-serving serves` owns lifecycle for declared local model processes. `anvil-serving eval`
checks and benchmarks one explicit endpoint. The two surfaces provide evidence for an operator;
they do not select a model at request time.

## Serve lifecycle

Use a manifest for every managed serve. Preview mutations before confirmation:

```bash
anvil-serving serves status --manifest <serves.toml>
anvil-serving serves up <serve-name> --manifest <serves.toml> --dry-run
anvil-serving serves up <serve-name> --manifest <serves.toml> --confirm
anvil-serving serves logs <serve-name> --manifest <serves.toml> --tail 200
```

Use transition commands to quiesce and drain a local tier before an operator-approved serving
change. Use `serves promote` for the managed promotion or rollback transaction.

## Preflight and benchmark

Run preflight against the exact endpoint and advertised model name:

```bash
anvil-serving eval preflight \
  --base-url http://127.0.0.1:<port>/v1 \
  --model <served-model> --confirm
```

Then record a capacity or quality benchmark with its model revision, engine, quantization,
context, concurrency, hardware, failures, and raw artifact path. Publish a dated finding under
`docs/findings/` and update `docs/BENCHMARKS.md` whenever the result changes a current
recommendation or reference deployment.

## Direct aliases

The router's `[router.model_routes]` maps a caller-facing alias to one local tier. Update that
mapping only as an explicit configuration change after the target serve has the required
evidence. The gateway returns 404 for an unknown alias and 503 for a configured alias whose local
tier is unavailable; it does not fall back to another model.
