# Host-supervised services

`anvil-serving host services` inventories and operates one explicitly declared
service that is owned by the resolved topology host. It is the lifecycle surface
for portable, supervisor-managed host services. It does not replace
`anvil-serving serves` or a model recipe as the authority for model deployment.

The native Windows PowerShell CLI can inspect offline configuration and
topology and dispatch declared typed operations to a remote owner. On a Windows
host, run the owning service runtime in Linux containers with Docker Desktop's
Linux engine or inside Linux in WSL. Native Windows service hosting is
unsupported, and only verbs with a declared remote execution plan operate a
remote owner.

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
| macOS | `launchd`, `docker` | `mlx-lm`, `mlx-vlm`, `mlx-audio` | Docker-supported declared adapters; MLX is not a Docker engine | TBD |
| Windows host (Linux containers or WSL) | `docker` | None | Docker-supported declared adapters | TBD |
| Linux | `docker` | None | Docker-supported declared adapters | TBD |
| NeoCloud: Vast.ai, Runpod | No provider adapter yet | N/A | N/A | TBD |
| Cloud: AWS, Azure | No provider adapter yet | N/A | N/A | TBD |

This is the implemented platform contract. An isolated live macOS LaunchAgent
smoke covers install, start, status, logs, restart, enable, disable, and stop.
Docker adapters have simulated supervisor tests; live Docker lifecycle
qualification on Linux, macOS, and Windows hosts using a Linux runtime remains
pending.

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

### Retained Docker incarnations

`retained_container = true` is a separate native host-service contract for an
operator-reviewed container that already exists and must be stopped and started
as the same Docker object. It is not adoption, recipe ownership, or external
Compose ownership. A retained declaration pins the full 64-character
`container_id`, name, immutable `image_id`, complete `identity_labels`, exact
`restart_count`, `restart_policy`, `restart_maximum_retry_count`, and SHA-256
digests of the exact writable-mount set and a closed security projection. It
also pins a separate, reviewed, nonsecret `definition` file with
`definition_sha256`. `shutdown_grace_seconds` pins the deliberate Docker stop
grace; the operation timeout must leave at least five more seconds for physical
verification. The closed provenance JSON binds the service, container and image
identities, reviewed nonsecret source and review digests, and the reviewed
Docker healthcheck digest. `healthcheck_required = true` is mandatory. The
definition is provenance only; the lifecycle never
executes it, renders Compose, loads an environment file, pulls an image, or
creates or removes a container.

Retained inspection asks Docker for a fixed projection. It never requests the
container environment, command, or full inspect document. Added or removed
labels, a changed container ID, name, image, writable mount, restart count or
policy, or security projection all fail closed. A missing container is drift,
including after a stop; it is not a successful stop. Status and exact `up` and
`down` are the only supported actions. Dependencies, model/serve declarations,
install, adoption, restart, enable, disable, and logs are refused.

The first command produces a reviewable `retained_container.preview_sha256`:

```bash
anvil-serving host services down SERVICE
```

Apply requires that digest and a protected owner authorization created after
the operator's storage-custody, ingress-state, drain-state, and human-approval
gates:

```bash
anvil-serving host services down SERVICE --expected-preview-sha256 SHA256 --operator-authorization-file PATH --no-dry-run --confirm
```

The authorization is an owner-only, non-symlink JSON file with schema
`anvil-retained-container-authorization/v1`, scope
`retained-container-lifecycle`, a five-minute-or-shorter expiry, and exact
service, action, manifest, preview, container, custody, definition, and evidence
digests. A stop requires `storage_consumer_state = "clear"` with its evidence
digest. It records `ingress_idle_state` as `quiesced` or `unknown` and
`drain_state` as `complete` or `bounded`, each with its evidence digest. An
unknown ingress state or bounded drain is accepted only when the same protected
authorization explicitly sets `uncertain_interruption_risk_accepted = true` and
binds the fresh human-approval digest; it is never relabeled as idle or drained. The
executor rechecks the manifest, definition, authorization, physical container,
and custody projection under its operator lock immediately before issuing one
exact `docker stop --timeout SECONDS CID` or `docker start CID`. The preview and
pre-mutation comparison bind Docker's lifecycle timestamps as well as its PID
and state, so a copied authorization cannot survive an intervening manual
start/stop cycle. Immediately before
the command it durably creates an exclusive consumption marker beside the
authorization. Reusing the authorization is refused even after a manual state
cycle, and a failed or uncertain command still consumes it. It never retries a mutation. A
failed start may issue one rollback stop only while the original CID and full
custody projection still match; replacement or configuration drift leaves an
explicit hold. The preview reports a separate `rollback_timeout_seconds` of the
declared shutdown grace plus five seconds for physical verification, along with
`maximum_total_seconds` for the primary action and that failure-only reserve.

This contract is available only through the owning native Linux CLI. The MCP
service tool does not accept authorization-file paths. A start receipt proves
that the same physical Docker incarnation reached `running` with its declared
Docker health status `healthy`; it does not claim application or router health.
Operational activation still performs the separately reviewed native health
readback. Starting an already running object or stopping an already stopped
object is refused rather than reported as an applied no-op.

Ordinary Docker bindings require the first-party recipe ownership label.
External Compose bindings keep their exact project, service, image, and
read-only configuration-mount checks. Neither sibling mode can be used as a
retained-container shortcut. Declaring or previewing retained custody does not
authorize an actual pause, router stop, readmission, or any later recovery step.

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
