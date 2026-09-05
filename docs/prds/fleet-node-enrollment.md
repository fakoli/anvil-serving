# Project: Managed Fleet Node Bootstrap and Enrollment

## Summary

Implement the open `fleet bootstrap` product surface for installing or upgrading the Anvil Serving node runtime on one explicitly declared topology host. The normal control path remains the authenticated controller transport; a hardened, fixed-operation SSH recovery transport may be used only when the declared node is absent or its controller is unavailable. Bootstrap produces a previewable, digest-pinned install bundle, applies it with confirmation and rollback, and accepts the node only after exact identity and version checks. It does not discover hosts, exchange credentials, mutate routes, or start model workloads.

## Goals

- Give operators one managed, repeatable CLI workflow to plan, apply, verify, and inspect node enrollment.
- Resolve the target through validated topology and fail closed on ambiguity, drift, or unexpected identity.
- Package exact code and a fixed bootstrap shim with hashes, bounded paths, rollback, and auditable receipts.
- Prefer the normal controller plane and constrain SSH to bootstrap/recovery of declared nodes.
- Separate package installation from route, serve, GPU, promotion, and credential authority.
- Provide explicit code seams, OS adapter boundaries, negative tests, and gates that a focused execution model can follow safely.

## Non-Goals

- Scanning the network, discovering undeclared hosts, inferring targets from addresses, or enrolling arbitrary machines.
- General remote shell, arbitrary command execution, remote Docker access, file browsing, or interactive SSH sessions.
- Generating, copying, reading, rotating, displaying, hashing, or persisting controller tokens, SSH private keys, provider credentials, or secret files.
- Starting, stopping, loading, unloading, promoting, or rerouting LLM, voice, media, purpose-model, or GPU workloads.
- Installing Docker, NVIDIA drivers, CUDA, WSL, operating-system packages, or other machine prerequisites in the first release.
- Continuous reconciliation, background auto-healing, mass rollout, cross-host scheduling, or automatic version promotion.
- Treating a successful file copy, process start, `/health` response, or tracked topology entry as accepted live identity.

## Requirements

- R001: `anvil-serving fleet bootstrap plan --host <id>` must be read-only and resolve exactly one declared host, transport, node identity, platform, install root, supervision adapter, and expected controller endpoint through the validated topology and execution-plan APIs.
- R002: Planning and apply must fail before staging when topology validation fails, the host is ambiguous or absent, the host is not bootstrap-capable, a topology fingerprint changed, or target fields disagree with the execution plan.
- R003: The normal path for an already reachable node must use the authenticated `ControllerTransport`; `SSHRecoveryTransport` is permitted only for a declared node whose controller is absent or unavailable and only for the fixed bootstrap operation.
- R004: SSH recovery must use a literal topology-declared private/tailnet endpoint, explicit user, pinned identity file, strict known-host verification, batch mode, disabled forwarding, bounded connect/command timeouts, bounded output, and the repository's established hardened option builder.
- R005: A bootstrap bundle must contain an exact wheel or install artifact, a stdlib-only fixed-operation target shim, and a closed manifest with schema version, package version, source commit, artifact hashes, expected node ID, supported platform, install adapter, supervision adapter, install root class, and minimum/maximum compatible controller protocol.
- R006: Every target path must be generated from validated bounded identifiers beneath a configured bootstrap staging/install root; traversal, absolute manifest paths, symlink/reparse escape, device names, alternate data streams, and unsafe archive entries must be rejected before extraction or replacement.
- R007: Authentication material must be provisioned separately through existing environment/file-backed operator mechanisms; bootstrap may verify only presence through the target controller's typed response and must never read, transmit, serialize, hash, log, or include secret names or values in a bundle or receipt.
- R008: `fleet bootstrap apply` must require the existing explicit confirmation idiom, revalidate topology and plan fingerprints immediately before mutation, stage to a unique temporary generation, verify every digest on the target, and invoke only the fixed shim operation described by the manifest.
- R009: Target installation must be transactional: preserve the previously active generation, install into a new generation, switch one bounded current pointer or supervisor reference atomically where the platform permits, and retain enough state for one-command rollback.
- R010: The first release must support explicit adapters for the repository's declared Windows and Linux node runtime/supervision contracts; an unsupported OS, privilege boundary, install layout, or supervisor must fail with a typed precondition result rather than running guessed commands.
- R011: After installation, acceptance must use `ControllerTransport` with `expected_node`, require exact node ID, package version, source revision when available, protocol compatibility, command catalog compatibility, and a bounded health result; an HTTP 200 without exact identity must fail acceptance.
- R012: If install, activation, restart, or acceptance fails, the workflow must attempt the bounded rollback when a prior generation exists, verify the restored controller identity, and otherwise return a typed manual-recovery state without deleting evidence or broad machine state.
- R013: Successful and failed runs must clean only their validated staging directory, retain a bounded metadata-only receipt, and never run broad temp, package-cache, container, or service cleanup.
- R014: Plan, apply, status, and rollback output must expose host ID, topology fingerprint, artifact/version identity, adapter, phase, timestamps, acceptance result, rollback result, and sanitized error codes while excluding private addresses, local user paths, commands, environment, credentials, raw remote output, and capability-bearing URLs.
- R015: Bootstrap must never modify topology declarations, router configuration, model routes, serve manifests, GPU mode, recipes, client profiles, active/promoted assignments, or deployment approval state; those remain separate managed workflows and human gates.

