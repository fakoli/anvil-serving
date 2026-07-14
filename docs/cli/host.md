# Host & setup

[CLI overview](../CLI.md) · [Control plane & integrations](control-plane.md) · [Troubleshooting](../TROUBLESHOOTING.md)

This family covers initial configuration, health checks, CLI upgrades, host inspection
and repair, GPU-sharing capability checks, and the local observability dashboard.

## Commands

| Command | Purpose |
| --- | --- |
| `init` | Scaffold the complete operational configuration or a single-model bring-up. |
| `doctor` | Check dependencies and configured service health. |
| `upgrade` | Upgrade the CLI to the newest stable published release. |
| `host status` | Show structured host status. |
| `host gpus` | Show GPU inventory. |
| `host gpu-sharing ...` | Inspect or probe CUDA sharing capabilities. |
| `host doctor` | Diagnose declared host configuration. |
| `host memory` | Show host RAM and WSL VM memory usage. |
| `host wsl-config` | Render or update WSL configuration. |
| `host restart-docker` | Restart Docker Desktop. |
| `host reset-wsl` | Reset WSL. |
| `host reclaim` | Drop the WSL VM page cache. |
| `dashboard serve` | Serve the packaged read-only dashboard. |

## Init

With no flags, `init` scaffolds the full operator configuration into
`~/.anvil-serving` (or the platform-equivalent home resolved by the CLI):

```bash
anvil-serving init
```

The generated set includes router variants, mode configuration, the serve-recipe
registry, model/voice/ComfyUI manifests and compose files, operator topology,
environment template, voice configuration, and tailnet-edge configuration. Existing
operator files are not silently overwritten.

For an isolated single-model bring-up, use focused help to supply the model, engine,
port, and output directory:

```bash
anvil-serving init --single-model --help
```

## Doctor

```bash
anvil-serving doctor
anvil-serving --json doctor
anvil-serving host doctor
```

`doctor` checks the installed tool and configured services. `host doctor` is the
topology-aware host diagnostic under the resource-owner command family.

## Upgrade

```bash
anvil-serving upgrade --dry-run
anvil-serving upgrade --confirm
anvil-serving upgrade --manager auto --confirm
```

`--dry-run` reports the detected install manager and proposed version without writing.
A bare `upgrade` also stops before mutation, but reports that confirmation is required;
repeat with `--confirm` to apply the resolved upgrade.
Use `--allow-editable` only when intentionally replacing an editable source install.

## Host

Read-only inspection works across Linux, macOS, and Windows where the underlying
capability exists:

```bash
anvil-serving host status
anvil-serving host gpus
anvil-serving host memory
```

Repair operations are explicit and guarded:

```bash
anvil-serving host wsl-config --dry-run
anvil-serving host wsl-config --confirm
anvil-serving host restart-docker --confirm
anvil-serving host reset-wsl --confirm
anvil-serving host reclaim --confirm
```

OS-specific operations fail with a clear capability error on unsupported hosts instead
of assuming WSL or Docker Desktop is present.

## GPU sharing

```bash
anvil-serving host gpu-sharing inspect
anvil-serving host gpu-sharing probe --gpu-uuid GPU-... --dry-run
anvil-serving host gpu-sharing probe --gpu-uuid GPU-... --confirm
```

`inspect` is non-mutating. `probe` runs a guarded Docker/CUDA prerequisite check; it
does not enable Green Contexts or MPS automatically.

## Dashboard

```bash
anvil-serving dashboard serve --help
```

The dashboard is read-only and intended for local system observability. It does not
become a second control plane.

## Related references

- [Getting started](../GETTING-STARTED.md)
- [Configuration](../CONFIGURATION.md)
- [Device topologies](../DEVICE-TOPOLOGIES.md)
- [System observability dashboard](../SYSTEM-OBSERVABILITY-DASHBOARD-MILESTONES.md)
