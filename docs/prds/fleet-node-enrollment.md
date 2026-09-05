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

- R001: `anvil-serving fleet bootstrap plan --target host:<id>` is read-only and resolves one declared bootstrap-capable host, transport, expected node, platform, roots, and adapters through validated topology and a host-owned execution-plan seam. Do not introduce a second `--host` target syntax.
- R002: Planning and apply must fail before staging when topology validation fails, the host is ambiguous or absent, the host is not bootstrap-capable, a topology fingerprint changed, or target fields disagree with the execution plan.
- R003: The normal path for an already reachable node must use the authenticated `ControllerTransport`; `SSHRecoveryTransport` is permitted only for a declared node whose controller is absent or unavailable and only for the fixed bootstrap operation.
- R004: SSH recovery must use a literal topology-declared private/tailnet endpoint, explicit user, pinned identity file, strict known-host verification, batch mode, disabled forwarding, bounded connect/command timeouts, and bounded output. Reuse the hardened argv currently built inline by `SSHRecoveryTransport.execute`; extracting a shared tested helper is part of T004, not an existing API.
- R005: A bootstrap bundle must contain an exact wheel or install artifact, a stdlib-only fixed-operation target shim, and a closed manifest with schema version, package version, source commit, artifact hashes, expected node ID, supported platform, install adapter, supervision adapter, install root class, and minimum/maximum compatible controller protocol.
- R006: Every target path must be generated from validated bounded identifiers beneath a configured bootstrap staging/install root; traversal, absolute manifest paths, symlink/reparse escape, device names, alternate data streams, and unsafe archive entries must be rejected before extraction or replacement.
- R007: Secrets are separately provisioned and never included in bundles/receipts. All bootstrap verbs require a per-client `node-admin:bootstrap` grant; workload-read never grants bootstrap visibility. Apply/rollback also require local `bootstrap_authorized` policy and confirmation. Legacy shared tokens receive no new grant. SSH recovery requires the same local authorization.
- R008: `fleet bootstrap apply` must require the existing explicit confirmation idiom, revalidate topology and plan fingerprints immediately before mutation, stage to a unique temporary generation, verify every digest on the target, and invoke only the fixed shim operation described by the manifest.
- R009: Install immutable `generations/<manifest-digest>` directories and preserve the prior generation. Use a stable launcher and atomic, flushed pointer/journal transitions recording operation ID, prior, candidate, and phase. Unsupported atomic replacement fails precondition. Status reconciles the journal with inspected supervisor/runtime identity; crashes must yield verified rollback or manual recovery.
- R010: v1 supports only `python-wheel-venv` installation with preprovisioned `windows-scheduled-task` or `linux-systemd-user` supervisors, a stable launcher, and fixed bootstrap receiver. No elevation, supervisor creation, Docker upgrade, OS-package installation, or guessed layout is allowed. macOS and other layouts return typed unsupported preconditions.
- R011: Acceptance uses a newly constructed `ControllerTransport(expected_node=...)` after activation and requires authenticated exact node ID, package version, mandatory source commit or immutable artifact digest, compatible protocol, exact expected command-catalog digest, and bounded health. Same version/wrong build and health-only responses must fail.
- R012: If install, activation, restart, or acceptance fails, the workflow must attempt the bounded rollback when a prior generation exists, verify the restored controller identity, and otherwise return a typed manual-recovery state without deleting evidence or broad machine state.
- R013: Successful and failed runs must clean only their validated staging directory, retain a bounded metadata-only receipt, and never run broad temp, package-cache, container, or service cleanup.
- R014: Whole CLI/API/log output uses a command-specific allowlisted context/error envelope: host ID, topology/plan/artifact identity, adapters, phase, timestamps, acceptance/rollback, and fixed error codes only. Never serialize generic ExecutionPlan/TransportError dictionaries, endpoints, paths, environment, commands, secrets, or raw output.
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

## Closed v1 implementation contract

