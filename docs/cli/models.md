# Models & recipes

[CLI overview](../CLI.md) · [Model serves](serves.md) · [Evaluation & benchmarks](eval.md)

The `models` family manages four related resources: the local model catalog,
downloaded artifacts, reusable serve recipes, and cache storage. Use recipes to move
from a known working engine configuration to a candidate container; use
[`serves switch`](serves.md#switch-heavy-by-recipe) when the candidate is ready to
replace a deployed role.

## Choose a workflow

| Goal | Start here | Then |
| --- | --- | --- |
| See models already on this host | `models sync --dry-run` | Apply with `--confirm`, then inspect `model-library/INDEX.md`. |
| Download a Hugging Face model | `models pull REPO --dry-run` | Apply with `--confirm`. |
| Find a known working serve configuration | `models recipes list` | Inspect it with `models recipes show MODEL`. |
| Replace the deployed Heavy recipe | `models recipes list` | Choose a row that activates `heavy`, inspect it, then preview `serves switch heavy --recipe MODEL --dry-run`. |
| Add or revise an operator recipe | `models recipes create|update ... --dry-run` | Apply with `--confirm`; retain the numbered backup. |
| Start a candidate without changing routing | `models recipes load MODEL --container NAME --dry-run` | Apply, run `eval preflight`, then review a `serves switch`. |
| Reclaim cache space | `models cache prune --dry-run` | Add `--execute --confirm` only after reviewing the protected mixture. |

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
fact that downloaded bytes have no automatic rollback.

## Recipes

A serve recipe records a working model-and-engine configuration independently from a
running container. The shipped registry is useful for discovery. Mutations require an
explicit operator-owned registry path, which keeps packaged defaults immutable and
reviewable.

Read-registry precedence is `--registry`, `./configs/serve-recipes.toml`,
`$ANVIL_SERVING_HOME/serve-recipes.toml` (or `~/.anvil-serving/serve-recipes.toml`),
then the packaged registry. A selector may be either a recipe's exact model identifier
or its unique basename.

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
[`eval preflight`](eval.md#preflight), then use [`serves switch`](serves.md#switch-heavy-by-recipe)
only after human review. The preview's cleanup command is conditional: use it only for a
container successfully created by that load, never for a name that existed beforehand.

## Model scoring

```bash
anvil-serving models score --help
```

Scoring ranks models from retained benchmark evidence. It does not auto-promote a
recipe or modify router policy.

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
