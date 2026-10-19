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
| Discover recipe-loaded containers | `models recipes running` | Select an unambiguous model or exact container for status, logs, or unload. |
| Replace the deployed Primary recipe | `models recipes list` | Choose a row that activates `primary`, inspect it, then preview `serves switch primary MODEL --dry-run`. |
| Add or revise an operator recipe | `models recipes create|update ... --dry-run` | Apply with `--confirm`; retain the numbered backup. |
| Start a candidate without changing routing | `models recipes load MODEL --container NAME --dry-run` | Apply, run `eval preflight`, then review a `serves switch`. |
| Remove one exact cached revision | `models cache remove OWNER/REPO --revision COMMIT --cache-dir /srv/model-cache --dry-run` | Apply with `--config-home /srv/operator --confirm` after reviewing the snapshot and reclaimable blobs. |
| Retire an entire unused model cache | `models cache discard --volume retired-model-cache --config-home /srv/operator --dry-run` | Review the fingerprint and complete inventory, stop writers, then confirm discard. |
| Reclaim cache space broadly | `models cache prune --dry-run` | Add `--execute --confirm` only after reviewing the protected mixture. |

## Commands

| Command | Purpose |
| --- | --- |
| `models sync` | Synchronize the model catalog. |
| `models pull` | Pull a model artifact. |
| `models score` | Rank models from benchmark evidence. |
| `models recipes list` | List recorded serve recipes. |
| `models recipes show` | Show one recipe. |
| `models recipes running` | Discover label-owned recipe containers without registry dependence. |
| `models recipes create` | Add one recipe to an operator registry. |
| `models recipes update` | Replace one selected recipe. |
| `models recipes delete` | Delete one selected recipe. |
| `models recipes load` | Start a named local container from one recipe. |
| `models recipes status` | Inspect one exact recipe-loaded candidate container. |
| `models recipes logs` | Read bounded logs from one exact recipe-loaded candidate container. |
| `models recipes unload` | Remove one exact recipe-loaded candidate container. |
| `models cache inventory` | Record a read-only model-cache and Docker storage inventory. |
| `models cache remove` | Plan or remove one exact cached repository revision. |
| `models cache discard` | Audit and discard one entire retired model cache. |
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

`pull` downloads a Hugging Face repository into a named Docker volume, or into
an explicit native cache with `--cache-dir`. It never
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

For a bounded Docker downloader, provide both ceilings in MiB. Docker receives
their exact byte values as `--memory` and `--memory-swap`; set both to the same
value to request no extra host swap for the container.

```bash
anvil-serving models pull OWNER/REPO --memory-mib 2048 --memory-swap-mib 2048 --dry-run
```

These are requested Docker configuration limits, retained in the CLI JSON
envelope's token-free plan output and exact Docker argv. They do not report
measured cgroup values. Omitting both flags preserves the legacy unbounded
downloader behavior. Native `--cache-dir` pulls reject the Docker-only flags.

For one public LFS artifact on Windows, `--native-stage` uses the installed
`curl.exe` to resume into a declared host directory, verifies the immutable
LFS SHA-256, and imports through a read-only bind into the Docker cache volume:

```powershell
anvil-serving models pull OWNER/REPO --revision 40_HEX_COMMIT --include weights.ninfer --native-stage C:/model-stage --no-token --dry-run
```

Apply the reviewed command with `--confirm` in place of `--dry-run`. This path
requires one literal file path, a full lowercase commit revision, and
`--no-token`; it does not read Hugging Face credentials. Confirmed apply checks
both staging and volume capacity, rehashes the imported bytes, and verifies the
snapshot. Preview performs no metadata/storage probes or writes. The import
container has no network. `--cache-dir`, `--exclude`, and Docker memory flags
are incompatible with staging. A failed transfer retains staged bytes for
resumption; imported cache bytes have no automatic rollback. It does not start
a model or change routing.

For native macOS MLX artifacts or another installed native engine, select the
exact repository and immutable 40-character lowercase commit:

```bash
anvil-serving models pull OWNER/REPO --revision 40_HEX_COMMIT \
  --cache-dir /operator/model-cache/hub --no-token --dry-run
anvil-serving models pull OWNER/REPO --revision 40_HEX_COMMIT \
  --cache-dir /operator/model-cache/hub --no-token --confirm --json
```

