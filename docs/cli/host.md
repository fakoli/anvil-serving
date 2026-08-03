# Host & setup

[CLI overview](../CLI.md) · [Control plane & integrations](control-plane.md) · [Troubleshooting](../TROUBLESHOOTING.md)

Use this family to create the operator configuration, verify the installation,
inspect the host that owns a deployment, and run explicitly guarded host repair.
Focused `--help` is the complete flag reference for every command below.

## Choose a workflow

| Goal | Start here | Then |
| --- | --- | --- |
| Configure a new installation | `init` | Review detected host values and remaining placeholders, then run `doctor`. |
| Check whether this machine is ready | `doctor --no-config` | Add `--config PATH` when the router config exists. |
| Inspect a topology-owned host | `host status` | Use `host doctor` for a recommendation or `host memory` for WSL details. |
| Inventory private operator config safely | `host config inventory` | Review classifications and dependency closure, then use the sanitized export. |
| Change the WSL memory cap safely | `host doctor` | Preview `host wsl-config`, apply it, then restart Docker Desktop. |
| Recover a wedged WSL backend | `host reset-wsl --dry-run` | Apply only after reviewing the process and container disruption. |
| Diagnose vLLM native-offload tmpfs pressure | `host shared-memory status` | Reclaim only when every file is verified orphaned. |
| Check GPU partitioning prerequisites | `host gpu-sharing inspect` | Run the confirmation-gated probe only when static evidence is insufficient. |
| Upgrade the installed CLI | `upgrade --dry-run` | Apply through the detected package owner with `--confirm`. |

## Command map

### Configure and maintain the installation

| Command | Purpose |
| --- | --- |
| `init` | Scaffold the complete operator configuration or a one-model bring-up. |
| `doctor` | Check Python, Docker, Compose, GPU discovery, and optional tier health. |
| `upgrade` | Upgrade the installed CLI to the newest stable published release. |

### Inspect the host

| Command | Purpose |
| --- | --- |
| `host status` | Return the structured host summary. |
| `host gpus` | List visible NVIDIA GPU indexes, stable UUIDs, and names. |
| `host doctor` | Explain host memory capacity and recommend a safe WSL cap. |
| `host memory` | Show Windows, WSL VM, page-cache, and GPU memory usage. |
| `host shared-memory status` | Report vLLM offload mmap files, live mappings, active owners, and reclaim eligibility. |
| `host config inventory` | Return metadata-only classifications, hashes, parser types, dependencies, and installed revisions. |
| `host config export` | Return safe versionable files and an allowlisted, redacted Anvil-owned OpenClaw fragment. |

### Plan and apply host repair

| Command | Purpose |
| --- | --- |
| `host wsl-config` | Preview, update, or revert the WSL memory and swap keys. |
| `host restart-docker` | Restart Docker Desktop once on Windows or macOS. |
| `host reset-wsl` | Reset a wedged Windows WSL backend, then restart Docker Desktop. |
| `host reclaim` | Drop clean WSL page cache once or run a foreground watchdog. |
| `host shared-memory reclaim` | Remove only twice-verified orphan vLLM native-offload mmap files. |

### Inspect GPU-sharing prerequisites

| Command | Purpose |
| --- | --- |
| `host gpu-sharing inspect` | Collect non-mutating Green Context and MPS capability evidence. |
| `host gpu-sharing probe` | Audit or run the reviewed, UUID-pinned CUDA prerequisite probe. |

### Observe the host

| Command | Purpose |
| --- | --- |
| `dashboard serve` | Run the packaged read-only observability dashboard. |

## Init

Full setup writes the packaged operational configuration to the platform config
home. `ANVIL_SERVING_HOME` changes that home; `--out-dir` selects one explicit
directory instead.

```bash
anvil-serving init --out-dir ./anvil-config
```

