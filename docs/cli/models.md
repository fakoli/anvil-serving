# Models & recipes

[CLI overview](../CLI.md) · [Model serves](serves.md) · [Evaluation & benchmarks](eval.md)

The `models` family manages the model catalog, downloaded artifacts, reusable serve
recipes, and benchmark-based model ranking. Recipe discovery and full CRUD are first-
class commands under `models recipes`.

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

## Recipes

A serve recipe records a working model-and-engine configuration independently from a
running container. The shipped registry is useful for discovery. Mutations require an
explicit operator-owned registry path, which keeps packaged defaults immutable and
reviewable.

The default read registry is `configs/serve-recipes.toml`. A selector may be either a
recipe's exact model identifier or its unique basename.

### Discover recipes

```bash
anvil-serving models recipes list
anvil-serving models recipes list --registry configs/serve-recipes.toml
anvil-serving models recipes show MODEL
anvil-serving models recipes show MODEL --registry configs/serve-recipes.toml
```

Start with `list`; use `show` before a mutation or load to verify the resolved engine,
quantization, context, and runtime settings.

### Create a recipe

The input file must contain exactly one `[[recipe]]` block.

```bash
anvil-serving models recipes create --recipe-file ./candidate-recipe.toml --registry ./serve-recipes.local.toml --dry-run
anvil-serving models recipes create --recipe-file ./candidate-recipe.toml --registry ./serve-recipes.local.toml --confirm
```

### Update or delete a recipe

```bash
anvil-serving models recipes update MODEL --recipe-file ./candidate-recipe.toml --registry ./serve-recipes.local.toml --dry-run
anvil-serving models recipes update MODEL --recipe-file ./candidate-recipe.toml --registry ./serve-recipes.local.toml --confirm
anvil-serving models recipes delete MODEL --registry ./serve-recipes.local.toml --dry-run
anvil-serving models recipes delete MODEL --registry ./serve-recipes.local.toml --confirm
```

Create, update, and delete use atomic writes and numbered backups. A mutation never
overwrites the packaged registry implicitly.

### Load a recipe

```bash
anvil-serving models recipes load MODEL --container my-candidate --registry ./serve-recipes.local.toml --dry-run
anvil-serving models recipes load MODEL --container my-candidate --registry ./serve-recipes.local.toml --confirm
```

`load` starts a new, explicitly named Docker container bound to loopback. It does not
change router policy or promote the candidate. Validate it with
[`eval preflight`](eval.md#preflight), then use [`serves promote`](serves.md#promote-a-recipe)
only after human review.

## Catalog and artifacts

```bash
anvil-serving models sync --help
anvil-serving models pull --help
```

`sync` refreshes catalog metadata. `pull` obtains a selected artifact through the
configured model source; inspect focused help for source- and model-specific options.

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
anvil-serving models cache prune --execute --yes --confirm
```

The command preserves the ownership and reservation rules represented by the current
serve configuration. `--include-servable` deliberately widens the deletion set; combining
it with an empty protected mixture also requires `--allow-empty-mixture`.

## Related references

- Shipped recipe registry: `configs/serve-recipes.toml`
- [Model serves](serves.md)
- [Evaluation & benchmarks](eval.md)
- [Benchmarks](../BENCHMARKS.md)
