# Model serves

[CLI overview](../CLI.md) · [Router](router.md) · [Models & recipes](models.md)

The `serves` family owns local model-server definitions and lifecycle. A serve is
manifest-owned; a recipe is a reusable model-and-engine configuration managed under
[`models recipes`](models.md#recipes).

## Commands

| Command | Purpose |
| --- | --- |
| `serves render` | Render a model serve definition. |
| `serves up` | Start manifest-owned serves. |
| `serves down` | Stop manifest-owned serves. |
| `serves rm` | Remove a manifest-owned serve. |
| `serves adopt` | Adopt an existing serve into manifest ownership. |
| `serves promote` | Preflight and promote a staged recipe with rollback. |
| `serves status` | Show bounded serve status. |
| `serves groups` | List serve groups and their members. |
| `serves logs` | Read bounded serve logs. |
| `serves multiplex` | Run the single-resident model multiplexer. |

## Select manifests and groups

Serve lifecycle commands operate on the configured manifest set. Use `--group NAME`
to target every serve with the matching tag; repeat the option for multiple groups.
`--group all` selects every serve in the set.

```bash
anvil-serving serves groups
anvil-serving serves status --group ocr
anvil-serving serves status --group all
```

Groups provide the supported way to turn an optional workload such as OCR on or off
without inventing a separate lifecycle script.

## Start and stop serves

```bash
anvil-serving serves up --group ocr --dry-run
anvil-serving serves up --group ocr --confirm
anvil-serving serves down --group ocr --confirm
anvil-serving serves groups
anvil-serving serves logs OCR_SERVE_NAME
```

Only manifest-owned resources are mutated. Destructive leaves require confirmation,
and `down` does not imply removal.

## Render and adopt

```bash
anvil-serving serves render --help
anvil-serving serves adopt --dry-run
anvil-serving serves adopt --confirm
```

`render` produces a reviewable serve definition. `adopt` brings an already-running
serve under the same ownership contract; it does not silently claim arbitrary
containers.

## Promote a recipe

```bash
anvil-serving serves promote PROMOTION_PLAN --dry-run
anvil-serving serves promote PROMOTION_PLAN --confirm
```

`PROMOTION_PLAN` names a `[[promotion]]` entry in the selected serves manifest.
Promotion stages the candidate, runs preflight, and preserves a rollback path. It is
separate from [`models recipes load`](models.md#load-a-recipe), which starts a named
local container but never promotes router policy.

## Multiplexing

```bash
anvil-serving serves multiplex --help
```

The multiplexer coordinates a single-resident model workload. Use it only where the
deployment topology assigns that model workload to the current host.

## Related references

- [Serves & eval](../SERVES-AND-EVAL.md)
- [Models & recipes](models.md)
- [Evaluation & benchmarks](eval.md)
- [Operator playbooks](../OPERATOR-PLAYBOOKS.md)
