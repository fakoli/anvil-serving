# Controller adapter bindings

Observatory controls are enabled only by a private deployment config. The browser
selects stable resource and action IDs; it never supplies controller addresses,
tool names, operator paths, or commands. The adapter verifies `expected_node`,
reads the controller's live tool catalog, and marks missing operations unavailable.

The constructor seam is `ControllerAdapter(config, environment)`. Its methods are
`snapshot()`, `controls(resource_id)`, `preview(resource_id, action_id, values,
parameters)`, `execute(preview, intent_key)`, `reconcile(preview, intent_key)`, and
`verify(preview, result)`. `preview` includes a server-private `binding` that the
facade must remove from every browser projection.

This example uses placeholders intentionally. Real node identities, paths, URLs,
and the token environment name belong only in the private operator deployment.

```toml
schema = "anvil-observatory/controller-adapter/v1"

[controller]
url = "https://CONTROLLER_ADDRESS:PORT"
expected_node = "OWNER_NODE_ID"
token_env = "PRIVATE_CONTROLLER_TOKEN_ENV"
topology = "TOPOLOGY_ID"
execution_host = "OWNER_NODE_ID"
execution_runtime = "RUNTIME_ID"

[[resources]]
id = "serve.primary"
label = "Primary serve"
host_id = "OWNER_NODE_ID"
kind = "serve"
manifest = "/PRIVATE_OPERATOR_HOME/serves/responders.toml"
serve = "primary"
tier = "primary-local"
aliases = ["primary"]
gpu_ids = ["gpu0"]
probe_timeout_seconds = 60
probe_max_tokens = 256

[[resources]]
id = "router.primary"
label = "Primary router tier"
host_id = "OWNER_NODE_ID"
kind = "configuration"
config = "/PRIVATE_OPERATOR_HOME/router.toml"
tier = "primary-local"
# This transaction recreates the router, so list every routed alias affected.
aliases = ["primary", "auxiliary"]
container = "anvil-router"
installed_config = "/etc/anvil/config.toml"
compose = "/PRIVATE_OPERATOR_HOME/docker-compose.yml"
service = "router"
env_file = "/PRIVATE_OPERATOR_HOME/runtime.env"
topology = "/PRIVATE_OPERATOR_HOME/operator-topology.toml"

[[resources]]
id = "recipe.primary-candidate"
label = "Primary candidate recipe"
host_id = "OWNER_NODE_ID"
kind = "recipe"
registry = "/PRIVATE_OPERATOR_HOME/serve-recipes.toml"
model = "ORG/MODEL"

[[resources]]
id = "profile.exclusive"
label = "Exclusive profile"
host_id = "OWNER_NODE_ID"
kind = "profile"
manifest = "/PRIVATE_OPERATOR_HOME/serves.toml"
profiles = "/PRIVATE_OPERATOR_HOME/serve-profiles.toml"
profile = "exclusive"
mode = "dual-gpu-exclusive"
active_serves = ["exclusive-model"]
inactive_serves = ["split-a", "split-b"]

[resources.admissions]
primary = "admitting"

[resources.gpu_owners]
compute_a = ["exclusive-model"]
compute_b = ["exclusive-model"]

[[resources]]
id = "experiment.context-smoke"
label = "Context smoke evaluation"
host_id = "OWNER_NODE_ID"
kind = "experiment"
suite = "context"
experiment_limit = 1

[resources.spec]
schema = "anvil-serving/context-job-v1"
suite = "context"
run_id = "DECLARED_RUN_ID"
# Include the complete bounded canonical benchmark job specification here.
```

Configuration edits expose only `max_concurrency` and `max_output_tokens` from
the installed router tier. Apply requires the configured source to be the exact
bind mounted `/etc/anvil/config.toml`, the source and installed digests to match,
an explicit Compose file and environment file, the shared serving-authority lock,
and a force recreate. Failure restores the saved bytes and recreates the prior
router.

Recipe edits expose only an existing recipe's `--max-model-len`,
`--max-num-seqs`, and `startup_timeout_seconds`. Missing or duplicate flags are
unsupported. Apply holds the registry's owner lock, checks the preview digest,
writes an immutable digest-named backup, and atomically replaces the registry.

Profile controls require a private structured postcondition. Verification resolves
the declared profile through `serves_profile`, then checks every active and inactive
member through `serves_status`, the exact operating mode and per-role physical owner,
and every declared router admission state. An admitting tier must also report ready.
Profiles without this complete postcondition remain visible but unsupported.

Experiments submit only a complete predeclared durable benchmark job. Preview
runs `benchmark_job_preflight`; it never invokes the model and never submits a
job. Reconciliation reads the controller operation record and never redispatches.