The scaffold includes router variants, modes, recipes, model/voice/ComfyUI
manifests and Compose files, operator topology, disabled machine `host.toml`,
`.env.example`, voice settings,
and tailnet-edge settings. Templates are validated before the first write.
Existing operator files receive numbered `.anvil.bak.N` backups before they are
replaced. Files that already match the generated content are left untouched,
without a backup or rewrite; repeated runs therefore do not accumulate copies
of unchanged files such as `.env.example`.

Bare `init` queries `nvidia-smi` and `tailscale ip -4`. The highest-VRAM GPU is
assigned to Compute A, the smallest distinct GPU is assigned to Compute B, and
the detected tailnet IPv4 address replaces the reference placeholder. Equal-VRAM
cards use canonical UUID ordering rather than volatile runtime index. Workload
capability remains independent of A/B placement. Missing tools, unavailable
values, or a single-GPU host leave unresolved placeholders visible; one card
is never silently assigned to both roles.

Override any detected value or disable host probing:

```bash
anvil-serving init --compute-a-gpu-uuid GPU-... --compute-b-gpu-uuid GPU-... --tailnet-ip 100.64.0.10
anvil-serving init --no-detect-host
```

For a one-model configuration:

```bash
anvil-serving init --single-model --model ./models/qwen --gpu 0 --engine vllm --out-dir ./single-model
```

This mode writes mutually consistent Compose, serve-manifest, router, and
topology files. It binds the model endpoint to `127.0.0.1` by default and does
not start a container or router. Unlike disruptive repair verbs, `init` is an
immediate scaffold operation; select a disposable `--out-dir` when evaluating
the generated files.

## Doctor

```bash
anvil-serving doctor --no-config
anvil-serving doctor --config ~/.anvil-serving/router.toml --json
```

Python, Docker, and Compose are required checks. NVIDIA runtime, GPU discovery,
and unavailable tier health are advisory because the router and model serves
may live on different hosts. With neither selector, the config-home
`router.toml` is checked first, followed by legacy `./router.toml` when
present. An explicit missing or invalid `--config` fails instead of being
skipped.

`host doctor` is the topology-aware host-capacity view:

```bash
anvil-serving host doctor
anvil-serving host doctor --topology operator-topology.toml --target host:dark --json
```

It recommends a WSL cap that targets a 14 GB Windows reserve and never exceeds
the 10 GB safety floor. It does not edit `.wslconfig`.

## Upgrade

```bash
anvil-serving upgrade --dry-run
anvil-serving upgrade --manager auto --confirm
```

Preview detects whether uv tool, pipx, or pip owns the installation and resolves
the newest stable PyPI version. Apply performs one package-manager attempt and
then verifies the exact `anvil-serving --version` output. Editable installs are
refused unless `--allow-editable` deliberately replaces the checkout with the
published package.

## Inspect the host

```bash
anvil-serving host status
anvil-serving host gpus
anvil-serving host memory --distro Ubuntu
```

`host status`, `host gpus`, and `host doctor` can resolve a topology-declared
host through its authenticated controller. `host memory` is a local Windows
operation because it reads WSL `/proc/meminfo`; the selected distro is only the
view into the shared WSL VM.