For a public repository whose files require Xet, add `--xet`. This explicit
native-only opt-in keeps Xet scratch under the declared cache filesystem for
the one writer-held download and removes that owned scratch afterward. HTTP is
the default transport.

Native pulls discover an already installed `hf` executable on `PATH`; use
`--hf-executable /operator/engine/bin/hf` to select one explicitly. Anvil installs
no downloader dependency. Pass either the direct hub-cache path or an existing
Hugging Face home containing `hub/`; the resolved hub path is passed to
`hf download --cache-dir`. Native pulls reject `--volume`, `--image`, mutable
revisions, and the Docker-only `--expected-bytes` metadata override.

The native preview reads public metadata for that exact commit and applies
`--include`/`--exclude` to its complete file inventory. It checks every selected
file's declared size and public LFS SHA-256 or Git blob hash, credits only
hash-verified existing selected bytes, and requires missing bytes plus
`--headroom-gib` on the target filesystem. Existing partial downloads receive
no speculative disk credit and remain available to `hf` for resumption. Preview
performs no writes, secret-source reads, downloads, Docker operations, or
serving lifecycle actions. Authenticated metadata resolution is deferred to
confirmed apply, so previews require publicly accessible metadata.

Apply holds one nonblocking writer lock per repository and resolved cache,
rechecks storage, and invokes the selected executable directly without a shell.
On POSIX the downloader inherits the lock so an abruptly exiting parent cannot
release it while its downloader remains alive. HTTP pulls disable Xet. An
explicit `--xet` pull uses private cache-local scratch and disables shared blobs;
its receipt records the selected transport. The configured Xet shard cache limit
is a soft upload-cache setting, not a download-space bound. `--no-token` strips inherited Hugging Face token variables and disables
implicit use of a saved login. Other pulls resolve only the declared secret
reference; native output never prints its variable name, dotenv path, or value.

After `hf` succeeds, independent verification requires every selected file at
`models--OWNER--REPO/snapshots/COMMIT`, checks its actual bytes against the
public hashes, and refuses unsafe links, missing files, or selected incomplete
artifacts. Unrelated cache contents are retained. Corrupt completed artifacts
fail closed and remain available for diagnosis. Repeating the same command
resumes the download; there is no automatic pruning or rollback.

The native `--json` envelope contains a `native-model-pull/v1` report in `data`,
including the exact inventory, storage admission, immutable snapshot path,
downloader status, and per-file verification. Every started apply retains a
JSON receipt outside the hub cache, under its parent's
`.anvil-serving/model-pulls/` directory. Use
`--evidence-out /operator/evidence/native-pull.json` to retain the report at a
new explicit file instead. Existing evidence files are refused rather than
overwritten, and failed or interrupted downloads retain their cache bytes and
started receipt for recovery.

## Recipes

A serve recipe records a working model-and-engine configuration independently from a
running container. The shipped registry is useful for discovery. Mutations require an
explicit operator-owned registry path, which keeps packaged defaults immutable and
reviewable.

Read-registry precedence is `--registry`, `$ANVIL_SERVING_HOME/serve-recipes.toml`
(or `~/.anvil-serving/serve-recipes.toml`), a source checkout's immutable
`./configs/serve-recipes.toml`, then the packaged registry. The latter two are
portable product catalogs for discovery, not operator promotion state. Create,
update, and delete require an explicit operator-owned registry path; normally
pass `$ANVIL_SERVING_HOME/serve-recipes.toml`. They never mutate the public
checkout or packaged catalog implicitly. A selector may be either a recipe's
exact model identifier or its unique basename.

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

For an unqualified loader, declare explicit host bounds in `[recipe.serve]`:

```toml
memory_limit_mib = 61440
memory_swap_limit_mib = 61440
host_memory_reserve_mib = 16384
```

The first value caps container RAM; the second caps RAM plus swap, so equal
values forbid additional swap. All three fields are required together. Values
must be positive integer MiB; the reserve must be at least 1024 MiB. Choose
bounds for the actual host: the example is not a model-fit claim. A bounded
load requires local Linux Docker with cgroup v2 memory/swap enforcement and
sufficient available RAM and free swap at admission. The host reserve is an
admission check, not a reservation against unrelated workloads. Existing recipes
without these fields retain their previous behavior.

