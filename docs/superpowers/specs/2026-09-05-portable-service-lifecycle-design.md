# Portable service lifecycle for Anvil Serving

Date: 2026-09-05

Status: Approved design. Product implementation is verified; private deployment
requires a validated operator workspace. See [Host services](../../HOST-SERVICES.md)
for the implemented command contract and explicit limitations.

Baseline: Anvil Serving 1.0.0, commit
`26fffff7fef3ee951b585c77448970317d5f81df`.

## Problem and user outcome

Anvil features can run under an OS supervisor without being operable through
Anvil Serving. On a macOS host, existing LaunchAgents own voice models, the
Realtime proxy, the controller, and an event service. Marking those endpoints
`external` lets Anvil call them but leaves their lifecycle outside the product.
The operator must know platform commands and distinguish a running daemon
from a resident model manually.

The product should answer what is running, what each feature serves, who owns
it, why it is unavailable, and which lifecycle operations are supported. The
same named feature must be operable through CLI and typed agent tools without
the caller constructing launchctl, systemctl, Windows, or container commands.

This implements the supervision principle in
[ADR-0034](../../adr/0034-fleet-control-plane-and-node-runtime-classes.md):
derive observed state from the owning runtime. It keeps the six product-family
boundaries and the gateway's explicit endpoint-routing contract intact.

## Existing seams and gaps

- `anvil_serving/serves.py::_normalize_serve_runtime` accepts the native
  discriminator syntactically but raises `NativeRuntimeNotSupported`.
- `anvil_serving/voice/serves/native.py` manages its own background processes
  using command identity and PID records. It is not an OS-service adapter.
- `voice/cli.py::execute_audio_lifecycle` skips `external` services. The
  combined voice stack currently requires co-located managed components.
- `commands/host.py` has host operations but no supervised-service family.
- `commands/control_plane.py` exposes controller foreground execution and
  health, without an installed-supervisor lifecycle.
- Command declarations, topology/target resolution, the operation catalog,
  bounded MCP schemas, and lifecycle events already provide integration seams.

## Approaches considered

1. **Shared service lifecycle with independent adapters — recommended.**
   One declared service identity and operation plan serve the existing
   domain commands and new host-service operations. This closes the current
   macOS gap and provides a contract for other supervisors and engines.
2. **Add launchctl commands to voice.** Smaller initially, but leaves
   controller/events outside the product and duplicates engine and ownership
   logic in each command family.
3. **Force every feature into containers.** Reuses existing lifecycle code,
   but cannot provide native Metal/MLX serving through the macOS Linux VM and
   loses the existing deployment model.

## Three independent contracts

| Contract | Responsibility | Examples |
| --- | --- | --- |
| Supervisor adapter | Inspect exact service identity, start/stop/restart, automatic startup, bounded logs | macOS launchd; Docker/Compose on the declared owning runtime |
| Engine adapter | Validate pinned launch parameters and supported capabilities; report model inventory/residency and engine health; perform explicit model load/unload where supported | native MLX on macOS; explicitly declared engines in Docker |
| Endpoint dialect | Validate declared request/response and readiness contracts | OpenAI-compatible chat/audio; engine metadata API; health-only service |

Topology resolves the actual host, execution runtime, ownership, and transport
before adapters are chosen. The caller's OS does not select the remote
supervisor. WSL is an explicit Linux execution runtime on a Windows host;
Windows-hosted Docker operations must resolve that runtime correctly.

Engine and supervisor combinations are capability-checked, not assumed to
form a supported Cartesian product. An unsupported combination produces a
typed explanation before mutation. A health-only controller or event service
does not need an inference-engine adapter.

Routing continues to use declared capabilities, endpoints, and wire dialects.
It does not choose a host, model, or engine from observed availability.

## Approved platform direction

The operator specified this matrix on 2026-09-05:

| Deployment environment | Model-serving runtime | Scope |
| --- | --- | --- |
| Windows | Docker | Current implementation scope |
| macOS | MLX natively, or Docker | Current implementation scope |
| Linux | Docker | Current implementation scope |
| Neocloud: Vast.ai, Runpod | TBD | Deferred; no provider implementation or provisioning |
| Cloud: AWS, Azure | TBD | Deferred; no provider implementation or provisioning |

