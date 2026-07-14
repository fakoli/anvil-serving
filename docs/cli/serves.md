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
| `serves switch` | Switch a deployment role to an activation-ready recipe. |
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

## Switch Heavy by recipe

For the common model-selection path, choose the deployment role and recipe directly:

```bash
anvil-serving serves switch heavy
anvil-serving serves switch heavy ThinkingCap-Qwen3.6-27B-FP8 --dry-run
anvil-serving serves switch heavy ThinkingCap-Qwen3.6-27B-FP8 --confirm
anvil-serving serves switch heavy gpt-oss-120b --confirm
```

With no `MODEL`, the command lists the resolved registry path and marks each declared
choice `ready` or `blocked` after validating its plan and effective Compose service; listing
does not prompt for confirmation. `switch` accepts a full model id or an unambiguous basename
as the second positional argument. The older `--recipe MODEL` spelling remains supported for
compatibility. It only accepts recipes
with a matching `[recipe.activation.ROLE]` entry, and verifies that the recipe's managed
serve and served-model identity match the referenced promotion plan before any mutation.
The existing promotion transaction still owns quiesce, drain, preflight, router update,
and automatic rollback. Before apply, `switch` resolves the effective Compose service,
binds it to the recipe's image/model/revision/flags/environment/GPU/port, compares the
Compose service hash and live container contract, snapshots all router artifacts into the
operation directory, compares the deployed router config and profile with the expected source
state, and takes exclusive role and promotion locks. A matching active target is a no-op. Each real switch writes a durable operation
journal and fresh gate evidence under the operator config directory instead of overwriting
dated findings. A normal registry row is intentionally not enough to alter a live routing
tier; add a reviewed activation mapping and promotion plan first. Controller and SSH
transport parity remain tracked follow-up work; run this command on the resource owner.

## Advanced: promote a plan

For lower-level plan operation and recovery:

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