- Topology gains an optional closed host `bootstrap` declaration: enabled/authorized flags (both default false), absolute staging/install roots, exact Python executable and receiver path/digest, `python-wheel-venv` adapter, platform-matched supervisor enum and bounded preprovisioned supervisor ID. Paths stay internal/private. The supervisor and immutable receiver are machine prerequisites, not installed by this workflow.
- CLI targeting is exclusively `--target host:<id>`. Extend the execution resolver with a host-owned bootstrap operation; do not invent a synthetic model resource. Plans bind the topology fingerprint, manifest digest, expected identity and local authorization. Apply re-resolves and compares immediately before stage.
- Bundle wire format is a ZIP with exactly three regular entries: `manifest.json`, `runtime.whl`, `bootstrap_shim.py`. Bound the entire compressed and expanded bundle to 16 MiB, manifest to 16 KiB and shim to 256 KiB. Reject duplicate entries, links/reparse points, encryption, traversal, unsafe names and unsupported compression before installation; validate nested wheel paths too. SHA-256 binds every entry and the outer bundle.
- Controller staging is dedicated authenticated `POST /admin/bootstrap/stage`, content type `application/octet-stream`, exact Content-Length, `X-Anvil-Bundle-SHA256`, caller-generated UUID `X-Anvil-Operation-Id`, `X-Anvil-Plan-SHA256`, and `X-Anvil-Expected-Node`. Validate authorization, policy, identity and all closed header fields before reading the body. Lost-response retries reuse the UUID; any UUID binding mismatch refuses. Subsequent typed operations carry only the validated operation ID and plan digest.
- SSH requires a preprovisioned dedicated key/principal with a server-side forced command to the pinned receiver, no PTY and no forwarding. A fixed identity preflight proves receiver digest, expected owner and non-writable permissions before upload; drift refuses. The closed receiver protocol supports `identity|stage|activate|status|rollback`. Stdin begins with a 4-byte big-endian length and at most 4096 bytes of canonical JSON containing operation, UUID, plan digest, expected node, bundle digest and byte length, followed only for stage by the exact ZIP bytes. The transport supplies no caller command, path or argv. Host/root compromise is outside this threat model.
- An operation ID is a generated UUID bound durably to one manifest/plan digest; same ID with different bytes refuses. Stage uses a newly created contained directory with restrictive permissions. Duplicate same-digest requests return the recorded phase without repeating activation. Only validated staging owned by that operation may be removed.
- Digest domains: entry hashes cover exact entry bytes; manifest identity covers canonical UTF-8 JSON (sorted keys, compact separators, no newline) and names the immutable generation; the outer ZIP hash is transfer integrity; plan identity hashes canonical target/topology/artifact/adapter fields excluding operation UUID and timestamps. ZIP order is manifest, wheel, shim; use stored compression, DOS timestamp 1980-01-01 00:00:00, regular mode 0600, no comments/extra fields. The manifest hashes wheel and shim, not itself or the outer archive. Equivalent inputs therefore produce identical bundle bytes.
- Target installation uses a fresh venv and `pip install --no-index --no-deps` of the verified wheel; the installed controller remains stdlib-only. A stable preprovisioned launcher reads the atomic current pointer. A bounded activation child can outlive the old controller request while restarting the declared supervisor; it has one fixed operation, a deadline, and no self-healing loop.
- Journal fields are exactly operation ID, prior/candidate generation digests, phase, timestamps and fixed outcome codes. Flush before and after each pointer/supervisor boundary. Never overwrite an active or previous generation. Unsupported atomic replacement, ownership uncertainty or failed restored-identity verification returns manual recovery.
- A new authenticated identity response returns node ID, package version, mandatory source commit or artifact digest, protocol version, catalog digest and health. Catalog comparison is against the planned per-node allowlist, not a fleet-wide catalog. Use a fresh transport after every restart.
- A local per-client authorization policy assigns scopes to preprovisioned environment/file-backed credential references. Legacy authentication remains compatible for old operations but never grants new bootstrap/workload scopes. All bootstrap verbs require `node-admin:bootstrap`; unified workload reads require `workloads:read`. Never copy credentials or policy contents into bundles.
- All whole-response contexts and errors use new metadata-only serializers; seeded endpoint/path/token/command values must be absent from success, refusal, rollback, logs and CLI text/JSON.
- Authorization policy is optional local `--authorization-policy <path>`, schema `{"schema_version":1,"clients":[{"id":"operator","credential_env":"EXAMPLE_OPERATOR_TOKEN","scopes":["workloads:read"]}]}`. Each client has exactly one `credential_env` or `credential_file` reference, never an inline value. Limit policy size to 64 KiB and clients to 32; IDs use the bounded identifier grammar. Only the two new scopes are valid. Reject unknown keys, duplicate IDs/references/resolved credentials, a resolved credential equal to the legacy shared token, and tokens outside 16-4096 bytes. Missing/malformed policy disables new privileged surfaces with fixed errors. No references/material enter responses/logs. New scoped credentials do not authorize legacy operations.

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