`MLX` is the macOS engine choice; `launchd` is its supervision mechanism.
`Docker` is the execution/supervision choice, with the engine selected by a
pinned recipe. Docker on macOS does not imply Metal/MLX GPU availability;
engine/hardware feasibility is validated independently.

Do not add native Linux/systemd model serving, native Windows/SCM model serving,
Task Scheduler, Podman, or native Ollama/LM Studio as supported deployment
paths in this feature. Non-model Anvil services use launchd on macOS or their
existing declared Docker deployment on Windows/Linux. Provider names are
deployment metadata until a later provider contract is defined; neither a
cloud API nor paid compute allocation is part of this work.

The operator approved explicit adoption of the existing macOS Parakeet.cpp
and Kokoro LaunchAgents for lifecycle control, with a `legacy` support
classification. Changing their engines to MLX or Docker remains an independent
reviewed migration. This exception is limited to the existing deployment and
does not create new native engine support.

## Service identity and configuration

Use one private operator `services.toml` document with schema
`anvil-services/v1` to record declared supervision bindings, not observed PIDs
or health. Each binding contains:

- Stable service ID, feature kind, and topology resource reference.
- Exact supervisor and owner scope: user/system plus the declared OS owner;
  a launchd label/domain or exact container/Compose identity as appropriate.
- Reference to an existing service definition or pinned recipe and its
  expected identity. Adoption retains the existing supervisor as owner.
- Engine adapter and endpoint contract references when applicable.
- Dependency references and explicit requested startup policy.
- Bounded readiness/shutdown deadlines and owned log references.

`serves.toml` and voice component declarations reference the same service ID.
They retain their model, recipe, capability, reservation, and topology
contracts. A supervisor binding cannot override a model resource's workload
classification or bypass admission by calling it a generic service.

Launch commands and engine settings have a single authority in the referenced
recipe or definition. The new binding does not duplicate an existing Compose
command or serialize a second copy of a LaunchAgent's environment.
Credentials remain environment/file references. Public examples are synthetic;
real adoption output belongs only in the private operator home.

## User-facing command direction

These describe the approved command direction; the linked product guide supplies
the final flags and supported declaration fields.
The resource names below are synthetic. Existing global topology, command-host,
command-runtime, target, transport, JSON, preview, and confirmation options apply.

```bash
# Read-only: declared services and bounded discovery of supervision identities.
anvil-serving host services status --target host:mac-node
anvil-serving host services discover --target host:mac-node
anvil-serving host services capabilities --target host:mac-node

# Explicitly bind an existing service; no restart or model load during adoption.
anvil-serving host services adopt voice-tts --manager launchd \
  --service-label com.example.anvil-voice-tts --resource voice-tts \
  --engine kokoro --support legacy --dry-run
anvil-serving host services adopt voice-tts --manager launchd \
  --service-label com.example.anvil-voice-tts --resource voice-tts \
  --engine kokoro --support legacy --no-dry-run --confirm

anvil-serving host services up voice-tts --dry-run
anvil-serving host services up voice-tts --no-dry-run --confirm
anvil-serving host services down voice-tts --no-dry-run --confirm
anvil-serving host services restart voice-tts --no-dry-run --confirm
anvil-serving host services logs voice-tts --tail 100

# Configure automatic startup independently of current process state.
anvil-serving host services enable voice-tts --no-dry-run --confirm
anvil-serving host services disable voice-tts --no-dry-run --confirm

# Existing feature commands use the same lifecycle planner and executor.
anvil-serving serves up voice-llm --confirm
anvil-serving serves down voice-llm --confirm
anvil-serving voice audio up --confirm
anvil-serving voice proxy down --confirm
```

Use the same `host services` operations for declared router, controller,
Realtime, event, and worker services. Domain convenience commands may delegate
to them without owning a second supervisor registration.

Model recipes continue to own candidate load/unload and exact model identity.
For a multi-model daemon, starting the daemon does not load all cached models.
`serves up` or recipe load ensures its exact declared model is resident where
the engine supports residency control. Unload targets only that model and
does not stop a shared daemon still serving other declared consumers.
For a process-bound engine such as a single-model MLX server, stopping its
owned process also unloads the model. Unsupported load/unload APIs are
reported explicitly rather than approximated with generation requests.

## Operation semantics

