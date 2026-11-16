# Serve recipes

A serve recipe records exactly how a model was served: weights and revision,
engine image, quantization, context, launch flags, the hardware it was written
for, and where it has been measured. Every recipe this project publishes is
listed here with a page and a download.

<!-- recipe-catalog:summary -->

## Use a downloaded recipe

Each file is a complete recipe registry, so the CLI reads it directly:

```bash
anvil-serving models recipes list --registry ./recipe-file.toml
anvil-serving models recipes load MODEL --container my-serve --registry ./recipe-file.toml --dry-run
```

Review the dry run, then repeat `load` with `--confirm`. Loading starts a
loopback-only container and changes no route. Qualify the endpoint with
`eval preflight` before you depend on it, and see
[model lifecycle](../MODEL-LIFECYCLE.md) to copy a recipe into your own
registry.

!!! warning "Recipes are starting points, not promotions"
    Status values and notes describe the recipe when it was written. A recipe
    with a linked finding was measured on the hardware named in that finding;
    your hardware, drivers, and images may differ. Replace placeholder GPU
    UUIDs with the values `anvil-serving host gpus` reports.

## All recipes

Grouped by the hardware each recipe was written for. *Findings* counts the
benchmark findings that measured the same served model; open a recipe to see
them. For measured results ranked by workload, see
[find a measured recipe](../benchmarks/recipe-results.md) and
[benchmark results](../benchmarks/results.md).

<!-- recipe-catalog:table -->