## Acceptance Criteria

- Planning a valid declared Windows or Linux node produces the same deterministic manifest and digest for the same source inputs and performs no remote mutation.
- Planning refuses an undeclared host, invalid topology, changed topology fingerprint, unsupported adapter, unsafe path, malformed bundle field, and protocol-incompatible target with typed bounded output.
- A reachable enrolled node uses only `ControllerTransport`; tests prove SSH is not invoked.
- An absent/unreachable declared controller can use only the fixed bootstrap SSH operation with strict options; attempts to supply an arbitrary command, undeclared endpoint, unpinned host key, or unsafe path are structurally impossible or refused.
- Target tests prove digest verification occurs before extraction/install, path validation prevents escape and link tricks, and activation preserves a prior generation.
- Acceptance rejects wrong node ID, wrong version/revision, incompatible protocol/catalog, missing auth provisioning, and health-only impostors.
- Failure-injection tests at stage, verify, install, activate, restart, and accept phases prove bounded cleanup and either verified rollback or an explicit manual-recovery result.
- Output/redaction tests prove no credentials, addresses, local personal paths, raw commands, raw remote output, or capability-bearing URLs appear.
- CLI manifests, focused tests, full tests, lint, docs, links, and Windows-oriented hygiene gates pass.

## Risks

- SSH bootstrap can become a general remote-execution escape hatch unless request and shim operations are closed enums with no caller-supplied command text.
- Cross-platform install and supervisor behavior differs materially; guessed privilege or service-manager behavior could leave a node partially installed.
- Replacing a running controller can sever the acceptance channel; rollback state and reconnect deadlines must be prepared before activation.
- Topology can change between preview and apply; fingerprint revalidation is mandatory immediately before staging.
- Receipts can leak network or filesystem identity even without secrets; outputs must be allowlisted rather than sanitized after serialization.
- A new node can be correctly installed but not credentialed; that is a separate blocked precondition, not permission to move a token.

## Open Questions

None. Credential provisioning and machine prerequisites remain separate; v1 enrolls one declared node at a time with explicit platform/supervision adapters, fixed SSH recovery, transactional generations, exact controller acceptance, and no workload mutation.

## Assumptions

### A001: The target host already satisfies machine prerequisites and has separately provisioned authentication.

**Rationale:** Installing operating-system dependencies or moving credentials would materially expand authority and platform risk. Bootstrap can detect and report missing prerequisites without repairing them.

**Requirements:** R007, R010, R011, R015

### A002: Topology owns target identity while the bundle owns software identity.

**Rationale:** Keeping these authorities separate lets apply compare an immutable topology fingerprint with artifact hashes and reject drift before remote mutation.

**Requirements:** R001, R002, R005, R008

### A003: SSH recovery exposes a closed operation, not a command transport.

**Rationale:** Existing ADRs reserve SSH for bootstrap/recovery. A fixed shim and manifest can provide that capability without creating arbitrary remote shell authority.

**Requirements:** R003, R004, R005, R008

## Code Map

- `anvil_serving/topology.py::Host`, `Transport`, `Topology`, `validate_topology`, `load_topology`, and `topology_snapshot_identity` own declared host identity, transport policy, and stable drift detection.
- `anvil_serving/targets.py::resolve_execution_plan` is the required topology-to-operation resolution seam. Bootstrap must add a typed operation/plan rather than independently walking raw TOML.
- `anvil_serving/transports.py::ControllerTransport` is the normal authenticated RPC path; `SSHRecoveryTransport` owns bounded SSH recovery and its hardened options. Do not add a subprocess-based parallel transport.
- `anvil_serving/control_plane/controller/` owns node identity, version, health, and command-catalog responses used for exact acceptance.
- `anvil_serving/commands/spec.py::CommandNode`, `RemoteOperation`, and `write_manifest` plus the CLI registration modules own command shape and generated `docs/CLI-COMMAND-MANIFEST.json`.
- `anvil_serving/fleet.py` contains the existing fleet output, version-parity, expected-node, and partial-failure idioms to reuse.
- `anvil_serving/operator_output.py` owns typed operator errors and redacted rendering; library modules must return dictionaries/dataclasses rather than print.
- `tests/test_topology.py`, `tests/test_topology_defaults.py`, `tests/test_transports.py`, `tests/test_controller.py`, `tests/test_remote_controller_regressions.py`, `tests/test_fleet_version.py`, `tests/test_fleet_drift.py`, and command-manifest tests are primary seams.
- `docs/adr/0034-fleet-control-plane-and-node-runtime-classes.md`, `docs/adr/0035-fleet-configuration-reconciliation.md`, `docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md`, `docs/ARCHITECTURE.md`, and `docs/CLI.md` define the product boundary that this work closes.