For zero swap, the Docker adapter sends RAM plus one byte as its wire total.
This keeps systemd's `MemorySwapMax` property explicit; Linux cgroup v2 rounds
the byte down to zero pages. This encoding is not a one-byte usable allowance.
Bounded loads verify the exact created container's effective RAM and swap limits
before returning success, including loads without an HTTP port. Missing,
unlimited, or mismatched limits fail closed and stop only that newly created ID;
the stopped container and its logs remain available for managed logs/unload.
HTTP readiness polling rechecks containment. Status reports `containment_error`
and exits nonzero on drift without changing the container; logs and unload remain
available. Existing positive swap allowances keep their exact wire values.

`models recipes status` and `running --json` include `host_memory`: Docker limits,
effective cgroup RAM/swap limits, current/peak usage, OOM events and exit state.
Confirm effective limits before trusting a new loader. Windows uses read-only
cgroup files in the local Docker Desktop WSL VM, selected by the exact container
ID, after validating the local endpoint and matching VM identity. Default
cgroupfs/systemd layouts are supported; remote endpoints, custom cgroup parents,
unknown drivers, missing files, malformed data and timed-out probes leave live
counters null. Nothing is executed inside the model container.

Capture peak/event observations while the container is running. Counters
unavailable after container exit are null; Docker OOM/exit state remains available.
An unlimited cgroup (`max`) has no numeric effective limit and is also represented
by null. These limits do not cap GPU VRAM or prove that the model fits. Keep
restoration separate.