`host status` additively reports the resolved `cache_reclaim` policy: whether it
is valid and enabled, the source `host.toml`, distro, threshold, and whether the
current host can apply it. A missing file or section is a valid disabled policy.
See [Configuration](../CONFIGURATION.md#machine-policy-hosttoml) for the strict
schema and lifecycle coverage.

These commands are read-only. Missing GPU tools or unavailable probes remain
visible as empty or degraded results instead of triggering repair.

## Operator configuration inventory and export

Use the typed host surface when a public checkout needs to recover or refresh
configuration owned by a private operator repository:

```bash
anvil-serving host config inventory --json
anvil-serving host config export --gateway-path ~/.openclaw/openclaw.json --json
anvil-serving host config export --path anvil-router.live.toml --json
```

Both commands resolve the effective operator home from `--home`, then
`ANVIL_SERVING_HOME`, then the platform default. Inventory returns only paths
relative to that home, classifications, byte sizes, SHA-256 digests, parser
types, dependency edges, and installed product/protocol revisions. It never
returns file contents or environment values.

Export includes exact content only for allowlisted Anvil TOML names and
env-example files classified as versionable configuration. Arbitrary TOML and
JSON names remain `unknown`; `openclaw.json` is handled only through the
separate allowlisted and sanitized gateway-fragment path. YAML is reported as
`unsupported`, and export fails closed when it is present because the packaged
runtime has no safe stdlib YAML parser for syntax and dependency validation.
After inventory, repeat `--path RELATIVE_PATH` to request a bounded supported
subset; its direct dependencies are included automatically, and YAML or any
non-versionable dependency still fails the selected export. The matching MCP
tool accepts a bounded `paths` array relative to the resource owner's configured
home. With no path selection, export continues to require whole-home closure.
Secret material, cookie stores, runtime databases and logs, backups, caches,
and unknown files remain excluded with counts. A versionable file containing a
secret-like literal, credential-bearing header shape, private-key material, or
capability URL is refused. Credential fields must use validated environment or
file SecretRefs. When `--gateway-path` is explicit, the full OpenClaw
document is never returned. The output is limited to the `anvil` model provider,
Anvil agent-model entries, Anvil realtime fields, and the Anvil MCP server
entry; raw credentials and capability-bearing URLs are redacted and counted.
An MCP entry is retained only when it matches the known local stdio launch
schema (`anvil-serving mcp serve` or the packaged Python module equivalent).
Unknown fields or any alternate command/argument shape are omitted and counted
instead of passing through an open-ended command configuration.

The entire operation fails closed on a symlink (including the caller-supplied
gateway path before resolution), unreadable or oversized file, path escaping
the selected home, unsupported YAML export, missing direct dependency, parse
failure, or unsafe credential field. The same two read-only operations are available
through MCP/controller transport as `operator_config_inventory` and
`operator_config_export`. Remote calls intentionally reject filesystem-root
overrides: the resource owner uses its configured `ANVIL_SERVING_HOME` and the
standard `~/.openclaw/openclaw.json` path when present. Neither command writes,
restarts, deploys, reroutes, or promotes anything.

## Repair the host

Use one consent spelling for public host mutations: preview with `--dry-run`,
then apply the reviewed operation with `--confirm`.

```bash
anvil-serving host wsl-config --memory 64 --swap 8 --dry-run
anvil-serving host wsl-config --memory 64 --swap 8 --confirm
anvil-serving host restart-docker --dry-run
anvil-serving host restart-docker --confirm
anvil-serving host reset-wsl --dry-run
anvil-serving host reset-wsl --confirm
anvil-serving host reclaim --dry-run
anvil-serving host reclaim --confirm
anvil-serving host shared-memory status
anvil-serving host shared-memory reclaim
anvil-serving host shared-memory reclaim --confirm
```

`host wsl-config` changes only `memory` and `swap`, preserves other sections,
and creates a numbered backup. Use `--revert --dry-run`, then
`--revert --confirm`, to restore the newest backup. `--force` has one narrow
meaning here: override the 10 GB Windows-reserve refusal. It does not replace
the public `--confirm` gate.

`host restart-docker` supports Docker Desktop on Windows and macOS.
`host reset-wsl` is Windows-only recovery for a hung VM and prints an elevated
fallback if process termination is denied. Both use one attempt and stop for
diagnosis rather than retry-looping disruptive actions.

`host reclaim` synchronizes the filesystem before dropping clean page cache with
`sync && echo 1 > /proc/sys/vm/drop_caches`; it does not request inode/dentry slab
reclaim. It
refuses when a checkpoint appears to be streaming. Its `--force` overrides that
specific active-load refusal, not confirmation. Watch mode is an explicit
foreground process:

```bash
anvil-serving host reclaim --watch --threshold-gb 60 --interval 30 --confirm
```

### Native KV-offload shared memory

vLLM native CPU KV offload creates process-shared files named
`/dev/shm/vllm_offload_*.mmap`. A worker crash or forced container removal can
leave one behind; page-cache reclaim does not remove tmpfs files. Inspect first:

```bash
anvil-serving host shared-memory status
```

The inspector scans live `/proc/*/maps` entries and running Docker container
configuration. Any live mapping, native-offload container, unavailable Docker
ownership, invalid path, or changing second inspection blocks deletion.
Confirmed reclaim removes only the exact validated paths from two matching
inspections and verifies they are absent afterward:

```bash
anvil-serving host shared-memory reclaim --confirm
```

Native-offload recipe load runs the same check before Docker starts and fails
closed when ownership is uncertain. Recipe unload and manifest-owned teardown
run it after the owner stops, including when a stopped failure container is
preserved for logs. The read-only controller tool is `host_shared_memory`; the
confirm-gated `host_manage` action is `reclaim-shared-memory`.

## Automatic reclaim after model lifecycle operations

On Windows/WSL, operators may enable one machine-level policy in
`$ANVIL_SERVING_HOME/host.toml`:

```toml
schema_version = 1

[cache_reclaim]
enabled = true
distro = "docker-desktop"
threshold_gb = 16
```

The existing dry run for a covered model operation shows this policy. Its
existing `--confirm` authorizes the model operation and the disclosed
best-effort postcondition; there is no extra lifecycle flag or prompt.

Automatic reclaim waits for the operation's declared readiness boundary,
requires at least 1 GiB of operation-attributable cache growth, refuses active
I/O, and is bounded. The CLI reports `reclaimed`, `disabled`,
`not-applicable`, `below-threshold`, `no-operation-growth`, `active-io`,
`readiness-timeout`, `unavailable`, or `failed`, with cache measurements when
available. A skip or failure warns but does not change a successful download or
model-start exit code. Automatic mode never uses the manual `--force` escape
hatch.

The covered commands are `models pull`, `models recipes load`, manifest-owned
`serves up`, `serves adopt`, `serves switch`, and `serves promote`, including
an explicit rollback. Ad-hoc Compose starts, voice, request-time ComfyUI loads,
and the request-triggered multiplexer remain outside v1. See
[ADR-0023](../adr/0023-lifecycle-aware-wsl-cache-reclaim.md).

## GPU sharing

Start with static, non-mutating inspection:

```bash
anvil-serving host gpu-sharing inspect --timeout 10
anvil-serving host gpu-sharing inspect --topology operator-topology.toml --target host:dark --json
```

The inspector looks at exported CUDA symbols, driver/runtime versions, GPU
identity, and read-only MPS commands. It never creates a CUDA context, starts
MPS, or launches a workload. Missing evidence stays `unknown` or `unavailable`.

The product probe first audits an exact image digest, source hash, Compose
profile, read-only filesystem, dropped capabilities, and one full GPU UUID:

```bash
anvil-serving host gpu-sharing probe --gpu-uuid GPU-00000000-0000-0000-0000-000000000000 --dry-run
anvil-serving host gpu-sharing probe --gpu-uuid GPU-00000000-0000-0000-0000-000000000000 --confirm
```

A confirmed probe uses one temporary container and may populate the Docker
image cache. Its contract still forbids context creation, workload launch, and
GPU-state mutation.

## Dashboard

```bash
anvil-serving dashboard serve --host 127.0.0.1 --port 8766
```

The dashboard runs in the foreground and exposes read-only observability APIs.
The default bind is `127.0.0.1:8766`. A non-loopback private bind requires a
bearer-token environment variable:

```bash
anvil-serving dashboard serve --host 100.64.0.10 --auth-env ANVIL_DASHBOARD_TOKEN
```

## Related references

- [Getting started](../GETTING-STARTED.md)
- [Configuration](../CONFIGURATION.md)
- [Device topologies](../DEVICE-TOPOLOGIES.md)
- [Collectors and observability adapters](control-plane.md#collectors)
