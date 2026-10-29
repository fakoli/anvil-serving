# Recipe monitoring

Refresh monitoring for an already running recipe:

```sh
anvil-serving models recipes monitor-sync MODEL --container NAME --registry recipe.toml --confirm
```

Omit `--confirm` to preview. The command verifies the local container, recipe
and registry digests, exact loopback port, GPU assignment and served model
before publishing a new monitoring generation. It does not restart the model.

Monitoring is opt-in through `recipe-monitoring.json` in the operator home:

```json
{
  "schema": "recipe-monitoring/v1",
  "ports": [39126, 39168],
  "engine_contracts": {"vllm-pinned": "vllm", "ignis-pinned": "ignis"}
}
```

Engine keys must match the recipe's declared engine. Supported metrics
contracts are `vllm`, `sglang`, `flare`, `ignis` and `llamacpp`. An optional
`model_api_key_files` object maps port strings to protected absolute key-file
paths; credentials are never written into the monitoring receipt.

Recipe load withdraws the previous registration before mutation and publishes
only after identity checks pass. Ignis publication first requires a successful,
credential-free `/ready` response; other contracts use `/health`. Unload withdraws
the selected registration. Removing the configuration disables new publication;
managed load and unload still invalidate an existing receipt whose exact container
or recorded port matches, so a reused port cannot inherit its generation.
The receipt is `monitoring/active-recipe.json`; an observer reads that directory
without write access and must independently check live health and identity.
Exactly one running recipe may occupy the configured monitoring slot. Remote
Docker endpoints, ambiguous bindings and changing credentials fail closed.

The observer is a separate infrastructure deployment. An active registration
alone does not prove fresh metrics, complete interval coverage or successful
benchmark execution. Ignis requires its API UI and metrics options enabled so
the observer can read `/ui/metrics` on the same API port.

For llama.cpp, enable its native metrics endpoint and use a single served alias.
The separately deployed observer requires an active matching registration, exact
sole model catalog, and validated native scheduler gauges before publishing
identity-bound gauge copies. Native counters remain native; no unsupported
request completion or latency metric is synthesized.