## Features

### F001: Deterministic bootstrap planning and bundle contract

Resolve one declared target and build a closed, digest-pinned, path-safe installation bundle without mutation.

**Requirements:** R001, R002, R005, R006, R014, R015

### F002: Hardened staging and transactional platform installation

Use normal controller RPC when possible and fixed SSH recovery only when required, with explicit Windows/Linux adapters and rollback.

**Requirements:** R003, R004, R008, R009, R010, R012, R013

### F003: Exact node acceptance and recovery

Accept only the expected controller identity/version/protocol/catalog and restore the prior generation on bounded failure.

**Requirements:** R007, R011, R012, R013

### F004: Managed operator surfaces and safe evidence

Expose plan, apply, status, and rollback through the registered CLI/controller contracts with metadata-only receipts and complete docs.

**Requirements:** R013, R014, R015

## Tasks

### T001: Amend the fleet ADR with the bootstrap coupling point

**Feature:** F001
**Priority:** high
**Type:** modify
**Likely files:** docs/adr/0034-fleet-control-plane-and-node-runtime-classes.md, docs/adr/0035-fleet-configuration-reconciliation.md, docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md, docs/ARCHITECTURE.md

Specify how an absent controller can be bootstrapped without weakening controller-first operation: one declared host, topology-fingerprinted plan, fixed SSH recovery operation, exact acceptance, and no workload/credential authority. Executor guidance: preserve the existing no-cross-host-scheduler and SSH-recovery decisions; update Feature 10 status only when implementation and gates actually ship.

**Acceptance criteria:**

- ADRs identify normal, bootstrap, acceptance, rollback, and manual-recovery states.
- Arbitrary SSH and implicit host discovery remain explicitly prohibited.
- Installation is separated from credential provisioning and workload deployment.

**Verification:**

- `python scripts/check_markdown_links.py --root .`
- `python -m mkdocs build --strict`
- `git diff --check`

### T002: Define bootstrap manifests, plans, receipts, and path validation

**Feature:** F001
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/fleet_bootstrap.py, tests/test_fleet_bootstrap.py
**Dependencies:** T001

Create immutable stdlib-only value objects and closed enums for phases/adapters. Implement canonical JSON serialization and SHA-256 hashing for the manifest, strict bounded identifiers, archive-entry validation, staging/install root containment, and allowlisted receipts. Executor guidance: use `pathlib.Path.resolve` plus platform-aware explicit checks, reject links before extraction, avoid `extractall`, and table-test Windows device names, drive/UNC paths, alternate streams, POSIX traversal, and symlink/reparse escapes.

**Acceptance criteria:**

- Equivalent inputs serialize byte-identically and yield the same bundle digest.
- Unknown fields, adapters, phases, hash algorithms, and protocol ranges fail closed.
- All unsafe path/archive cases are refused before target mutation.
- Receipt serialization contains only documented metadata fields.

**Verification:**

- `python scripts/run_tests.py tests/test_fleet_bootstrap.py -x -q`
- `python -m ruff check anvil_serving/fleet_bootstrap.py tests/test_fleet_bootstrap.py`

### T003: Resolve and fingerprint one bootstrap execution plan

**Feature:** F001
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/topology.py, anvil_serving/targets.py, anvil_serving/fleet_bootstrap.py, tests/test_topology.py, tests/test_fleet_bootstrap.py
**Dependencies:** T002

Add a typed bootstrap remote operation to `resolve_execution_plan` and derive the plan only from validated topology objects. Capture the topology snapshot identity and expected node/controller fields. Executor guidance: never compare raw URLs to infer ownership; use existing host/transport/resource IDs and surface structured validation errors.

**Acceptance criteria:**

- Exactly one declared, bootstrap-capable host resolves deterministically.
- Missing, ambiguous, incompatible, or policy-disallowed targets return typed precondition failures.
- Re-resolving after topology drift produces a different fingerprint and blocks apply.

**Verification:**

- `python scripts/run_tests.py tests/test_topology.py tests/test_topology_defaults.py tests/test_fleet_bootstrap.py -x -q`

### T004: Implement fixed-operation transport staging and target adapters

**Feature:** F002
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/transports.py, anvil_serving/fleet_bootstrap.py, anvil_serving/control_plane/bootstrap_shim.py, tests/test_transports.py, tests/test_fleet_bootstrap.py
**Dependencies:** T003