### T008: Establish scoped node-admin and workload-read authorization

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/control_plane/authorization.py, tests/test_control_plane_authorization.py

Add the shared bounded per-client authorization policy reader and scope checker using preprovisioned credential references. Parse and resolve the closed policy schema, expose exact `node-admin:bootstrap` / `workloads:read` decisions, and keep legacy shared tokens outside both scopes. This task defines the reusable policy core only; it does not wire an endpoint or claim a bootstrap/workload surface.

**Acceptance criteria:**

- A valid preprovisioned client receives only its exact declared scope; `workloads:read` never implies `node-admin:bootstrap`.
- Unknown keys, invalid scopes, duplicate client IDs/references/resolved material, legacy-token equality, oversized policy/material, and missing secret material fail closed.
- Comparisons are constant-time and errors/log-safe serializers contain no credential reference, path, client material, or token-derived value.
- Loading the optional policy does not change legacy-operation authorization.

**Verification:**

- `python scripts/run_tests.py tests/test_control_plane_authorization.py -x -q`
- `python -m ruff check anvil_serving/control_plane/authorization.py tests/test_control_plane_authorization.py`

### T009: Wire scoped authorization into controller operations

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/control_plane/controller/http.py, anvil_serving/control_plane/controller/server.py, anvil_serving/control_plane/controller/cli.py, tests/test_controller.py
**Dependencies:** T008

Add `--authorization-policy <path>` to the controller serve parser and pass it through `serve`/`make_server` into the shared policy loader. Add one pre-dispatch scope gate that future bootstrap and workload operations can declare. Preserve existing legacy-operation authentication and token normalization. Authenticate before reading a body. The legacy `/tools/call` route identifies its operation inside JSON: perform its exact scope check after bounded parsing but before dispatch, store, transport, or collector access. For header-identified MCP operations, check scope before body consumption and require the parsed body to match the headers before dispatch. The dedicated bootstrap binary endpoint retains its stricter pre-body scope/header/policy gate in T012.

**Acceptance criteria:**

- Legacy, media-only, wrong-scope, missing-policy, and malformed-policy credentials receive fixed denials before controller dispatch.
- A correctly scoped client reaches only the operation declaring that exact scope.
- The serve parser accepts the optional policy path and forwards it unchanged; absence or invalid policy disables only new privileged operations. Tests cover parser-to-server plumbing and both header-identified and bounded-JSON authorization boundaries.
- Duplicate or legacy-equal resolved credentials cannot create an ambiguous controller principal.
- Existing controller operations and token-normalization regressions retain their prior behavior.

**Verification:**

- `python scripts/run_tests.py tests/test_controller.py tests/test_controller_token_normalization.py -x -q`
- `python -m ruff check anvil_serving/control_plane/controller/http.py anvil_serving/control_plane/controller/server.py anvil_serving/control_plane/controller/cli.py tests/test_controller.py`

### T010: Wire scoped authorization into router operator endpoints

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/front_door.py, anvil_serving/router/serve.py, tests/router/test_front_door_auth.py, tests/router/test_serve_cli.py
**Dependencies:** T008

Add `--authorization-policy <path>` to the router run parser and pass it through server construction into the shared policy loader. Add a distinct operator-scope gate to front-door dispatch without broadening the data-plane bearer token. This task supplies the final router authorization prerequisite for the workload PRD; it does not add the workload endpoint itself.

**Acceptance criteria:**

- Data-plane, legacy, media-only, wrong-scope, and missing-policy credentials cannot reach an operator-scoped handler.
- A `workloads:read` client can pass the operator gate but receives no bootstrap or ordinary legacy-operation authority.
- Denial happens before handler invocation and returns only fixed metadata-safe errors.
- Parser and server-construction tests prove the optional path reaches the policy loader; absent/invalid policy disables only new operator surfaces, never silently grants them.
- Existing chat, streaming, health, and ordinary router authentication tests retain their behavior.

**Verification:**

- `python scripts/run_tests.py tests/router/test_front_door_auth.py tests/router/test_serve_cli.py tests/test_control_plane_authorization.py -x -q`
- `python -m ruff check anvil_serving/router/front_door.py anvil_serving/router/serve.py tests/router/test_front_door_auth.py tests/router/test_serve_cli.py`

