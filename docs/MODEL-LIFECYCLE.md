# Model catalog, artifacts, and recipes

The `models` family manages what is *available* to serve: the catalog of known models, the
downloaded artifacts on disk, and the recipes that record how to run them. It is deliberately
separate from `serves`, which manages what is *running*, and from the router, which decides what
callers can reach.

Nothing in this family promotes a model or changes routing.

| Task | Command |
| --- | --- |
| Refresh the model catalog | `models sync` |
| Download a pinned artifact | `models pull` |
| Record and reuse known-good configurations | `models recipes …` |
| Reclaim disk | `models cache prune`, `models cache remove` |
| Rank candidates from recorded evidence | `models score` |

## Catalog

`models sync` rebuilds the catalog of known models:

```bash
anvil-serving models sync --dry-run
anvil-serving models sync --confirm
```

Apply builds a complete staged catalog and swaps it atomically with numbered backups, so a
removed model cannot survive as a stale card. If a worker errors mid-sync, the active catalog is
left untouched rather than partially rewritten.

## Artifacts

`models pull` downloads one pinned artifact:

```bash
anvil-serving models pull <repo-id> --dry-run
anvil-serving models pull <repo-id> --confirm
```

Downloads default to a named Docker volume rather than a host bind mount — on Windows and WSL2 a
`C:/` bind mount is dramatically slower for large weight files. Credentials are passed as
environment-variable *names*, never literal tokens.

Free space is verified before the download starts. There is no automatic rollback of bytes
already written: an interrupted pull leaves a partial artifact for you to inspect or remove
deliberately.

**A cached artifact is not a serve.** Pulling weights makes a model available locally; it does
not start anything, qualify anything, or make the model reachable through the gateway.

## Recipes

A recipe is the complete known-working configuration for one model *and* its engine — image,
flags, quantization, context, and the settings that made it work. The engine is part of the
recipe, not an implementation detail: the same checkpoint on a different engine build is a
different result and needs its own qualification.

```bash
anvil-serving models recipes list
anvil-serving models recipes show <model>
```

The packaged registry that ships with the product is immutable. Mutations require an explicit
operator-owned registry path, and are written atomically with backups:

```bash
anvil-serving models recipes create --registry <registry.toml> --recipe-file <recipe.toml> --dry-run
anvil-serving models recipes update <model> --registry <registry.toml> --recipe-file <recipe.toml> --confirm
anvil-serving models recipes delete <model> --registry <registry.toml> --confirm
```

`--registry` is required for all three: there is no implicit default that could let a mutation
land in the packaged registry by omission. `update` and `delete` also take the target `<model>`
as a positional — the model id or an unambiguous basename.

`models recipes load` starts a candidate container from a recipe:

```bash
anvil-serving models recipes load <model> --container <name> --dry-run
anvil-serving models recipes load <model> --container <name> --confirm
```

`--container` is required and names the new Docker container, so a loaded candidate can never
collide with a managed serve. The candidate binds to loopback and changes no router policy. This is the intended way to try a
model without touching production: load it, qualify it with `eval preflight`, and only then
consider [promotion](MODEL-PROMOTION.md).

## Cache

`models cache prune` plans or performs reclamation:

```bash
anvil-serving models cache prune --dry-run
anvil-serving models cache prune --execute --confirm
```

`models cache remove` deletes one exact repository revision:

```bash
anvil-serving models cache remove <repo-id> --revision <sha> --dry-run
anvil-serving models cache remove <repo-id> --revision <sha> --confirm
```

Deletion requires explicit current-host evidence that the artifact is dead everywhere it is
referenced. A metadata-only hardware caveat is never sufficient, and widening the blast radius
requires an explicit flag rather than being inferred.

Before reclaiming anything, check that nothing you rely on is a rollback target. A pinned
rollback whose weights were pruned is no longer a rollback — see
[Promote and roll back](MODEL-PROMOTION.md).

## Ranking candidates

`models score` ranks models from retained benchmark evidence:

```bash
anvil-serving models score
```

It reads recorded evidence and orders candidates. It never promotes a recipe, edits router
policy, or writes back a verdict. Treat its output as an input to a human decision.

## Related

- [Serve lifecycle](cli/serves.md) — starting, probing, and stopping serves.
- [Models & recipes commands](cli/models.md) — full flag-level reference.
- [Promote and roll back](MODEL-PROMOTION.md) — the guarded transaction.
- [Model settings example](MODEL-SETTINGS-EXAMPLE.md) — worked per-model tuning.
- [Model dossiers](benchmarks/models/index.md) — status and recipe per evaluated model.