Add a closed bootstrap request to the controller transport and the narrowest equivalent SSH recovery operation. Stage a bundle, invoke a stdlib target shim with only a manifest path and operation enum, and implement explicit Windows/Linux install/supervisor adapters. Executor guidance: construct subprocess argv arrays, never shell strings; use existing SSH option builders; bound stdout/stderr; make external runners injectable; do not print from library code.

**Acceptance criteria:**

- Reachable controllers never invoke SSH.
- Recovery SSH cannot carry caller-supplied commands or arbitrary destinations and uses every hardened option in R004.
- Target verifies manifest and artifact hashes before install.
- Unsupported platform/privilege/supervisor states fail without guessed commands.

**Verification:**

- `python scripts/run_tests.py tests/test_transports.py tests/test_fleet_bootstrap.py -x -q`
- `python scripts/run_tests.py tests/test_remote_controller_regressions.py -x -q`

### T005: Add transactional activation, exact acceptance, and rollback

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/fleet_bootstrap.py, anvil_serving/control_plane/controller/server.py, anvil_serving/transports.py, tests/test_fleet_bootstrap.py, tests/test_controller.py
**Dependencies:** T004

Install into generation directories, preserve the prior active generation, switch through the explicit supervisor adapter, reconnect through `ControllerTransport(expected_node=...)`, and compare exact version/revision/protocol/catalog/health. On any failure, attempt one bounded rollback and verify the restored identity. Executor guidance: model each phase as data, persist the receipt before destructive phase transitions, and test every failure boundary with fake adapters rather than mutating local services.

**Acceptance criteria:**

- Successful acceptance proves exact expected node and software/protocol identity.
- Health-only, wrong-node, wrong-version, incompatible-protocol, and incompatible-catalog targets fail.
- Every injected phase failure yields verified rollback or an explicit manual-recovery state.
- Cleanup targets only the validated run staging directory.

**Verification:**

- `python scripts/run_tests.py tests/test_fleet_bootstrap.py tests/test_controller.py tests/test_remote_controller_regressions.py -x -q`

### T006: Register fleet bootstrap CLI and controller surfaces

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/cli.py, anvil_serving/commands/spec.py, anvil_serving/fleet_bootstrap.py, anvil_serving/control_plane/controller/server.py, docs/CLI-COMMAND-MANIFEST.json, tests/test_cli.py, tests/test_fleet_bootstrap.py
**Dependencies:** T003, T005

Add `fleet bootstrap plan`, `apply`, `status`, and `rollback` using existing confirmation and output-format idioms. Route all remote actions through typed operations, expose JSON suitable for MCP/controller callers, and regenerate the command manifest through `write_manifest`. Executor guidance: `plan` must be provably read-only; `apply` and `rollback` must refuse without explicit confirmation; do not hand-edit generated JSON.

**Acceptance criteria:**

- Help, command manifest, parser, and dispatch expose the four bounded verbs.
- Mutation verbs refuse before any transport call without confirmation.
- JSON and text output derive from the same returned structured result.
- Output redaction tests cover success and each failure phase.

**Verification:**

- `python scripts/run_tests.py tests/test_fleet_bootstrap.py tests/test_cli.py -x -q`
- `python -m anvil_serving.cli fleet bootstrap --help`

### T007: Document, threat-test, and run cross-platform release gates

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** docs/CLI.md, docs/ARCHITECTURE.md, docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md, tests/test_fleet_bootstrap.py
**Dependencies:** T006

Document prerequisites, plan/apply/accept/rollback states, public/private ownership, secret exclusions, and a generic two-node example using reserved synthetic addresses only where an address is necessary. Add adversarial tests for injection, path escape, host-key bypass, wrong identity, output leakage, drift, interruption, and partial failure. Executor guidance: run Windows CLI hygiene because argv, paths, and service adapters are load-bearing; do not perform a real remote install as part of unit verification.

**Acceptance criteria:**

- Documentation never implies bootstrap deploys workloads or provisions secrets.
- Threat tests cover every prohibited authority and every failure boundary.
- No real host identity, address, user path, credential, or raw remote output is committed.
- Focused, full, lint, docs, link, manifest, hygiene, and diff gates pass.

**Verification:**

- `python scripts/run_tests.py tests/test_fleet_bootstrap.py tests/test_transports.py tests/test_topology.py tests/test_controller.py -x -q`
- `python scripts/run_tests.py tests/ -q`
- `python -m ruff check anvil_serving tests`
- `python -m mkdocs build --strict`
- `python scripts/check_markdown_links.py --root .`
- `python scripts/run_tests.py tests/test_cli_reference_audit.py tests/test_docs_command_invocations.py -x -q`
- `git diff --check`
