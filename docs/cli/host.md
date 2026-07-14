# Host & setup

[CLI overview](../CLI.md) · [Control plane & integrations](control-plane.md) · [Troubleshooting](../TROUBLESHOOTING.md)

Use this family to create the operator configuration, verify the installation,
inspect the host that owns a deployment, and run explicitly guarded host repair.
Focused `--help` is the complete flag reference for every command below.

## Choose a workflow

| Goal | Start here | Then |
| --- | --- | --- |
| Configure a new installation | `init --out-dir PATH` | Replace host placeholders, then run `doctor`. |
| Check whether this machine is ready | `doctor --no-config` | Add `--config PATH` when the router config exists. |
| Inspect a topology-owned host | `host status` | Use `host doctor` for a recommendation or `host memory` for WSL details. |
| Change the WSL memory cap safely | `host doctor` | Preview `host wsl-config`, apply it, then restart Docker Desktop. |
| Recover a wedged WSL backend | `host reset-wsl --dry-run` | Apply only after reviewing the process and container disruption. |
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

### Plan and apply host repair

| Command | Purpose |
| --- | --- |
| `host wsl-config` | Preview, update, or revert the WSL memory and swap keys. |
| `host restart-docker` | Restart Docker Desktop once on Windows or macOS. |
| `host reset-wsl` | Reset a wedged Windows WSL backend, then restart Docker Desktop. |
| `host reclaim` | Drop clean WSL page cache once or run a foreground watchdog. |

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
manifests and Compose files, operator topology, `.env.example`, voice settings,
and tailnet-edge settings. Templates are validated before the first write.
Existing operator files receive numbered `.anvil.bak.N` backups before they are
replaced.

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
anvil-serving doctor --config ./router.toml --json
```

Python, Docker, and Compose are required checks. NVIDIA runtime, GPU discovery,
and unavailable tier health are advisory because the router and model serves
may live on different hosts. With neither selector, `./router.toml` is checked
only when it exists. An explicit missing or invalid `--config` fails instead of
being skipped.

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

These commands are read-only. Missing GPU tools or unavailable probes remain
visible as empty or degraded results instead of triggering repair.

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

`host reclaim` synchronizes the filesystem before dropping clean page cache and
refuses when a checkpoint appears to be streaming. Its `--force` overrides that
specific active-load refusal, not confirmation. Watch mode is an explicit
foreground process:

```bash
anvil-serving host reclaim --watch --threshold-gb 60 --interval 30 --confirm
```

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
- [System observability dashboard](../SYSTEM-OBSERVABILITY-DASHBOARD-MILESTONES.md)