**Up/down/restart.** Reinspect the exact identity before each operation.
Starting an already ready service and stopping an already absent service are
idempotent. `down` stops the owned instance and suppresses supervisor demand
or crash restart for the current session without silently changing the
declared automatic-start policy. Adapters must describe whether suppression
lasts until the next explicit `up`, login, runtime restart, or reboot; when a
supervisor cannot guarantee suppression, refuse with an unsupported-operation
result. `restart` preserves that policy and verifies the replacement instance.

**Enable/disable.** Change future automatic-start behavior only. Disabling
does not implicitly kill the current process; enabling does not implicitly
load a model. A caller that wants persistent shutdown requests disable and
down explicitly. The adapter maps this contract to native semantics, including
registration and persistent-disabled state on launchd, rather than equating
similarly named OS commands.

**Readiness and stop proof.** A live PID, an open port, an advertised model,
a loaded model, and readiness are separate facts. Metadata uncertainty remains
`unknown`. Stopping must verify that the exact owned process/container exited;
a failed health probe alone does not prove shutdown. Port release is a separate
observation and does not authorize killing another listener.

**Startup conflicts.** Check declared dependencies, endpoint binds, current
supervisor ownership, and model memory/reservation policy before starting.
If two declared services claim the same bind, identify the conflict. Do not
evict the occupant or silently substitute another model. Claims and observation
are host/runtime-relative, including proxy and container namespace boundaries.

**Groups and voice.** Existing groups and voice orchestration resolve to the
same service dependencies. Start dependencies before dependents; stop in reverse
order. Refuse dependency cycles and an operation that breaks a running declared
dependent unless it explicitly includes that dependent. Failed startup rolls
back only instances started by the current operation, preserving pre-existing
services. Keep current split-host voice restrictions until a separately declared
multi-owner operation can report each owner's independent result.

**External services.** Discovery is read-only and returns bounded identifiers,
supervisor state, feature hints, and adoption eligibility. It does not return
secret values, complete environments, or arbitrary raw command lines. A known
label or engine hint does not grant ownership. Adoption validates an exact
definition, owner, executable identity, binds, dependencies, and manifest digest;
the preview lists conflicts and required declarations. Apply writes a binding
atomically after rechecking the preview identities. External remains a legitimate
read-only state when adoption is not desired or cannot be established.

**Fresh installation.** A separate confirmed `host services install SERVICE`
operation renders a supervisor definition from a declared pinned recipe. Its
dry-run shows the exact target and redacted diff. It does not download engines,
replace a conflicting definition, or enable/start a service implicitly. Existing
recipe/artifact tooling remains responsible for installing engine prerequisites.
Adoption and installation are distinct operations.

## CLI, MCP, and controller parity

Register `host_services_status`, `host_services_discover`,
`host_services_capabilities`, `host_services_logs`, and
`host_services_manage` through the existing explicit catalog. The manage
operation uses an allowlisted action enum for adopt/install/up/down/restart/
enable/disable, exact resource identity, bounded inputs, and existing
`dry_run`/`confirm` conventions. Update command declarations, operation contracts,
controller role allowlists, and generated documentation together.

CLI and MCP call the same library planner/executor. They return the same
structured observation and operation receipt, including:

- Declared resource, owning host/runtime, supervisor, engine, supported actions.
- Registration, running, readiness, automatic-start policy, exact model
  residency, conflict, and unavailable/unknown states as separate fields.
- Before/after observations, applied/skipped/failed state, bounded diagnostics,
  operation identity, and sanitized command preview.

OS and engine subprocess/API errors retain the earliest actionable cause in
bounded diagnostics. Do not report a nonzero result as a stopped model or an
empty host. Redaction applies to previews, errors, logs, and lifecycle events.

Normal remote operations use the authenticated owning controller. A controller
cannot synchronously kill itself and then prove completion over the same
request. Mutating its own supervisor therefore requires local execution or
the existing explicitly selected, authenticated SSH recovery path; the normal
controller transport returns `requires_recovery_transport` before mutation.
No hidden fallback, new daemon, or remote arbitrary-shell tool is introduced.

## Implementation boundaries

Introduce a stdlib-only `anvil_serving/service_runtime/` package for typed
bindings/observations, validation, planning, and supervisor adapters. Keep
engine lifecycle and residency contracts in a separate bounded adapter package.
Register adapters explicitly, following the existing catalog convention.