### T002: Define bootstrap manifests, bundles, receipts, and path validation

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

### T003: Parse bootstrap declarations and resolve one target

**Feature:** F001
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/topology.py, anvil_serving/targets.py, tests/test_topology.py, tests/test_topology_defaults.py
**Dependencies:** T002

Add the closed host bootstrap declaration and a typed bootstrap remote operation to `resolve_execution_plan`. Resolve only from validated topology objects and preserve existing default behavior when bootstrap is absent. This slice owns topology parsing, host capability, and target selection; bundle/plan hashing follows in T011.

**Acceptance criteria:**

- Exactly one declared, bootstrap-capable host resolves deterministically.
- Missing, ambiguous, incompatible, or policy-disallowed targets return typed precondition failures.
- Bootstrap declarations reject unsafe roots, mismatched platform/supervisor pairs, unbounded identifiers, and missing pinned receiver identity.
- Existing topologies without bootstrap declarations parse byte-for-behavior compatibly.

**Verification:**

- `python scripts/run_tests.py tests/test_topology.py tests/test_topology_defaults.py -x -q`

### T011: Bind bootstrap plans to topology and artifact identity

**Feature:** F001
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/fleet_bootstrap.py, tests/test_fleet_bootstrap.py
**Dependencies:** T002, T003

Construct the immutable execution plan from the resolved bootstrap target and hash only the canonical target/topology/artifact/adapter domain. Bind expected node, topology snapshot, manifest digest, receiver identity, policy state, and protocol/catalog expectations; exclude operation UUIDs and timestamps.

**Acceptance criteria:**

- Equivalent source and topology inputs produce byte-identical plans and digests.
- Re-resolving after topology, receiver, artifact, adapter, protocol, or catalog drift produces a mismatch that blocks apply before staging.
- Caller-supplied endpoint, path, command, node identity, or adapter overrides are structurally unavailable.
- Plan and error serialization omit endpoints, paths, credentials, commands, and raw topology dictionaries.

**Verification:**

- `python scripts/run_tests.py tests/test_fleet_bootstrap.py -x -q`
- `python -m ruff check anvil_serving/fleet_bootstrap.py tests/test_fleet_bootstrap.py`

### T004: Implement the fixed receiver protocol and target validation

**Feature:** F002
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/fleet_bootstrap.py, anvil_serving/control_plane/bootstrap_shim.py, tests/test_fleet_bootstrap.py
**Dependencies:** T008, T011

Implement the stdlib-only pinned receiver and fixed `identity|stage|activate|status|rollback` protocol. Every operation accepts only the closed framed metadata for operation ID, plan digest, and expected node; stage additionally binds the bundle digest/length and exact ZIP bytes. Install only after canonical manifest/archive/path/digest validation. This slice contains no network subprocess or controller endpoint wiring.

**Acceptance criteria:**

- Receiver input cannot carry caller command text, argv, destination, or arbitrary path fields.
- Malformed/oversized frames, wrong expected node, operation/plan rebinding, duplicate ZIP entries, unsafe wheel/archive paths, and digest mismatch fail before install.
- Target verifies receiver, manifest, wheel, shim, and outer-bundle identities before installation.
- Unsupported platform/privilege/supervisor states fail without guessed commands.

**Verification:**

- `python scripts/run_tests.py tests/test_fleet_bootstrap.py -x -q`
- `python -m ruff check anvil_serving/fleet_bootstrap.py anvil_serving/control_plane/bootstrap_shim.py tests/test_fleet_bootstrap.py`

### T012: Add the dedicated controller bootstrap stage endpoint

**Feature:** F002
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/control_plane/controller/http.py, anvil_serving/control_plane/controller/server.py, tests/test_controller.py, tests/test_fleet_bootstrap.py
**Dependencies:** T004, T009

Add authenticated `POST /admin/bootstrap/stage` as a bounded `application/octet-stream` endpoint. Validate `node-admin:bootstrap`, local policy, expected node, exact Content-Length, UUID, plan digest, and bundle digest headers before reading the body; then call the fixed receiver contract.

**Acceptance criteria:**

