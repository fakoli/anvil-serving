# Host-supervised services

`anvil-serving host services` inventories and operates one explicitly declared
service that is owned by the resolved topology host. It is the lifecycle surface
for portable, supervisor-managed host services. It does not replace
`anvil-serving serves` or a model recipe as the authority for model deployment.

Use [Host & setup](cli/host.md) for the wider host command family and
[Configuration](CONFIGURATION.md) for the operator-home model.

## Commands and confirmation

The commands are `status`, `discover`, `capabilities`, `logs`, `adopt`,
`install`, `up`, `down`, `restart`, `enable`, and `disable`. `status`,
`discover`, `capabilities`, and bounded `logs` are read operations. Every other
command first returns a plan. A mutation applies only with **both**
`--no-dry-run` and `--confirm`; `--confirm` by itself still returns the preview.

```bash
anvil-serving host services capabilities
anvil-serving host services discover
anvil-serving host services status --topology operator-topology.toml
anvil-serving host services logs voice-stt --tail 100 --topology operator-topology.toml

# Preview, then apply the exact same declared action.
anvil-serving host services up voice-stt --topology operator-topology.toml
anvil-serving host services up voice-stt --topology operator-topology.toml \
  --no-dry-run --confirm
```

The lifecycle action resolves the owning resource with the normal `--topology`,
`--command-host`, `--command-runtime`, `--target`, and `--transport` options.
The command runtime must match the binding resource's supervisor execution
runtime. Windows Docker execution through WSL requires an explicit WSL owner.
A local CLI may use `--manifest PATH` to inspect a deliberate local
`services.toml` override. For Docker model adoption it may also name the
owning declaration with `--serve` and a local `--serve-manifest PATH` override.
Controller and MCP calls always use the owner’s configured `services.toml`; an
MCP Docker-model adoption accepts `serve` but resolves its serve manifest from
the owner config home. Neither remote surface accepts an arbitrary path.

A single manifest may contain several runtime owners. Unfiltered status reports
other owners as `requires_owner_runtime` with unknown process state; select that
owner's execution context to observe it. It never probes another owner's
host-relative loopback URL or Docker context from the caller's runtime.

The MCP catalog exposes the same contract as `host_services_status`,
`host_services_discover`, `host_services_capabilities`, `host_services_logs`,
and `host_services_manage`. Inputs are typed service facts; they never include
a command argv, an environment mapping, or a secret value. A controller cannot
stop or restart itself through its own remote transport; use a declared recovery
transport for that case.

## Manager and engine are separate facts

The manager identifies the supervisor that owns the process. The engine says
what the declared service runs. Neither field selects a route, downloads a
model, or chooses a replacement model.

| Host family | Supported manager | Native engine | Docker engine | Provider lifecycle |
| --- | --- | --- | --- | --- |
| macOS | `launchd`, `docker` | `mlx-lm`, `mlx-vlm` | Docker-supported declared adapters; MLX is not a Docker engine | TBD |
| Windows | `docker` | None | Docker-supported declared adapters | TBD |
| Linux | `docker` | None | Docker-supported declared adapters | TBD |
| Cloud or NeoCloud provider | No provider adapter yet | N/A | N/A | TBD |

An existing macOS LaunchAgent for Parakeet or Kokoro may be adopted only as
`support = "legacy"`. That records its supervised identity and bounded state;
it does not make the legacy process a supported MLX engine. Adoption never
migrates a model, moves weights, converts a definition, changes an endpoint, or
replaces a running service. Plan a separate qualified migration through the
owning recipe or serve manifest.

## Service inventory

`init` installs a generic `services.toml` in the operator config home. The
empty scaffold is intentional. Add a binding only after discovering or
otherwise inspecting the exact supervisor-owned service.

```toml
schema = "anvil-services/v1"

[[service]]
id = "voice-stt"
resource = "voice"
manager = "launchd"
engine = "parakeet"
support = "legacy"
label = "org.example.voice-stt"
owner_uid = 1000
source_definition = "definitions/org.example.voice-stt.plist"
definition = "/absolute/operator/LaunchAgents/org.example.voice-stt.plist"
definition_sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
memory_mib = 2048
```

The service `id`, topology `resource`, manager identity, engine, and support
classification are required. A launchd binding pins its label, current user
UID, absolute definition path, and `definition_sha256`. A Docker binding pins
its container name, immutable image ID, and Anvil identity labels. Docker
discovery returns only eligible Anvil-owned containers; it is not an arbitrary
container-management tool.

`startup_policy` is the policy enabled by `enable`: it may be `always` or
`unless-stopped`. `disable` is the separate operation that removes automatic
start; there is no `startup_policy = "no"` adoption value.

`source_definition` is the staged reviewed source. `definition` is the final
manager-owned destination. `definition_sha256` pins the staged bytes and the
installed launchd definition. `install` copies a hash-matching staged launchd
definition into an existing safe destination directory without starting or
enabling it. It refuses an existing registration or a changed source. For
Docker, container creation remains the job of the owning serve recipe, so
`install` only verifies that declared container is already present. No command
downloads an engine or model.

If an `up` starts a previously registered but idle launchd job and a later step fails,
the rollback explicitly bootouts that newly started job to prevent
KeepAlive from starting it after the failed transaction. The error receipt
reports `registration_restored = false` for that deliberate cleanup. A
preexisting running service is never stopped by this rollback path.

An operator-config inventory and export treat `services.toml`,
`source_definition`, `definition`, `serve_manifest`, and `services_manifest`
as versionable dependency edges. The `definition_sha256` field is a content pin,
not a filesystem path. Selected exports include the required source/definition
closure when those files are safely inside the selected operator home, and still
refuse unsafe, missing, outside-home, or secret-bearing dependencies.

## Model reservations and observable state

Host services may describe a model process, but they do not bypass model
admission. A Docker model binding must name both `serve` and `serve_manifest`;
the owning `serves` ledger checks its declared container and reservations before
an `up` or `restart`. A native model binding needs a positive `memory_mib`
budget. The owner refuses it when all resident native model budgets would leave
less than 4 GiB for the host.

Native `serves.toml` entries use `runtime = "native"`, `service`, and an optional
`services_manifest`; their model and engine must match the selected binding.
Native recipes use those same fields under `recipe.serve`, including an explicit
engine and a model equal to `recipe.model`. Native entries reject Docker launch,
GPU reservation, and exclusive-mode controls. Their up/down/status/logs actions
delegate to this lifecycle. Voice STT/TTS and proxy declarations opt in with
`lifecycle = "service"`, `service`, and optional `services_manifest`.

Dependencies must share one host and supervisor execution runtime. `up` starts
dependencies first; stopping a dependency with running consumers is refused.
Stop consumers before dependencies. Cross-owner orchestration remains unsupported.

The status payload keeps these facts separate:

| Fact | Meaning |
| --- | --- |
| `registered` | The supervisor has the pinned identity registered. |
| `running` | The supervisor reports that process as running. |
| `enabled` | Its automatic-start policy is enabled. |
| `state` | Supervisor lifecycle detail such as absent, unloaded, exited, or unavailable. |
| `engine.ready` | A declared loopback endpoint answered its bounded readiness probe. |
| identity and support | The pinned manager identity and whether the binding is supported or legacy. |

Running does not prove readiness, enabled does not start a process, and an
adopted legacy identity does not prove model residency or route eligibility.
Unknown, inaccessible, or changed supervisor state blocks a mutation until the
owner can inspect it again. `logs` returns a bounded, redacted tail from only
the declared service log sources.
