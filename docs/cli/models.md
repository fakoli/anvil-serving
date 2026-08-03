# Models & recipes

[CLI overview](../CLI.md) · [Model serves](serves.md) · [Evaluation & benchmarks](eval.md)

The `models` family manages four related resources: the local model catalog,
downloaded artifacts, reusable serve recipes, and cache storage. Use recipes to move
from a known working engine configuration to a candidate container; use
[`serves switch`](serves.md#switch-primary-by-recipe) when the candidate is ready to
replace a deployed role.

## Switch Primary to another model

You switch the `heavy` role to a recorded recipe; you do not edit the active
Compose service by hand.

```bash
anvil-serving models recipes list
anvil-serving models recipes show MODEL
anvil-serving serves switch primary MODEL --dry-run
anvil-serving serves switch primary MODEL --confirm
```

`list` shows which recipes activate `heavy`. `show` resolves the exact model id or
unique basename and prints the engine configuration plus the same switch-preview
command. The dry run validates the activation plan without changing the running
service; `--confirm` applies the reviewed switch.

If the model does not have a compatible recorded recipe yet, create or update an
operator recipe first. A recipe is the complete known-working model-and-engine
configuration, so changing only its `model` field is not treated as a safe model
swap.

## Choose a workflow

| Goal | Start here | Then |
| --- | --- | --- |
| See models already on this host | `models sync --dry-run` | Apply with `--confirm`, then inspect `model-library/INDEX.md`. |
| Download a Hugging Face model | `models pull REPO --dry-run` | Apply with `--confirm`. |
| Find a known working serve configuration | `models recipes list` | Inspect it with `models recipes show MODEL`. |
| Replace the deployed Primary recipe | `models recipes list` | Choose a row that activates `primary`, inspect it, then preview `serves switch primary MODEL --dry-run`. |
| Add or revise an operator recipe | `models recipes create|update ... --dry-run` | Apply with `--confirm`; retain the numbered backup. |
| Start a candidate without changing routing | `models recipes load MODEL --container NAME --dry-run` | Apply, run `eval preflight`, then review a `serves switch`. |
| Remove one exact cached revision | `models cache remove OWNER/REPO --revision COMMIT --dry-run` | Apply with `--confirm` after reviewing the snapshot and reclaimable blobs. |
| Reclaim cache space broadly | `models cache prune --dry-run` | Add `--execute --confirm` only after reviewing the protected mixture. |

## Commands

| Command | Purpose |
| --- | --- |
| `models sync` | Synchronize the model catalog. |
| `models pull` | Pull a model artifact. |
| `models score` | Rank models from benchmark evidence. |
| `models recipes list` | List recorded serve recipes. |
| `models recipes show` | Show one recipe. |
| `models recipes create` | Add one recipe to an operator registry. |
| `models recipes update` | Replace one selected recipe. |
| `models recipes delete` | Delete one selected recipe. |
| `models recipes load` | Start a named local container from one recipe. |
| `models recipes status` | Inspect one exact recipe-loaded candidate container. |
| `models recipes logs` | Read bounded logs from one exact recipe-loaded candidate container. |
| `models recipes unload` | Remove one exact recipe-loaded candidate container. |
| `models cache inventory` | Record a read-only model-cache and Docker storage inventory. |
| `models cache remove` | Plan or remove one exact cached repository revision. |
| `models cache prune` | Plan or prune model-cache storage. |

## Catalog sync

`sync` scans local Hugging Face caches and plain model directories, then writes
structured `cards/*.json` summaries plus a human `INDEX.md`. Preview resolves the
same output and source roots but performs no scan and creates no directory.

```bash
anvil-serving models sync --out ./model-library --dry-run
anvil-serving models sync --out ./model-library --confirm
```

Source precedence is command flags, configured model roots, then platform discovery.
Use the platform path separator for multiple `--hf-roots` or `--model-dirs` values
(`:` on Linux/macOS, `;` on Windows).
Apply builds a complete staged catalog, moves any prior catalog to a numbered
`.anvil.bak.N` directory, and installs the replacement. Removed source models therefore
do not survive as stale cards. The target must be new, empty, or an existing catalog
containing both `cards/` and `INDEX.md`; sync refuses files, links, general directories,
the checkout, the current directory, and the user home. One output-specific lock covers
the scan and replacement. A worker error or incomplete staged catalog leaves the active
catalog untouched.

## Artifact pull

`pull` downloads a Hugging Face repository into a named Docker volume. It never
places a token value on the command line: `--token-env` names the source variable,
and `--token-file` is a fallback dotenv file.

```bash
anvil-serving models pull openai/gpt-oss-120b --dry-run
anvil-serving models pull openai/gpt-oss-120b --confirm
```

The named-volume default avoids slow host bind mounts on Windows/WSL2 and remains
valid on Linux and macOS Docker hosts. Preview resolves repository filters, token
mode (including the environment-variable name and expanded dotenv path, never a
token value), preconditions, ordered Docker actions, resumable recovery, and the
fact that downloaded bytes have no automatic rollback. It also discloses the
machine's automatic WSL cache-reclaim policy. When that policy is enabled, a
successful confirmed pull captures operation cache growth and evaluates the
best-effort page-cache-only postcondition once. A skip or failure warns without
changing the successful download's exit code.

A confirmed pull first queries repository metadata (or uses
`--expected-bytes`), inspects the target snapshot inside the named volume, and
fails before downloading unless free bytes cover the missing artifact bytes
plus `--headroom-gib`. The download keeps native `hf download` progress and
resumability. Success is reported only after the exact requested snapshot
exists with no incomplete files or broken links.

## Recipes

A serve recipe records a working model-and-engine configuration independently from a
running container. The shipped registry is useful for discovery. Mutations require an
explicit operator-owned registry path, which keeps packaged defaults immutable and
reviewable.

Read-registry precedence is `--registry`, `$ANVIL_SERVING_HOME/serve-recipes.toml`
(or `~/.anvil-serving/serve-recipes.toml`), a source checkout's immutable
`./configs/serve-recipes.toml`, then the packaged registry. The latter two are
portable product catalogs for discovery, not operator promotion state. Create,
update, and delete require an explicit registry or resolve to the private
operator home; they never mutate the public checkout or packaged catalog by
default. A selector may be either a recipe's exact model identifier or its
unique basename.

### Discover recipes

```bash
anvil-serving models recipes list
anvil-serving models recipes list --registry configs/serve-recipes.toml
anvil-serving models recipes show MODEL
anvil-serving models recipes show MODEL --registry configs/serve-recipes.toml
```

Start with `list`; its `activates` column identifies recipes that can replace a deployed
role such as `heavy`. Use `show` before a mutation or load to verify the resolved engine,
quantization, context, runtime settings, activation plan, direction, Compose service,
and exact `serves switch ... --dry-run` preview command.

### Create, update, or delete a recipe

The input file must contain exactly one `[[recipe]]` block.

```toml
[[recipe]]
model = "org/model"
status = "unverified"

[recipe.serve]
engine = "vllm"
image = "vllm/vllm-openai:nightly"
port = 30123
flags = ["--served-model-name org/model"]
```

Start with the fields above, then use `models recipes show` on a similar shipped
recipe for engine-specific GPU, environment, volume, context, and quantization fields.
The full registry schema is represented by `configs/serve-recipes.toml`.

Most vLLM images accept the model as a positional argument through their image
entrypoint. For an image with a different API-server entrypoint, set `entrypoint`
to its argv and set `model_flag` to the single option that introduces the model id:

```toml
[recipe.serve]
image = "nvcr.io/nvidia/vllm:26.06-py3"
entrypoint = ["python3", "-m", "vllm.entrypoints.openai.api_server"]
model_flag = "--model"
```

`entrypoint[0]` becomes Docker's `--entrypoint`; the remaining entries are passed
after the image. Without these optional fields, recipe loading keeps the existing
positional-model behavior.

For an environment-owned launcher that selects the model itself, set `model_env`
instead of `model_flag`. Anvil injects that variable from the recipe's exact
`model` identity and passes no positional model argument to the launcher. A
declared `env` entry cannot override it.

```toml
[recipe.serve]
image = "vendor/runtime@sha256:..."
entrypoint = ["/usr/local/bin/serve-model.sh"]
model_env = "MODEL"
```

Persistent engine/JIT data uses auxiliary named volumes, never host bind mounts:

```toml
[recipe.serve]
named_volumes = [
  "candidate-jit:/cache",
  "candidate-reference:/opt/reference:ro",
]
```

Each entry is `NAME:/absolute/container/path[:ro]`. Sources must be Docker volume
names; targets must be normalized absolute POSIX paths; repeated sources or
targets and attempts to shadow `/root/.cache/huggingface` fail closed. The model
cache remains owned by `recipe.download.volume`.

```bash
anvil-serving models recipes create --recipe-file ./candidate-recipe.toml --registry ./serve-recipes.local.toml --dry-run
anvil-serving models recipes create --recipe-file ./candidate-recipe.toml --registry ./serve-recipes.local.toml --confirm
```

```bash
anvil-serving models recipes update MODEL --recipe-file ./candidate-recipe.toml --registry ./serve-recipes.local.toml --dry-run
anvil-serving models recipes update MODEL --recipe-file ./candidate-recipe.toml --registry ./serve-recipes.local.toml --confirm
anvil-serving models recipes delete MODEL --registry ./serve-recipes.local.toml --dry-run
anvil-serving models recipes delete MODEL --registry ./serve-recipes.local.toml --confirm
```

Their previews include the resolved registry and source digests, complete proposed
recipe TOML, ordered write actions, deferred gate, and manual recovery path. Create, update,
and delete use atomic writes and numbered backups. A mutation never overwrites the
packaged registry implicitly.

### Load a recipe

```bash
anvil-serving models recipes load MODEL --container my-candidate --registry ./serve-recipes.local.toml --dry-run
anvil-serving models recipes load MODEL --container my-candidate --registry ./serve-recipes.local.toml --confirm
```

`load` starts a new, explicitly named Docker container bound to loopback. It does not
change router policy or promote the candidate. Validate it with
[`eval preflight`](eval.md#preflight), then use [`serves switch`](serves.md#switch-primary-by-recipe)
only after human review. The preview's cleanup command is conditional: use it only for a
container successfully created by that load, never for a name that existed beforehand.

When machine-level cache reclaim is enabled, the preview also declares that `load`
will wait up to 600 seconds for the recipe's HTTP health after the container starts.
Only then does it evaluate the cache threshold, fixed 1 GiB growth gate, and settled-I/O
gate. A readiness timeout skips reclaim and leaves the container running; it does not
change the successful load's exit code. Configure the default-off policy in
[`host.toml`](../CONFIGURATION.md#machine-policy-hosttoml).

### Operate a loaded recipe

`load` labels the candidate with its exact recipe model and revision. Status,
logs, and unload verify those ownership labels before acting, so a mistyped
container name cannot silently target an unrelated workload.

```bash
anvil-serving models recipes status MODEL --container my-candidate --registry ./serve-recipes.local.toml
anvil-serving models recipes logs MODEL --container my-candidate --registry ./serve-recipes.local.toml --tail 200
anvil-serving models recipes unload MODEL --container my-candidate --registry ./serve-recipes.local.toml --dry-run
anvil-serving models recipes unload MODEL --container my-candidate --registry ./serve-recipes.local.toml --confirm
```

Use these commands for isolated benchmark candidates. Use `serves status`,
`serves logs`, and `serves down` for manifest-owned deployments. Do not use raw
Docker as the normal candidate lifecycle path.

## Model scoring

```bash
anvil-serving models score --help
```

Scoring ranks models from retained benchmark evidence. It does not auto-promote a
recipe or modify router policy.

## Cache remove

Use the exact-removal verb when one known repository revision—not a policy
selected set—is the target:

```bash
anvil-serving models cache remove OWNER/REPO --revision COMMIT --dry-run
anvil-serving models cache remove OWNER/REPO --revision COMMIT --confirm
```

The selector requires both an exact `OWNER/REPO` and revision. Preview reports
the matching snapshot and bytes that become unreferenced; apply removes only
that snapshot, collects only blobs no remaining snapshot references, and
verifies the target snapshot is absent. It does not approximate repository
identity with a substring or wildcard.

## Cache inventory

Capture a read-only, machine-readable inventory before and after storage work:

```bash
anvil-serving models cache inventory
anvil-serving models cache inventory --output ./cache-inventory.json
```

The `model-cache-inventory/v1` result includes filesystem capacity, used, and
available bytes; cached repositories, revisions, snapshots, logical and
incomplete bytes, and modification timestamps; plus Docker image, container,
volume, and build-cache accounting. `--volume` and `--image` select the named
Hugging Face cache volume and inspection image. The volume is mounted read-only,
and `--output` uses an atomic replacement after requiring an existing parent
directory.

Modification, creation, and Docker last-used timestamps are observations for
inventory and cleanup planning. They are not proof that a model was actually
served or benchmarked. Use retained benchmark evidence and protected runtime
state for qualification and deletion decisions. Agents can request the same
read-only report through the `model_cache_inventory` MCP tool.

## Cache prune

Always inspect the plan before deleting cached artifacts:

```bash
anvil-serving models cache prune --dry-run
anvil-serving models cache prune --mixture MODEL --execute --confirm
```

The command preserves the ownership and reservation rules represented by the current
serve configuration. A metadata-only hardware caveat is never enough to label a model
safe to delete: default deletion requires explicit current-host `dead_everywhere`
evidence. `--include-servable` deliberately widens the deletion set; combining it with
an empty protected mixture also requires `--allow-empty-mixture`.
The removed `--yes` consent spelling is rejected with guidance to use the shared
`--confirm` flag.

For a structured read-only plan through the agent/controller surface, use the
`cache_prune_plan` MCP tool. CLI `--json` is the standard result envelope, not a second
handler-specific JSON format. Safety refusals and undeleted candidates are preserved in
that envelope's error message. Preview lists the resolved scan roots, ordered apply
actions, rescan drift, and the irreversible/no-automatic-rollback boundary.

## Related references

- Shipped recipe registry: `configs/serve-recipes.toml`
- [Model serves](serves.md)
- [Evaluation & benchmarks](eval.md)
- [Benchmarks](../BENCHMARKS.md)