Extend `commands/host.py` and the corresponding MCP/controller registration
instead of adding a seventh product family. Delegate native `serves`, voice,
and recipe lifecycle through this package in focused changes. Preserve Docker
ownership, admission, promotion rollback, and media's existing ownership receipts.
Do not bypass `serves` reservation checks with a lower-level host command.

Replace the general native-runtime rejection only when all exposed native
operations have a validated implementation or an explicit capability error.
Extend manifest parsing, private configuration inventory/export closure, and
deployment materialization to understand the new service references. Public
examples and scaffold templates remain synchronized.

## Delivery and support claims

1. **Shared contract and the observed gap.** Implement launchd plus existing
   Docker/Compose behavior behind the contract; exact legacy-service adoption;
   CLI/MCP/controller parity; status/logs/start/stop/restart/startup controls;
   macOS MLX and non-model Anvil service integration. Legacy voice bindings
   remain visibly distinct from supported new MLX/Docker recipes.
2. **Docker host parity.** Verify the same Docker adapter on Windows/WSL,
   macOS, and Linux, including command-host identity, paths, log decoding,
   timeout/shutdown behavior, startup policy, and resource observation.
   Additional OS-native supervisors are outside the agreed matrix.
3. **Engine contract integration.** Qualify native MLX and the already
   supported Docker engine recipes through the common contract. A container
   engine offering model residency APIs may supply a bounded residency adapter;
   the initial feature does not add unrequested engine distributions. Generic
   compatible endpoints retain declared readiness without invented controls.

All three stages belong to this feature's tracked outcome. Neither mock
coverage nor one successful macOS smoke establishes Windows/Linux Docker or
engine-wide support. Publish capabilities per operation, adapter version,
engine version, OS/runtime, and verification level. Neocloud and cloud remain
TBD and do not block completing the agreed local-platform matrix.

## Acceptance and verification

Use hermetic unit and contract tests with injected process/API boundaries.
Tests must prove behavior from independently supplied OS/engine responses,
not compare a generated plan to a second copy of its own generation logic.

Required cases:

1. Running, registered-but-stopped, unloaded registration, disabled, failed,
   absent, permission-denied, and unreachable supervisor states.
2. An already running service is adopted without starting another instance;
   ambiguous owner, changed definition, wrong scope, and wrong host are refused.
3. Dry-run/confirmation/role/target failures make zero mutating subprocess or
   API calls. Generic service commands cannot bypass model admission.
4. Start/stop/restart are idempotent where specified; supervisor restart policy
   cannot immediately resurrect a successfully stopped service. Startup policy
   survives a stop/start cycle unless explicitly changed.
5. PID reuse and a healthy unrelated port occupant cannot establish ownership
   or justify signaling a process. Concurrent operations serialize per exact
   resource and revalidate before mutation.
6. Health without model residency reports a running server with no loaded
   model; unavailable model inventory stays unknown. Shared-daemon unload
   leaves unrelated resident models and consumers intact.
7. Dependency ordering, bind conflicts, failed startup, bounded shutdown,
   partial rollback, and pre-existing resource preservation.
8. CLI, MCP, controller operation contracts, generated help, capability
   reporting, error envelopes, and redaction have equivalent behavior.
9. Existing container reservation, exclusive mode, promotion, voice,
   recipe, and media ownership regression gates pass.

Platform tests first use a temporary benign supervised fixture with an isolated
label/port, then an explicitly approved service migration. No tests inspect or
stop unrelated user services. Live migration acceptance requires operating the
affected features through Anvil only, verifying actual endpoint readiness and
stop proof, and restoring the recorded previous startup policy and service state.
Release/deployment follows the existing release-readiness skill, including exact
version parity and rollback; no release is implied by this design document.

## Operator migration boundary

Restore/validate the private workspace registry before writing real service
bindings or deployment records. The registry was unavailable during this design
pass. Product investigation and this sanitized proposal do not establish an
operator-home target or authorize changes to live supervision.

Migration inventories existing units, records their original identity and
startup policy privately, previews adoption, resolves bind conflicts, then
applies only the reviewed bindings. Do not start every discovered service:
alternative model configurations can intentionally claim the same port.

## Completion criteria

The operator can perform the original inventory and lifecycle work through
Anvil Serving tools, with accurate loaded-model and health status, no duplicate
supervision, and no raw OS command required in the ordinary workflow. CLI and
agent tools agree. Every claimed supervisor/engine combination has corresponding
contract and host evidence, and the live migration has a verified rollback.