- Missing/wrong scope, disabled local policy, malformed or duplicate headers, wrong expected node, oversize length, and digest mismatch refuse before body consumption or staging.
- Lost-response retry with the same UUID and identical binding returns recorded phase; any binding mismatch refuses.
- Generic JSON tool bodies and legacy bearer tokens cannot invoke stage.
- Success and denial envelopes contain no headers, token material, endpoint, path, request bytes, or raw exception.

**Verification:**

- `python scripts/run_tests.py tests/test_controller.py tests/test_fleet_bootstrap.py -x -q`
- `python -m ruff check anvil_serving/control_plane/controller/http.py anvil_serving/control_plane/controller/server.py tests/test_controller.py`

### T013: Add the constrained SSH recovery transport

**Feature:** F002
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/transports.py, anvil_serving/cli.py, tests/test_transports.py, tests/test_cli.py
**Dependencies:** T004, T008

Extract the existing hardened SSH argv construction and add the dedicated forced-command recovery path. Perform the fixed receiver digest/owner/permission preflight, send only the closed frame and optional stage ZIP over stdin, and make arbitrary remote command/path/argv inputs impossible.

**Acceptance criteria:**

- Recovery uses literal declared endpoint/user/key, strict known-host checking, batch mode, no PTY/forwarding, bounded deadlines, and bounded output.
- Wrong receiver digest/owner/permissions, an unpinned key/host identity, or an undeclared endpoint refuses before upload.
- Non-stage frames contain operation ID, plan digest, and expected node and no trailing bytes; stage length must equal exact ZIP bytes.
- Existing non-bootstrap SSH recovery behavior remains unchanged and no shell-string execution is introduced.

**Verification:**

- `python scripts/run_tests.py tests/test_transports.py tests/test_cli.py -x -q`
- `python -m ruff check anvil_serving/transports.py anvil_serving/cli.py tests/test_transports.py tests/test_cli.py`

### T005: Add transactional target activation and crash recovery

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/fleet_bootstrap.py, anvil_serving/control_plane/bootstrap_shim.py, tests/test_fleet_bootstrap.py
**Dependencies:** T004

Install immutable generations and implement the flushed crash journal, atomic pointer transition, stable launcher handoff, and fixed preprovisioned Windows scheduled-task/Linux systemd-user supervisor adapters. This slice owns local target activation/status/rollback state only; controller identity and end-to-end orchestration follow.

**Acceptance criteria:**

- Unsupported atomic replacement, supervisor drift, ownership uncertainty, or absent prior generation returns a typed precondition/manual-recovery state as specified.
- Process death before/after each journal, pointer, and supervisor boundary reconciles to one declared state without overwriting active/previous generations.
- Only the preprovisioned platform-matched supervisor can be invoked; no elevation, creation, Docker, package-manager, or guessed command is available.
- Cleanup targets only the validated run staging directory.

**Verification:**

- `python scripts/run_tests.py tests/test_fleet_bootstrap.py -x -q`
- `python -m ruff check anvil_serving/fleet_bootstrap.py anvil_serving/control_plane/bootstrap_shim.py tests/test_fleet_bootstrap.py`

### T014: Expose mandatory controller build identity

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/control_plane/controller/http.py, anvil_serving/control_plane/controller/server.py, tests/test_controller.py, tests/test_remote_controller_regressions.py
**Dependencies:** T005, T009

Extend the authenticated identity response with mandatory source commit or immutable artifact digest plus protocol and catalog identity, using fixed metadata-only fields. Preserve node verification and ordinary controller behavior.

**Acceptance criteria:**

- Identity always includes exact node, package version, build identity, protocol version, catalog digest, and bounded health.
- Missing build identity, malformed digest, wrong-node request, and unauthenticated request fail with fixed safe errors.
- Same package version with a different build remains distinguishable.
- Identity output contains no endpoint, path, token, environment, command, or raw exception.

**Verification:**

- `python scripts/run_tests.py tests/test_controller.py tests/test_remote_controller_regressions.py -x -q`
- `python -m ruff check anvil_serving/control_plane/controller/http.py anvil_serving/control_plane/controller/server.py tests/test_controller.py`

### T015: Orchestrate transport selection, exact acceptance, and rollback

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/fleet_bootstrap.py, anvil_serving/transports.py, tests/test_fleet_bootstrap.py, tests/test_remote_controller_regressions.py
**Dependencies:** T012, T013, T014

Implement controller-first plan/apply/status/rollback orchestration. Revalidate topology and plan immediately before stage, choose SSH only for a declared absent/unavailable controller, construct a fresh expected-node transport after restart, verify the full identity, and attempt one bounded verified rollback on failure.