When machine-level cache reclaim is enabled, the preview also declares that `load`
will wait up to 600 seconds for the recipe's HTTP health after the container starts.
Only then does it evaluate the cache threshold, fixed 1 GiB growth gate, and settled-I/O
gate. A readiness failure skips reclaim and returns a nonzero load exit code.
The container remains available for owned `models recipes logs` and `unload` recovery.
Configure the default-off policy in
[`host.toml`](../CONFIGURATION.md#machine-policy-hosttoml).

### Operate a loaded recipe

`load` labels the candidate with its exact recipe model, revision, canonical
recipe digest, source-registry digest, and native-offload classification.
Legacy containers that predate the digest labels remain discoverable from the
required Anvil ownership and model labels. Status, logs, and unload recheck the
full immutable Docker container ID before acting; logs and removal use that ID,
not the reusable name. A mistyped name, reused name, or same-name replacement
therefore fails closed instead of silently targeting another container.

```bash
anvil-serving models recipes running
anvil-serving models recipes running --json
anvil-serving models recipes status MODEL
anvil-serving models recipes status MODEL --container my-candidate --registry ./serve-recipes.local.toml
anvil-serving models recipes logs MODEL --container my-candidate --registry ./serve-recipes.local.toml --tail 200
anvil-serving models recipes logs MODEL --container my-candidate --tail 500 --contains ERROR --contains "KV cache"
anvil-serving models recipes unload MODEL --container my-candidate --registry ./serve-recipes.local.toml --dry-run
anvil-serving models recipes unload MODEL --container my-candidate --registry ./serve-recipes.local.toml --confirm
```

`running` asks Docker only for containers carrying the exact
`io.anvil-serving.managed-by=models-recipes` label, then emits a bounded typed
inventory: container name and immutable ID, model/revision, available recipe
and registry digests, image digest, served identity, bound port, GPU selection,
state, and health. It does not return environment values, raw launch commands,
credentials, or endpoint addresses. Exited retained candidates are included.
Missing or malformed identities and non-Anvil containers are excluded.

When one discovered container matches, `status`, `logs`, and `unload` no longer
require its name to be recovered out of band or its recipe to remain in the
currently selected registry. Two matching containers fail closed and require
an exact `--container`. An unload without a current recipe treats a missing
native-offload label conservatively and invokes the existing twice-verified,
exact-path shared-memory cleanup guard after removal.

`--contains` is a case-insensitive literal filter and may be repeated; a line
is emitted when it contains any selected literal. Filtering happens after the
exact recipe/container ownership check and preserves stdout/stderr separation.
Use it to inspect actionable startup signatures without returning every model
load progress line. Remove the filter when the complete bounded tail is needed.

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

Use the exact-removal verb for one known repository revision in a directly
accessible native Hugging Face cache:

```bash
anvil-serving models cache remove OWNER/REPO --revision 40_HEX_COMMIT --cache-dir /srv/model-cache --dry-run
anvil-serving models cache remove OWNER/REPO --revision 40_HEX_COMMIT --cache-dir /srv/model-cache --config-home /srv/operator --confirm
```

The selector requires both an exact `OWNER/REPO` and revision. Preview reports
the matching snapshot and bytes that become unreferenced; apply removes only
that snapshot, collects only blobs no remaining snapshot references, and
verifies the target snapshot is absent. It does not approximate repository
identity with a substring or wildcard.

**Behavior change:** legacy Docker revision removal without `--cache-dir` or
`--discard-volume` refuses `--confirm`, including dispatcher authorization,
before contacting Docker. That helper cannot audit ownership or fence writers.
Its `--dry-run` remains a bounded read-only preview of an existing volume; the
helper has no deletion action. The preview binds the inspected daemon path
read-only, so a missing volume cannot be created by cleanup preview. Artifact
acquisition preflight retains its existing named-volume creation behavior.

Use [whole-cache discard](#whole-cache-discard) when every asset in a cache is
retired, or use native exact removal when the cache is directly accessible.
Docker Desktop and remote-daemon deletion must run on an owner with direct
access to the daemon's cache storage; there is no destructive helper fallback.

Native Hugging Face caches take an explicit local cache root and only a
40-character lowercase commit revision. Preview is filesystem-only:

```bash
anvil-serving models cache remove OWNER/REPO --revision 40_HEX_COMMIT \
  --cache-dir /operator/model-cache/hub --dry-run
```

Apply requires `--confirm` and the authoritative operator configuration home:

```bash
anvil-serving models cache remove OWNER/REPO --revision 40_HEX_COMMIT \
  --cache-dir /operator/model-cache/hub --config-home /operator/home --confirm
```

Before deletion, the command audits running and stopped Docker bind mounts and
current configuration references, rejects unresolved values and unsafe paths,
then re-plans. It unlinks only the selected snapshot's links, refs, and
exclusive blobs through no-follow directory descriptors, preserving other
snapshots and shared blobs before verifying the snapshot is absent.
Live configuration cannot delegate through the root `recovered-configs` archive;
restore that manifest into an authoritative current location before attempting
native cache removal.
It refuses observed cache drift and active Hugging Face locks. Released Hugging
Face lock paths persist, so it checks and retains their advisory locks through
the deletion rather than treating a lock pathname as active. A writer that does
not cooperate with those cache locks can still race filesystem metadata, so
stop external cache writers before applying the reviewed command.

An operator can declare one or more exact 64-character container IDs as
non-model read-only root observations with `--non-model-root-view CONTAINER_ID`.
Native revision removal uses this assertion during confirmed apply; dedicated
volume removal also validates it during preview. For each declared ID,
the current inventory must show an exact read-only bind from `/` to an absolute
container destination and no GPU device requests. It permits only that one root
observation; another overlapping mount, a mutable root view, an unknown ID, or
a GPU request still blocks removal. The declaration records the caller's
observation purpose and does not classify or otherwise exempt containers.

### Discard a dedicated Docker model cache

Use `--discard-volume` for a retired model whose global shared-blob cache belongs
entirely to one repository revision:

```bash
anvil-serving models cache remove OWNER/REPO --revision 40_HEX_COMMIT --volume retired-model-cache --discard-volume --config-home /operator/home --dry-run
```

Review the returned volume fingerprint, stop external cache writers, then apply
the same selection with that fingerprint:

```bash
anvil-serving models cache remove OWNER/REPO --revision 40_HEX_COMMIT --volume retired-model-cache --discard-volume --config-home /operator/home --expected-volume-fingerprint SHA256 --writers-quiesced --confirm
```

This CLI-only local POSIX operation requires filesystem access to Docker's cache
storage. It accepts the dedicated `hub/models--OWNER--REPO` plus
`hub/blobs/<prefix>/<hash>` layout; other layouts remain refused.
It refuses another model or revision, unknown cache contents, nonlocal volume
drivers/options, active or retained container references, current configuration
references, open files, memory mappings, working directories, executables,
held download locks, and identity drift. A confirmed operation holds
cache locks, repeats the audits, removes only the named volume without force,
and verifies both Docker and filesystem absence. The measured free-space delta
is reported separately from the cache-size estimate.

`--writers-quiesced` is the owner's assertion that independent writers have
stopped; advisory locks cannot fence a writer that ignores them. The entire
selected cache is discarded after its supported contents are audited. There is no
automatic rollback: recovery requires downloading the pinned revision again.
Other model caches, images, containers, and build caches are preserved.

Some dedicated caches also contain generated Hugging Face metadata, exact-model
remote-code copies, empty kernel lookup records, or empty Xet staging metadata
and bounded transfer logs. The
preview inventories the supported auxiliary artifacts and binds them to its
fingerprint. If present, apply additionally requires
`--auxiliary-caches-reviewed` after reviewing that inventory. Unknown files,
another model's remote code, and downloaded kernel payloads remain refused.

## Cache inventory

Capture a read-only, machine-readable inventory before and after storage work:

```bash
anvil-serving models cache inventory
anvil-serving models cache inventory --output ./cache-inventory.json
anvil-serving models cache inventory --cache-dir /operator/model-cache/hub
```

The `model-cache-inventory/v1` result includes filesystem capacity, used, and
available bytes; cached repositories, revisions, snapshots, logical and
incomplete bytes, and modification timestamps; plus Docker image, container,
volume, and build-cache accounting. `--volume` and `--image` select the named
Hugging Face cache volume and inspection image. The volume is mounted read-only,
and `--output` uses an atomic replacement after requiring an existing parent
directory.

Pass `--cache-dir` either the Hugging Face home that contains `hub/`, or a
direct hub-cache directory such as `~/.cache/huggingface/hub` or a custom
`HF_HUB_CACHE` path containing `models--*` entries. A directory that contains
both `hub/` and direct `models--*` entries is refused as ambiguous. This is
filesystem-only and reports each standard repository's exact refs and snapshot
revisions, snapshot logical bytes, inode-deduplicated logical and allocated blob
bytes, local-link integrity, upstream artifact completeness as `unverified`, and
unsafe metadata paths, alongside disk capacity. It makes no Docker, network, or
credential access. Repositories downloaded by exact revision may correctly have
no `refs/` directory. Nonstandard layouts retain unknown fields; symlinked roots
and unsafe metadata are never followed.

Modification, creation, and Docker last-used timestamps are observations for
inventory and cleanup planning. They are not proof that a model was actually
served or benchmarked. Use retained benchmark evidence and protected runtime
state for qualification and deletion decisions. The `model_cache_inventory` MCP
tool currently covers managed Docker-volume inventory; native cache inspection
is CLI-only until an ownership-aware controller surface exists.

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

## Whole-cache discard

`models cache discard` retires one entire native flat-model directory or local
Docker Hugging Face volume, including incomplete downloads and global shared blobs.
Use it only when every model and auxiliary asset in that root is retired.
It runs on a native Linux storage owner and requires root access to `/proc`
for complete process auditing. It has no destructive MCP wrapper. Docker
Desktop and remote storage must be operated from an owner with that access.

```bash
anvil-serving models cache discard --volume retired-model-cache --config-home /srv/operator --dry-run
anvil-serving models cache discard --volume retired-model-cache --config-home /srv/operator --expected-fingerprint SHA256 --writers-quiesced --inventory-reviewed --confirm
```

Use `--cache-dir /srv/models/retired-model` instead of `--volume` for a flat
materialization with safetensors weights, one pinned source revision, and known
model/tokenizer metadata. Unknown assets are deliberately refused; nested model
assets require matching download receipts. Preview inventories every entry and binds the operator config,
model identity, and cache filesystem identity into the fingerprint. Apply repeats
the audit under retained POSIX locks. Running or stopped containers, native file
descriptors, memory mappings, working directories, executables, active locks,
configured references, missing config dependencies,
external links, special files, mount boundaries (including same-filesystem bind
mounts), and unreadable state
block deletion. Historical declarations must be explicitly reconciled first.
`--non-model-root-view ID` is only for an independently audited read-only root
observer without GPU access; every declaration is validated again.

The owner must keep external writers quiescent through apply. Docker removal is
non-force; a failed removal request or postcheck returns `unknown` and must be
inspected before any retry.
Native removal uses no-follow directory descriptors; a failure after deletion
starts returns `partial`. Removed downloads must be acquired again for recovery.

Docker volumes reject hardlinked files. Native retirement may unlink one name
of a regular file shared outside the selected root, preserving the other names
and contents. It refuses duplicate inode names inside the root and shared locks;
shared files are listed separately and excluded from estimated reclaimed bytes.

`--config-home` must match the canonical home selected by `ANVIL_SERVING_HOME`
and pass topology validation. The operator is responsible for selecting the
complete authoritative home; an alternate environment override is not independent
proof of ownership. Metadata audits check entry, size, and time bounds; a hung
filesystem syscall remains an operating-system limitation.
