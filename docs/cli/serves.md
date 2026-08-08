# Model serves

[CLI overview](../CLI.md) · [Router](router.md) · [Models & recipes](models.md)

The `serves` family owns local model-server definitions and lifecycle. A serve is
manifest-owned; a recipe is a reusable model-and-engine configuration managed under
[`models recipes`](models.md#recipes).

Each manifest entry also belongs to a user-facing `stack`. Stacks are the
lifecycle ownership boundary for separate workloads such as `serving`,
`auxiliary`, `voice-audio`, `voice-proxy`, and `comfyui`. The CLI maps a stack
to the stable Docker Compose project `anvil-<stack>`, injects that project when
the `up` command omits it, and refuses to manage an existing container owned by
another stack unless `serves up --recreate` is explicit. If an authored
`--project-name` contradicts `stack`, manifest loading fails before Docker is
called. Entries that omit `stack` retain the `serving` default.

## Commands

| Command | Purpose |
| --- | --- |
| `serves render` | Render a model serve definition. |
| `serves up` | Start manifest-owned serves. |
| `serves up-for` | Resolve a chat alias to its backing serve and start it. |
| `serves down` | Stop and remove manifest-owned serve containers. |
| `serves rm` | Remove a manifest-owned serve. |
| `serves adopt` | Adopt an existing serve into manifest ownership. |
| `serves switch` | Switch a deployment role to an activation-ready recipe. |
| `serves promote` | Preflight and promote a staged recipe with rollback. |
| `serves mode status|preview|enter|leave` | Inspect or transact exclusive TP=2 ownership. |
| `serves status` | Show bounded serve status. |
| `serves lint` | Report manifest defects that no other surface makes visible. |
| `serves rollback-check` | Prove every declared rollback is actually usable. |
| `serves probe` | Run one engine-aware functional request. |
| `serves groups` | List serve groups and their members. |
| `serves logs` | Read bounded serve logs. |
| `serves multiplex` | Run the single-resident model multiplexer. |

Every leaf uses its real parser for exact usage, local arguments, choices, and
defaults. The registry adds its canonical summary, global options, safety
policy, and direct reference link. For example:

```bash
anvil-serving serves up --help
anvil-serving serves switch --help
```

This page owns the longer workflows and behavioral guidance. Keeping that prose
out of the runtime registry avoids a second documentation copy while the
generated command index and reference audit still enforce the public paths.

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

Bare `serves status` polls only entries assigned to an authored group—the
operator-supported serving path. Untagged candidates and experiments remain
available by explicit name or `--group all`, but are not contacted merely
because they exist in the manifest.

## Start and stop serves

```bash
anvil-serving serves up --group ocr --dry-run
anvil-serving serves up --group ocr --confirm
anvil-serving serves down --group ocr --confirm
anvil-serving serves down CANDIDATE --keep-container --confirm
anvil-serving serves groups
anvil-serving serves logs OCR_SERVE_NAME
```

Only manifest-owned resources are mutated. Destructive leaves require confirmation.
`down` stops gracefully and then removes each selected container by default, preventing
stopped experiments and stale container configuration from accumulating in Docker.
Use `--keep-container` when retaining the stopped container and its logs is intentional.
`--confirm` is the only public consent spelling; the removed `serves rm --yes` and
`serves adopt --yes` forms fail with migration guidance before reaching Docker.

`serves up` returns only after the selected serve's declared health endpoint is
ready. A failed or timed-out readiness check fails the command instead of
reporting a successful start.

## Start by alias

Answering "how do I start what `llm.primary` needs" means reading the router
config's `[router.model_routes]` for the tier, then the serve manifest for the
`[[serve]]` whose `router_tier` matches it — the join key exists in the data,
but nothing walks it. `serves up-for ALIAS` does:

```bash
anvil-serving serves up-for llm.primary
anvil-serving serves up-for llm.primary --confirm
anvil-serving serves up-for llm.voice --config ./router.toml --json
```

Without `--confirm`, it only resolves and prints the chain — alias, tier,
serve name, container, port, the exact `up` argv, and the manifest file it
came from — closing the four-file read in one command. `--confirm` delegates
to `serves up` for the resolved serve; `--dry-run` forwards to it. An unknown
alias exits 2 and lists the configured aliases; a tier with no backing serve
in the manifest set exits 1.

A tier can legitimately back more than one serve — a promoted primary and its
rollback commonly share `router_tier` (and a port), since the rollback config
routes the same alias to the same tier id. `up-for` refuses to guess between
them: it prints every candidate serve with its `groups` and exits 1, requiring
`serves up NAME` to pick explicitly. Auto-selecting the wrong one on a shared
port is worse than asking — the same "detection beats prevention" judgment
`rollback-check` and `lint` already make (see
[Strategy: make divergence loud](../STRATEGY-MAKE-DIVERGENCE-LOUD.md)).

## Split and exclusive TP=2 modes

Ordinary split-mode entries reserve one `gpu_role`. An exclusive candidate
declares exactly two `gpu_roles`, `operating_mode = "dual-gpu-exclusive"`, and
`tensor_parallel_size = 2`. It cannot be started with ordinary `serves up`.

```bash
anvil-serving serves mode status --manifest ./serves.toml
anvil-serving serves mode preview TP2_SERVE \
  --restore-group split-stack --manifest ./serves.toml
anvil-serving serves mode enter TP2_SERVE \
  --restore-group split-stack --manifest ./serves.toml \
  --preserve-on-failure --confirm
anvil-serving serves mode leave TP2_SERVE \
  --restore-group split-stack --manifest ./serves.toml --confirm
```

Preview reports both stable GPU roles, the TP size, active workloads to drain
and stop, workloads blocked while exclusive, and the explicit rollback group.
Entry fails closed on unresolved Docker state, drains routed competitors,
stops all GPU inference, rechecks both roles, then starts the exclusive owner.
Failure restores the split group. Leave stops the owner before restoring that
group. By default, failed entry removes the candidate before rollback. Add
`--preserve-on-failure` when debugging a candidate: Anvil stops the failed
target, retains the stopped/exited container and its logs, and then restores the
split group. If the target cannot be proven stopped, Anvil removes it so an
unhealthy or restarting process cannot retain either GPU during restoration.
The `serves_mode` MCP tool exposes the same structured plan; live remote
entry/leave requires its separate human-approval gate.

## Lint

`serves lint` is static analysis over the loaded manifest set. It touches no
Docker and no network, and it exits non-zero when it finds an error, so it can
gate CI or a pre-promotion check.

```bash
anvil-serving serves lint
```

Add `--json` for the same report structurally, matching the status and group
JSON conventions.

Three checks, each added because the defect it finds occurred live while every
other command reported success (see
[Strategy: make divergence loud](../STRATEGY-MAKE-DIVERGENCE-LOUD.md)):

| Check | Severity | What it catches |
| --- | --- | --- |
| `duplicate-serve-name` | error | Two entries sharing a `name` after container de-dup. Name selection becomes ambiguous and one entry silently wins, so an edit to the losing copy is invisible at runtime. |
| `missing-registry` | error | A `--registry` path inside an `up` command that does not exist. Otherwise this surfaces only once a mode transaction is already running. |
| `worktree-anchored-registry` | warning | A recipe registry resolving inside a linked git worktree, which `git worktree remove` deletes. |

`load_manifest_set` **refuses** a duplicate name outright, so a shadowed
edit can no longer reach a live command. `serves lint` deliberately loads
leniently and still reports the defect — the command you reach for when
blocked must not be the one that breaks.

Sharing a **container** across files is not a defect — that is the supported
read-only mirror pattern that `load_manifest_set` de-dupes deliberately. Only a
duplicate `name` surviving that de-dup is reported.

## Rollback check

`serves rollback-check` proves every declared rollback path is actually
usable. It touches no container state — it reads the manifest set and
promotion plans, validates router configs, and asks Docker whether images are
present locally.

```bash
anvil-serving serves rollback-check
anvil-serving serves rollback-check --restore-group split-default
```

It exists because two rollback paths were found broken live on 2026-08-08,
each only by accident:

- a promotion plan's `rollback_router_config` referenced a router profile file
  that did not exist;
- the split-restore `primary` serve's compose image was a nightly tag
  (`vllm/vllm-openai:nightly-...`) evicted from Docker Hub, so the documented
  rollback group could not start.

A rollback that cannot run is a false safety net. Add `--json` for the same
report structurally, matching `lint`'s conventions.

| Check | Severity | What it catches |
| --- | --- | --- |
| `promotion-topology` | error | A promotion plan whose target/rollback router configs no longer validate against each other (mismatched tiers, ports, or model identity). |
| `rollback-profile-invalid` | error | A routed exclusive serve's `rollback_router_config` exists but fails to parse/validate as a router config. |
| `rollback-image-missing` | error | A compose image that a promotion plan's rollback serve, or a `--restore-group` serve, depends on is not present locally (`docker image inspect` fails) — the exact shape of the evicted-nightly-tag incident. |
| `image-unverifiable` | info | A dependent serve's `up` command has no compose file (for example a `models recipes load` command), so image presence cannot be checked. Not an error — some rollbacks legitimately have no compose image. |
| `docker-unavailable` | warning | Docker itself could not be reached; image checks were skipped rather than failing the whole report. |
| `unknown-restore-group` | error | A `--restore-group` that matches no serve. Silently verifying nothing is itself a false safety net. |

Only `error`-severity findings fail the exit code; `warning` and `info` are
reported but never block.

## Functional probes

Health proves that a process is accepting requests; it does not prove the
serve's defining modality works. `serves probe` provides a bounded,
manifest-aware request for the supported purpose engines:

```bash
anvil-serving serves probe embeddings
anvil-serving serves probe reranker
anvil-serving serves probe ocr --image ./screen.png --text "Read all visible text."
anvil-serving serves probe comfyui --manifest ./serves.comfyui.toml
```

Embedding probes require a non-empty vector, reranker probes require one finite
score per document, OCR/vision probes require non-empty recognized text, and
the ComfyUI probe requires system metadata. Results are bounded JSON and omit
image bytes. Chat LLMs retain the stronger `eval preflight` contract; audio
serves use `voice benchmark`.

On a Windows/WSL machine with the default-off `host.toml` cache policy enabled,
confirmed manifest-owned `up` waits up to 600 seconds for every selected serve's
declared HTTP health, then evaluates one best-effort page-cache reclaim. Ad-hoc
`serves up --compose` is excluded. The dry run discloses the resolved policy, and a
readiness timeout or reclaim failure warns without stopping the container or changing
the successful lifecycle exit code.

## Render and adopt

```bash
anvil-serving serves render --help
anvil-serving serves adopt --dry-run
anvil-serving serves adopt --confirm
```

`render` produces a reviewable serve definition. `adopt` brings an already-running
serve under the same ownership contract; it does not silently claim arbitrary
containers. An enabled machine cache policy gives `adopt` the same bounded health wait
and single postcondition as manifest-owned `up`.

## Switch Primary by recipe

For the common model-selection path, choose the deployment role and recipe directly:

```bash
anvil-serving serves switch primary
anvil-serving serves switch primary Laguna-S-2.1-NVFP4 --dry-run
anvil-serving serves switch primary Laguna-S-2.1-NVFP4 --confirm
anvil-serving serves switch primary gpt-oss-puzzle-88B --confirm
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

An enabled machine cache policy runs once at this public boundary after the switch's
existing health, exact-identity, preflight, and router-readiness gates. Nested `up` work
inside the transaction does not reclaim separately.

## Advanced: promote a plan

For lower-level plan operation and recovery:

```bash
anvil-serving serves promote PROMOTION_PLAN --dry-run
anvil-serving serves promote PROMOTION_PLAN --confirm
```

`PROMOTION_PLAN` names a `[[promotion]]` entry in the selected serves manifest.
Promotion stages the candidate, runs preflight, and preserves a rollback path. It is
separate from [`models recipes load`](models.md#load-a-recipe), which starts a named
local container but never promotes router policy. Recipe-loaded candidates are
inspected and removed through
[`models recipes status|logs|unload`](models.md#operate-a-loaded-recipe), not
raw Docker or manifest-only `serves` verbs.

Promotion and an explicitly requested rollback also evaluate the enabled machine cache
policy exactly once after their existing readiness gates. Controller-dispatched serve
operations inherit the behavior because they execute the same resource-owner CLI; no
new MCP tool or schema field is added. See
[`host.toml` configuration](../CONFIGURATION.md#machine-policy-hosttoml) and
[ADR-0023](../adr/0023-lifecycle-aware-wsl-cache-reclaim.md).

## Multiplexing

```bash
anvil-serving serves multiplex --help
```

The multiplexer coordinates a single-resident model workload. Use it only where the
deployment topology assigns that model workload to the current host.

## Related references

- [Operator playbooks](../OPERATOR-PLAYBOOKS.md)
- [Promote and roll back](../MODEL-PROMOTION.md)
- [Models & recipes](models.md)
- [Evaluation & benchmarks](eval.md)