**Acceptance criteria:**

- Reachable controllers never invoke SSH; undeclared/unqualified recovery never invokes SSH either.
- Health-only, wrong-node, wrong-version/build, incompatible protocol/catalog, stale topology/plan, and missing credentials fail acceptance.
- Stage, install, activate, restart, reconnect, and accept failures yield verified rollback or explicit manual recovery within the deadline.
- Success/refusal/rollback contexts and logs exclude addresses, paths, credentials, raw commands, raw output, and capability-bearing URLs.

**Verification:**

- `python scripts/run_tests.py tests/test_fleet_bootstrap.py tests/test_remote_controller_regressions.py -x -q`
- `python scripts/run_tests.py tests/test_transports.py -x -q`

### T006: Register the fleet bootstrap CLI

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/cli.py, anvil_serving/commands/fleet.py, anvil_serving/fleet_bootstrap.py, tests/test_cli.py
**Dependencies:** T011, T015

Register `fleet bootstrap plan`, `apply`, `status`, and `rollback` using only `--target host:<id>` and the closed plan/operation fields. Wire parser and dispatch to the structured orchestration API. Command-manifest generation follows in T016.

**Acceptance criteria:**

- Help, parser, and dispatch expose exactly the four bounded verbs and reject `--host` or positional target aliases.
- Mutation verbs refuse before any transport call without confirmation.
- JSON and text output derive from the same returned structured result.
- Output redaction tests cover success and each failure phase.

**Verification:**

- `python scripts/run_tests.py tests/test_fleet_bootstrap.py tests/test_cli.py -x -q`
- `python -m anvil_serving.cli fleet bootstrap --help`

### T016: Register controller commands and generated manifest parity

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/commands/spec.py, docs/CLI-COMMAND-MANIFEST.json, tests/test_command_tree.py, tests/test_fleet_bootstrap.py
**Dependencies:** T006

Declare the typed bootstrap controller operations and regenerate the command manifest with repository helpers. Prove parser, dispatch, controller declaration, and generated documentation agree on verbs, fields, scope, confirmation, and result schema.

**Acceptance criteria:**

- The generated manifest contains only plan/apply/status/rollback with the reviewed arguments and `node-admin:bootstrap` scope.
- Parser/manifest/controller drift, missing confirmation metadata, and an extra command/path/argv field fail parity tests.
- Regeneration is deterministic and introduces no hand-edited manifest divergence.
- Controller operations return the same allowlisted result used by CLI JSON/text.

**Verification:**

- `python scripts/run_tests.py tests/test_command_tree.py tests/test_fleet_bootstrap.py -x -q`
- `python -c "from anvil_serving.commands.spec import write_manifest; write_manifest()"`
- `python scripts/run_tests.py tests/test_command_tree.py -x -q`
- `git diff --check`

### T007: Document, threat-test, and run cross-platform release gates

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** docs/CLI.md, docs/ARCHITECTURE.md, docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md, tests/test_fleet_bootstrap.py
**Dependencies:** T010, T016

Document prerequisites, plan/apply/accept/rollback states, public/private ownership, secret exclusions, and a generic two-node example using reserved synthetic addresses only where an address is necessary. Add adversarial tests for injection, path escape, host-key bypass, wrong identity, output leakage, drift, interruption, and partial failure. Executor guidance: run Windows CLI hygiene because argv, paths, and service adapters are load-bearing; do not perform a real remote install as part of unit verification.

**Acceptance criteria:**

- Documentation never implies bootstrap deploys workloads or provisions secrets.
- Threat tests cover every prohibited authority and every failure boundary.
- No real host identity, address, user path, credential, or raw remote output is committed.
- Focused, full, lint, docs, link, manifest, hygiene, and diff gates pass.

**Verification:**

- `python scripts/run_tests.py tests/test_fleet_bootstrap.py tests/test_transports.py tests/test_topology.py tests/test_controller.py tests/router/test_front_door_auth.py -x -q`
- `python scripts/run_tests.py tests/ -q`
- `python -m ruff check anvil_serving tests`
- `python -m mkdocs build --strict`
- `python scripts/check_markdown_links.py --root .`
- `python scripts/run_tests.py tests/test_cli_reference_audit.py tests/test_docs_command_invocations.py -x -q`
- `git diff --check`
