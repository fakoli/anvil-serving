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
- Controller staging is dedicated authenticated `POST /admin/bootstrap/stage`, content type `application/octet-stream`, exact Content-Length, `X-Anvil-Bundle-SHA256`, caller-generated UUID `X-Anvil-Operation-Id`, `X-Anvil-Plan-SHA256`, `X-Anvil-Target-Config-SHA256`, and `X-Anvil-Expected-Node`. Validate authorization, policy, identity and all closed header fields before reading the body. Lost-response retries reuse the UUID; any UUID binding mismatch refuses. Subsequent typed operations carry only the validated operation ID, plan digest and target configuration digest.
- SSH requires a preprovisioned dedicated key/principal with a server-side forced command to the pinned receiver, no PTY and no forwarding. A fixed identity preflight proves receiver digest, expected owner and non-writable permissions before upload; drift refuses. The closed receiver protocol supports `identity|stage|activate|status|rollback`. Stdin begins with a 4-byte big-endian length and at most 4096 bytes of canonical JSON containing operation, UUID, plan digest, expected node, bundle digest and byte length, followed only for stage by the exact ZIP bytes. The transport supplies no caller command, path or argv. Host/root compromise is outside this threat model.
- An operation ID is a generated UUID bound durably to one manifest/plan digest; same ID with different bytes refuses. Stage uses a newly created contained directory with restrictive permissions. Duplicate same-digest requests return the recorded phase without repeating activation. Only validated staging owned by that operation may be removed.
- Digest domains: entry hashes cover exact entry bytes; manifest identity covers canonical UTF-8 JSON (sorted keys, compact separators, no newline) and names the immutable generation; the outer ZIP hash is transfer integrity; plan identity hashes canonical target/topology/artifact/adapter fields excluding operation UUID and timestamps. ZIP order is manifest, wheel, shim; use stored compression, DOS timestamp 1980-01-01 00:00:00, regular mode 0600, no comments/extra fields. The manifest hashes wheel and shim, not itself or the outer archive. Equivalent inputs therefore produce identical bundle bytes.
- Target installation uses a fresh venv and `pip install --no-index --no-deps` of the verified wheel; the installed controller remains stdlib-only. A stable preprovisioned launcher reads the atomic current pointer. A bounded activation child can outlive the old controller request while restarting the declared supervisor; it has one fixed operation, a deadline, and no self-healing loop.
- Journal fields are exactly operation ID, prior/candidate generation digests, phase, timestamps and fixed outcome codes. Flush before and after each pointer/supervisor boundary. Never overwrite an active or previous generation. Unsupported atomic replacement, ownership uncertainty or failed restored-identity verification returns manual recovery.
- A new authenticated identity response returns node ID, package version, mandatory source commit or artifact digest, protocol version, catalog digest and health. Catalog comparison is against the planned per-node allowlist, not a fleet-wide catalog. Use a fresh transport after every restart.
- A local per-client authorization policy assigns scopes to preprovisioned environment/file-backed credential references. Legacy authentication remains compatible for old operations but never grants new bootstrap/workload scopes. All bootstrap verbs require `node-admin:bootstrap`; unified workload reads require `workloads:read`. Never copy credentials or policy contents into bundles.
- All whole-response contexts and errors use new metadata-only serializers; seeded endpoint/path/token/command values must be absent from success, refusal, rollback, logs and CLI text/JSON.
- Authorization policy is optional local `--authorization-policy <path>`, schema `{"schema_version":1,"clients":[{"id":"operator","credential_env":"EXAMPLE_OPERATOR_TOKEN","scopes":["workloads:read"]}]}`. Each client has exactly one `credential_env` or `credential_file` reference, never an inline value. Limit policy size to 64 KiB and clients to 32; IDs use the bounded identifier grammar. Only the two new scopes are valid. Reject unknown keys, duplicate IDs/references/resolved credentials, a resolved credential equal to the legacy shared token, and tokens outside 16-4096 bytes. Missing/malformed policy disables new privileged surfaces with fixed errors. No references/material enter responses/logs. New scoped credentials do not authorize legacy operations.

### Canonical bootstrap value contracts

T002 owns the following exact v1 values. Later tasks consume these definitions;
they do not introduce alternate receipt fields or serialization.

- Manifest schema is `anvil-serving.fleet-bootstrap-manifest/v1`. Exact fields
  are schema, package_version, source_commit, runtime_sha256, shim_sha256,
  expected_node, platform, install_adapter, supervisor_adapter,
  install_root_class, controller_protocol_min and controller_protocol_max.
  Digests are exactly64 lowercase hexadecimal characters; source_commit is
  exactly40 lowercase hexadecimal characters. Node IDs use
  `[A-Za-z][A-Za-z0-9_-]{0,63}`. Version uses three decimal components of1..9
  digits, optionally followed by a/b/rc and1..9 digits; no arbitrary labels.
- Platform is windows/linux. Install adapter is python-wheel-venv. Supervisor
  is windows-scheduled-task for Windows or linux-systemd-user for Linux.
  install_root_class is exactly user: it identifies the explicitly declared
  user-managed root, never derives or grants a filesystem location.
- Controller protocol bounds are exact valid ISO calendar dates YYYY-MM-DD,
  inclusive and ordered min<=max. They refer to the controller's existing MCP
  protocol date (currently2026-07-28), not the separately versioned receiver
  framing schema. No numeric version, wildcard or unparsable range is accepted.
- Canonical JSON is UTF-8, sorted keys, compact separators, ensure_ascii=true,
  allow_nan=false, no trailing newline. Reject duplicate keys at every depth.
  Inbound manifests and outer bundles must already be canonical; do not
  silently normalize a different byte identity.
- The outer compressed and expanded16 MiB bounds are separate from an additional
 16 MiB aggregate expanded nested-wheel ceiling. Limit the wheel to4096
  entries, names to1024 UTF-8 bytes, and components to255 UTF-8 bytes. Nested
  wheel members may use stored/deflated compression; the outer bundle remains
  stored-only. Reject path/link/encryption/collision violations in both layers.
  Wheel entry count and declared expansion bounds are checked before payload
  reads; actual bounded reads and CRC validation must agree with declarations.
- Receipt schema is `anvil-serving.fleet-bootstrap-receipt/v1`. Exact fields:
  schema, operation_id, host, topology_sha256, plan_sha256, manifest_sha256,
  bundle_sha256, platform, install_adapter, supervisor_adapter, phase, outcome,
  created_at, updated_at, acceptance, rollback, error_code and trigger_error_code. No generic
  context, message, exception, path, endpoint or command field exists.
  Identity/digest/adapter fields are null until validated; supplied values obey
  the manifest grammar and platform pairing. operation_id is null for planning
  or early refusal, otherwise a canonical lowercase hyphenated UUIDv4.
  Timestamps are exact UTC microsecond-Z strings with created_at<=updated_at.
- Phase enum is planned, staged, verified, installed, activated, restarted,
  accepted, rollback-started, rolled-back, manual-recovery, refused, cleanup-failed.
  Outcome enum is pending/success/error. Acceptance enum is
  not-checked/accepted/rejected; rollback enum is
  not-required/pending/verified/failed/unavailable.
  Fixed error codes are invalid-contract, unsupported-platform, unsafe-path,
  invalid-bundle, digest-mismatch, topology-drift, authorization-denied,
  precondition-failed, transport-unavailable, receiver-mismatch, install-failed,
  activation-failed, restart-failed, acceptance-failed, rollback-failed,
  cleanup-failed, timeout and internal-error; success has null error_code.
- Receipt consistency: planned is success/not-checked/not-required with no
  operation ID; staged through restarted are pending/not-checked/not-required;
  accepted is success/accepted/not-required. These phases have null error_code.
  rollback-started is pending with rollback=pending and a fixed triggering
  error; rolled-back is error with rollback=verified and that error retained.
  manual-recovery is error with rollback=failed/unavailable and a fixed error.
  refused is error/not-checked/not-required with a fixed error. Recovery phases
  allow acceptance=not-checked/rejected, never accepted. Every phase other than
  planned/refused requires an operation ID and all validated identity/digest/
  adapter fields; planned requires all those fields except operation ID.
  Early refused receipts may omit them, but never emit unvalidated input.
  trigger_error_code is null outside cleanup-failed.
- Cleanup runs after a terminal accepted/rolled-back/manual-recovery/refused
  disposition, not while rollback is pending. If owned staging cleanup fails,
  phase becomes cleanup-failed, outcome=error and error_code=cleanup-failed;
  preserve the last validated acceptance/rollback statuses and put the prior
  error_code in trigger_error_code (null after successful acceptance, otherwise
  the fixed primary failure). Thus cleanup-failed permits accepted/not-required
  with no trigger, or not-checked/rejected with not-required/verified/failed/
  unavailable and a nonnull fixed trigger. It requires the validated operation
  ID and identities of the staging owner. Cleanup failure never rewrites an
  accepted runtime as rejected or a verified rollback as failed, and never
  drops a primary error. No staging exists for a pre-plan refusal.
- Pure T002 path checks are preflight, not a race-free extraction capability.
  Reject both POSIX and Windows lexical hazards on every platform; inspect
  existing ancestors with lstat/reparse/junction checks and containment after
  resolve. T004/T005 must recheck at safe create/open/replace boundaries.
  T002 performs no extraction, install, transport, topology mutation or
  operation-ID generation.

### Host-owned bootstrap topology and resolution contract

T003 owns this closed declaration and pure resolution seam. T002's local
filesystem containment checks remain separate; never inspect remote paths with
the caller's filesystem during topology parsing.

- Add frozen HostBootstrap and optional Host.bootstrap, default None. The
  bootstrap table accepts exactly enabled and bootstrap_authorized (exact
  booleans, default false), execution_runtime (required declared runtime ID),
  staging_root, install_root, python_executable, receiver_path, receiver_sha256,
  install_adapter, supervisor_adapter and supervisor_id. All except the two
  flags are required; unknown keys refuse. Host.os is the only OS authority and
  must be windows/linux. Receiver SHA-256 is exactly64 lowercase hex characters.
  Adapter values are the canonical T002 enums; supervisor_id matches
  [A-Za-z][A-Za-z0-9_.-]{0,63}.
- execution_runtime names exactly one declared runtime on the same host with
  role native. The name is not fixed: node-native is only a synthetic example.
  Reject missing, cross-host or non-native references. Existing Docker/WSL
  resource runtimes do not become bootstrap runtimes implicitly.
- All four paths are NFC-normalized strings of at most1024 UTF-8 bytes, with
  at most255 UTF-8 bytes per component, already canonical and absolute for the
  declared host OS. Use PureWindowsPath/PurePosixPath and explicit lexical
  checks, never resolve/exists/stat, environment expansion or remote I/O.
  Reject root-only paths, relative/root-relative paths, empty/dot/dot-dot
  components, controls/surrogates and mixed separators. Windows additionally
  rejects UNC/device paths, alternate data streams, reserved device components
  and trailing dots/spaces; require a drive-rooted path with backslashes.
  Linux requires a single leading slash and forbids backslashes.
  Staging and install roots must be distinct and non-nested (case-insensitive
  on Windows). Exact executable/receiver paths are separately validated;
  later create/open/replace operations still perform their own containment
  and link/reparse checks.
- Add CommandSpec.execution_policy host-bootstrap, restricted to the exact
  command name controller-bootstrap, resource_role=None, runtime roles
  (native,), transports (controller, ssh), recovery_capable=True,
  gpu_role_required=False and host OSes (windows, linux). Mutation class is
  write. Other specifications cannot adopt this policy. This is a typed
  resolution declaration, not registration of a new remotely callable tool.
- resolve_execution_plan branches before resource-owner preflight and accepts
  only an explicit host:<id> using the bounded canonical node-ID grammar.
  No host-role selector, inferred target, synthetic resource, capacity/GPU
  lookup, direct local execution or caller-selected SSH is allowed. The host
  declaration must be enabled and bootstrap_authorized. Auto and controller
  both select the declared controller even when the target is the caller.
- Require exactly one controller transport on that host/runtime allowing
  controller-bootstrap, with expected_node equal to the host ID, a declared
  auth reference and no unauthenticated-loopback exemption. Permit zero or one
  same-host/runtime SSH transport allowing that operation; ambiguity refuses.
  SSH remains an internal recovery candidate, never selected here. Receiver,
  credential and forced-command checks remain T013/T015 gates.
- Reuse ExecutionPlan with resource_host/runtime/resource/endpoint, gpu_role and
  capacity all None; add an internal frozen host_bootstrap field. Preserve
  validated command identity using the existing resolver, but translate its
  errors to fixed bootstrap-command-identity-invalid prose/code. For this
  policy only, as_dict allowlists command, topology, topology_snapshot,
  command_host/runtime, execution_host/runtime, target, transport,
  transport_id, recovery_transport_id and expected_node. No overlay payload,
  root, executable, receiver, address, endpoint, auth reference, fingerprint,
  known-host path or raw declaration is emitted.
- Resolution uses TargetResolutionError with details.reason_code and fixed
  prose: bootstrap-target-required, bootstrap-host-missing,
  bootstrap-contract-missing, bootstrap-disabled,
  bootstrap-authorization-denied, bootstrap-runtime-invalid,
  bootstrap-controller-missing, bootstrap-controller-ambiguous,
  bootstrap-controller-identity-invalid, bootstrap-recovery-ambiguous,
  bootstrap-transport-invalid and bootstrap-command-identity-invalid.
  Invalid command declarations remain CommandSpecError. No input interpolation
  or raw topology exception appears in bootstrap refusals.
- Preserve the exact pre-feature topology snapshot digest for absent bootstrap:
  remove only the new None host field from the asdict hash input. Every
  declared bootstrap field participates in the fingerprint. T011 later hashes
  the full private plan domain; never hash only its public projection.

### Bootstrap plan identity contract

T011 owns one frozen BootstrapPlan produced by
build_bootstrap_plan(execution: ExecutionPlan, manifest: BootstrapManifest).
Neither the builder nor the value constructor accepts individual field overrides;
a custom frozen-value constructor may accept only these two resolved inputs.
There is no public from_dict or private-canonical serializer. A safe fixed repr
must not expose internal paths or resolved transport values.

- Plan schema is anvil-serving.fleet-bootstrap-plan/v1. The private hash domain has exactly schema, host, execution_runtime, topology_sha256, manifest_sha256, expected_node, platform, staging_root, install_root, python_executable, receiver_path, receiver_sha256, install_adapter, supervisor_adapter, install_root_class, supervisor_id, bootstrap_enabled, bootstrap_authorized, expected_protocol_version and expected_catalog_sha256. No stored plan_sha256 is hashed into itself. Exclude operation UUIDs, timestamps, bundle digest, receipt state, endpoint/auth/SSH values, command identity and raw topology dictionaries. The topology digest transitively binds those declared transport values; manifest identity binds the exact artifact and compatibility contract.
- Canonicalize the private plan with the existing canonical_json_bytes and hash SHA-256. Public to_dict has exactly schema, host, topology_sha256, plan_sha256, manifest_sha256, expected_node, platform, install_adapter, supervisor_adapter, expected_protocol_version and expected_catalog_sha256. Never compute plan identity from this public projection.
- Construction requires exact ExecutionPlan and BootstrapManifest values, the host-bootstrap command policy, controller transport and complete mutually consistent execution_host, native execution_runtime, host_bootstrap, selected host:<id> and transport_expected_node. Require resource_host, resource_runtime, resource, resource_endpoint, gpu_role and capacity all None. Require enabled and bootstrap_authorized true, an authenticated controller binding, and matching host/runtime/bootstrap references. Recheck pure declared HostBootstrap paths and identifiers without local filesystem, environment or network access; convert their validation failures to fixed bootstrap errors.
- Manifest expected_node/platform/install_adapter/supervisor_adapter must equal resolved topology values and install_root_class must be user. Reuse T002 bounded ID/digest/platform grammars; no implicit platform, path or identity conversion is permitted.
- expected_protocol_version comes from control_plane.mcp.protocol.PROTOCOL_VERSION, must be a canonical valid date within the inclusive manifest protocol range and equal controller_protocol_max for v1. Import runtime seams lazily where necessary: topology already imports bootstrap adapter enums, so a module-level reverse import would create a cycle.
- expected_catalog_sha256 is the configured per-node operation-allowlist identity, not the full tool-schema catalog. Export controller_operation_catalog_sha256(operations) for T014 reuse after that controller's existing catalog validation. Accept only an exact tuple of 1..256 unique exact strings matching [A-Za-z][A-Za-z0-9_-]{0,63}, including controller-bootstrap. Sort lexically, then SHA-256 the canonical UTF-8 JSON object with exactly schema=anvil-serving.controller-operation-catalog/v1 and operations=the sorted list. Do not silently normalize hyphen/underscore spellings. Use the same JSON encoding rules as canonical_json_bytes; this fixed, already bounded list may exceed the generic JSON helper's 128-node ceiling, so encode the closed validated catalog directly without weakening manifest/receipt decoding limits.
- The builder obtains the allowlist only from execution.transport_allowed_operations. No protocol/catalog override exists. T014 computes the same identity after its installed catalog validates every declared operation; unknown operations must never be advertised as accepted merely because their strings hash.
- Shape/digest/identifier failures use invalid-contract; false policy flags use authorization-denied; unsupported platform/supervisor pairing uses unsupported-platform; target/runtime/node/adapter/protocol/catalog inconsistency uses precondition-failed. All messages are fixed and input-free. A later apply comparison uses topology-drift for changed topology, receiver-mismatch for changed receiver, digest-mismatch for changed artifact and precondition-failed for other plan drift, before any staging. T011 itself performs no apply, staging, identity probe or filesystem mutation.
- Tests construct real parsed synthetic Windows/Linux topologies and resolve them through resolve_execution_plan before building. Prove equivalent inputs have identical plan hashes; each private target/root/receiver/artifact/policy/protocol/catalog change either changes identity or refuses; catalog order is canonical, duplicates/malformed IDs/missing bootstrap/257 entries refuse, and 256 valid entries succeed. Check constructor/builder reject individual override keywords, public dict and repr omit seeded private values, and existing manifest/bundle/receipt tests retain their behavior.

### Fixed receiver request framing contract

T004.1 supplies pure framing; T004.4 adds the rollback trigger described below.
Neither performs receiver provisioning, target file read,
bundle validation, staging, installation, transport or authorization decision.

- Export ReceiverOperation with exactly identity, stage, activate, status and rollback. Frozen BootstrapReceiverFrame has operation, expected_node and optional operation_id, plan_sha256, target_config_sha256, bundle_sha256, bundle_length and trigger_error_code; schema is fixed to anvil-serving.fleet-bootstrap-receiver-frame/v1. Direct constructors require exact enum/value types; from_dict requires exact primitive wire types and the exact operation-specific key set. Unknown keys, explicit nulls for omitted fields and Boolean integer substitutes refuse.
- identity has exactly schema, operation and expected_node. stage additionally requires operation_id, plan_sha256, target_config_sha256, bundle_sha256 and bundle_length. activate/status additionally require operation_id, plan_sha256 and target_config_sha256, and forbid bundle fields. rollback requires those same fields plus its exact trigger_error_code and forbids bundle fields. Every other operation forbids trigger_error_code. Reuse the existing canonical node, UUIDv4 and lowercase SHA-256 grammars. bundle_length is an exact integer from 1 through MAX_BUNDLE_BYTES.
- encode_receiver_frame(frame, payload=b"") returns four-byte unsigned big-endian JSON length, canonical JSON bytes and stage payload. decode_receiver_frame(raw) returns the validated immutable frame and exact payload bytes. Both accept exact bytes, reject metadata lengths outside 1..4096 before JSON parsing, enforce canonical duplicate-free JSON through the existing decoder, and bound total input before slicing. Identity and non-stage requests have no trailing bytes; stage requires exactly bundle_length bytes, no truncation or trailing bytes.
- The framing layer checks the outer payload SHA-256 against bundle_sha256 before returning or encoding stage bytes, using digest-mismatch on disagreement. It does not parse ZIP or install anything: structurally framed non-ZIP bytes are still subject to later validate_bundle. Malformed framing, wrong types, unknown fields and noncanonical JSON use invalid-contract with fixed input-free messages. to_dict serializes only the operation-specific allowlist, never object __dict__.
- target_config_sha256 binds the trusted local target configuration independently of the opaque full plan hash. A transport first compares receiver identity and configuration digests against BootstrapPlan, then includes the expected configuration digest on every non-identity request. The receiver must re-read and compare it at each stage/activation mutation boundary. This closes configuration drift between identity preflight and upload. Controller stage additionally carries X-Anvil-Target-Config-SHA256 and applies the same pre-body check; later typed calls carry that digest with operation_id and plan_sha256.
- Tests cover all five operation round trips, exact literal byte framing, 0/4097 metadata lengths, truncated header/JSON/payload, trailing data, duplicate keys, noncanonical JSON, invalid UTF-8, unknown operations/fields, explicit null/Boolean substitutions, UUID/digest/node bounds, maximum payload, digest mismatch, immutable values and no seeded private payload in errors. No filesystem/network/environment/mutation seam is called.

### Receiver rollback trigger and result framing contract

T004.4 is a fix-forward of the unpublished request contract: rollback must
carry its initiating failure so post-restart acceptance failure can be bound
durably without caller prose or an inferred cause. T004.3 then closes only
pure result values/codecs; permissions, filesystem state and operation policy
remain later receiver responsibilities.

- Add optional trigger_error_code to BootstrapReceiverFrame, required only for rollback and omitted for every other operation. Its exact enum is BootstrapErrorCode except cleanup-failed: cleanup requires an original cause, not itself. Rollback wire adds this one required enum string to its existing closed fields; explicit null, missing, wrong type and extra-operation use refuse. Every other operation remains byte-identical. Revalidate exact frame type/schema/fields before to_dict or encoding, including tampered frozen values. This fixes an unpublished v1 candidate; it does not claim an installed protocol migration.
- The trigger is evidence, never permission to roll back an arbitrary successful operation. T004/T005 authorize the operation/state separately, bind the initiating code when rollback starts, and refuse a changed trigger on an identical-UUID retry before action. Internal automatic rollback binds its own fixed initiating failure. No trigger text, command, path or environment value is accepted.
- Result schema is anvil-serving.fleet-bootstrap-receiver-result/v1. MAX_RECEIVER_RESULT_BYTES is 4096. encode_receiver_result(result) and decode_receiver_result(raw) use exactly four-byte unsigned big-endian JSON length, canonical JSON and EOF; length is 1..4096 and total input is checked before slicing/parsing. Reuse the existing duplicate-free bounded decoder, exact primitive/enum validators, closed field sets and canonical byte comparison. No result payload/trailing bytes exist.
- Add BootstrapPermissionVerdict with exactly owner-readonly, owner-writable, untrusted-writable, indeterminate and unsupported. Add frozen BootstrapReceiverProtocolError, BootstrapReceiverIdentityResult and BootstrapReceiverOperationResult, plus their BootstrapReceiverResult union. Constructors, to_dict and codecs revalidate exact types/schema/fields and reject subclasses/tampering with fixed input-free invalid-contract errors. Only closed safe fields are retained/serialized/repr; no raw mapping, path, owner/UID/ACL detail, command, exception or provider output is stored.
- BootstrapReceiverProtocolError has exactly schema, operation=null, expected_node=null, outcome=error and error_code=invalid-contract. These are fixed init=False constants: an undecodable request or unavailable trusted identity cannot supply guessed/partially echoed fields.
- BootstrapReceiverIdentityResult has exactly schema, operation=identity, expected_node, outcome, receiver_sha256, target_config_sha256, receiver_permission, target_config_permission and error_code. Node uses the existing exact grammar; digests are exact lowercase SHA-256 or null when unmeasurable, and permissions always use exact verdict enums. Success requires both digests, receiver owner-readonly, configuration owner-readonly or owner-writable, and null error. Error requires one of receiver-mismatch, precondition-failed, unsupported-platform, invalid-contract or internal-error; pending is forbidden. A codec verifies shape, not measured digest truth or permissions.
- BootstrapReceiverOperationResult has exactly schema, operation, expected_node, operation_id, plan_sha256, target_config_sha256, bound, bundle_sha256, bundle_length, manifest_sha256, phase, outcome, error_code and trigger_error_code. Operation is stage, activate, status or rollback only; UUID/node/digests reuse existing grammars and bound is exact bool. Bound=true requires bundle/manifest digests and exact integer bundle_length in 1..MAX_BUNDLE_BYTES, asserting a validated durable binding matching all echoed request identity. Bound=false requires all three bundle fields null, phase refused, outcome error, nonnull fixed error and null trigger; it reveals no conflicting stored identity.
- Bound ordinary phases staged, verified, installed, activated and restarted are pending with both error fields null. Rollback-started is pending with a nonnull non-cleanup original error and null trigger. Rolled-back and manual-recovery are error with a nonnull non-cleanup original error and null trigger. Cleanup-failed is error with error_code=cleanup-failed and required nonnull non-cleanup trigger_error_code. No planned, accepted or bound refused result exists; controller acceptance and cleanup after accepted success remain orchestration/receipt work.
- Stage and status may report any bound receiver-owned phase, including already-advanced or rollback state on an identical lost-response retry. Activate may report installed, activated, restarted, rollback-started, rolled-back, manual-recovery or cleanup-failed. Rollback may report rollback-started, rolled-back, manual-recovery or cleanup-failed. Every operation may instead return unbound refused. Use existing fixed error enums with phase consistency, not per-operation allowlists that prevent status from reporting historical errors.
- match_receiver_result(frame,result) validates exact input objects, raises fixed invalid-contract for protocol-error, and otherwise requires exact operation/node plus operational UUID/plan/config equality. Bound stage additionally matches request bundle SHA/length. Bound rollback requires its original error (error_code, or trigger_error_code for cleanup-failed) equal the request trigger. A well-formed but mismatched response raises fixed receiver-mismatch. The matcher returns the validated result unchanged and performs no state/IO/authorization. Expected measured receiver/config digests and permission policy are compared separately during T004/T013 preflight.
- Tests use literal canonical bytes for all three variants and every phase/operation row, malformed prefix/length/truncation/trailing/UTF-8/duplicate/noncanonical JSON, exact field/type/enum/null/subclass failures, binding and trigger mismatches, permission combinations, tampered values, bounds and seeded private-data absence. No file, network, environment, staging, installation, permission measurement or live availability is implied.

### Receiver ownership and staging boundary

The preprovisioned receiver is a deterministic self-contained Python ZIP
application, not an import from the candidate wheel. Its byte-exact embedded
fleet_bootstrap validator is the sole bundle validator. Candidate shim bytes
remain inert before verification; candidate paths are never added to sys.path.

Trusted local configuration is a fixed sibling of the receiver, canonical JSON
bounded to 16 KiB. Its exact target-config/v1 fields are schema, expected_node,
platform, staging_root, install_root, python_executable, receiver_path,
receiver_sha256, install_adapter, supervisor_adapter, install_root_class,
supervisor_id, bootstrap_enabled and bootstrap_authorized. The schema string is
anvil-serving.fleet-bootstrap-target-config/v1. All values derive exactly from
BootstrapPlan, with the target-config schema replacing the plan schema;
bootstrap_target_config_sha256(plan) hashes that closed canonical domain.
Neither a frame nor the candidate bundle supplies paths or local policy.

T004.2 closes this pure configuration value before filesystem work:

- Add frozen BootstrapTargetConfig in fleet_bootstrap.py with those exact fields and exact enum/Boolean types; schema is fixed, init=False. Its constructor validates the canonical node/digest/supervisor-ID grammar, platform/adapter pairing, user root class, four canonical OS-specific paths and nonoverlapping staging/install roots. False Boolean policy values are valid configuration, not permission to operate; later receiver authorization requires both true.
- Move the existing pure _valid_bootstrap_path, _valid_bootstrap_component and _bootstrap_paths_overlap implementations and their path-only constants from topology.py to fleet_bootstrap.py, reusing identical existing Windows device/forbidden-character sets there. topology imports the same helper names for compatibility; the plan builder uses them locally. Preserve all existing topology and valid-input path behavior. Keep topology's unrelated Unicode handling and secret/transport validation intact. No new path grammar, filesystem call or topology dependency belongs in the target decoder.
- BootstrapTargetConfig.from_bytes accepts only exact nonempty bytes bounded to 16 KiB, duplicate-free canonical JSON and the exact 14-key field set. No missing/null/extra fields, primitive coercions, enum subclasses or caller-selected schema are accepted. Reuse _decode_json, canonical_json_bytes, _validate_exact_fields and existing fixed errors; compare reserialized bytes for exact canonical identity.
- BootstrapTargetConfig.from_plan accepts only an exact BootstrapPlan and copies only the closed target fields through the validated constructor. No field-override keywords are accepted. bootstrap_target_config_sha256(plan) is exactly SHA-256 of that target's canonical private bytes, not of BootstrapPlan.to_dict or plan_sha256. Target identity excludes topology, execution runtime, artifact, catalog, protocol, operation ID and timestamps, while the full plan continues to bind them separately.
- The only full configuration serializer is explicitly named to_private_bytes. Revalidate exact field types and bounds before serialization, hash or repr, with a fixed metadata-only repr containing only expected_node and target_config_sha256. Do not add a generic public to_dict or print paths/supervisor IDs/policy contents. Unsafe/inconsistent values use fixed invalid-contract errors; unsupported platform/supervisor pairing uses unsupported-platform. This value layer performs no file read/write, environment lookup, target permission check, receiver hashing, provisioning or authorization decision.
- Tests prove cross-OS canonical round trips, exact field-set/type/size/canonical-JSON failures, false flags retained without granting authority, every target field changing the digest or refusing, excluded full-plan fields leaving target identity unchanged, repr/error privacy, frozen values and no field overrides. A clean subprocess importing only fleet_bootstrap and decoding configuration must not import topology/targets/controller; existing topology/target/plan regressions prove the shared-helper relocation is behavior-preserving.

A later receiver identity result must prove receiver_sha256 and
target_config_sha256 plus bounded permission verdicts, without paths, owner IDs
or ACL details. Inspect the same opened file identity while hashing and recheck
identity before use. File ownership/permission checks are platform adapters,
not stat UID guesses on Windows. Untrusted-writable, indeterminate or unsupported
checks fail closed. Owner-writable local configuration is permitted; the receiver
artifact itself must be owner-readonly. Ancestor owner write is not equivalent
to an untrusted writer. Trusted administrator/root compromise is out of scope.

Stage owns only operations/<canonical UUID> under the trusted staging root.
Bind operation_id, plan_sha256, target_config_sha256, expected_node,
bundle_sha256, bundle_length, manifest_sha256 and phase durably. Identical lost
response retries may return/resume that bound operation; any identity mismatch
refuses before writing. Validate all bundle and manifest identities plus local
platform/adapters before a verified phase. Exact result framing, permission
adapters, file-handle-safe staging and receiver packaging are closed before the
remaining T004 implementation is dispatched; T004.1 depends only on the fully
specified pure request contract above. They remain implementation prerequisites,
not evidence of installed receiver availability.

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
**Likely files:** anvil_serving/topology.py, anvil_serving/targets.py, tests/test_topology.py, tests/test_targets.py
**Dependencies:** T002

Implement the Host-owned bootstrap topology and resolution contract above. Add the closed host declaration and typed host-bootstrap execution policy to resolve_execution_plan. Resolve only from validated topology objects; preserve exact legacy snapshot identity and output when bootstrap is absent. This slice owns pure topology parsing, host capability and controller-first target selection; bundle/plan hashing follows in T011. No filesystem inspection, transport request, CLI registration or installation belongs here.

**Acceptance criteria:**

- Exactly one declared, bootstrap-capable host resolves deterministically.
- Missing, ambiguous, incompatible, or policy-disallowed targets return typed precondition failures.
- Bootstrap declarations reject unsafe roots, mismatched platform/supervisor pairs, unbounded identifiers, and missing pinned receiver identity.
- Existing topologies without bootstrap declarations parse byte-for-behavior compatibly.
- Pure cross-OS path tests reject unsafe inputs without filesystem or environment access; every declared bootstrap field changes topology identity while absence preserves the exact legacy digest.
- Resolver tests cover every fixed refusal code, explicit host-only targeting, same-host controller-first behavior, optional recovery ambiguity, mandatory authenticated expected-node binding, and seeded private-value exclusion from whole output.

**Verification:**

- `python scripts/run_tests.py tests/test_topology.py tests/test_targets.py tests/test_topology_defaults.py -x -q`
- `python -m ruff check anvil_serving/topology.py anvil_serving/targets.py tests/test_topology.py tests/test_targets.py`

### T011: Bind bootstrap plans to topology and artifact identity

**Feature:** F001
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/fleet_bootstrap.py, tests/test_fleet_bootstrap.py
**Dependencies:** T002, T003

Implement the Bootstrap plan identity contract above using only the two resolved builder inputs. Bind the entire private plan domain and expose only the exact public projection. Keep per-node allowlist hashing distinct from generic JSON decoding bounds and preserve lazy imports at the topology/bootstrap seam. This task is pure planning; later apply owns fresh re-resolution and pre-stage comparison.

**Acceptance criteria:**

- Equivalent source and topology inputs produce byte-identical plans and digests.
- Re-resolving after topology, receiver, artifact, adapter, protocol, or catalog drift produces a mismatch that blocks apply before staging.
- Caller-supplied endpoint, path, command, node identity, or adapter overrides are structurally unavailable.
- Plan and error serialization omit endpoints, paths, credentials, commands, and raw topology dictionaries.

**Verification:**

- `python scripts/run_tests.py tests/test_fleet_bootstrap.py -x -q`
- `python -m ruff check anvil_serving/fleet_bootstrap.py tests/test_fleet_bootstrap.py`

### T004.1: Define bounded immutable receiver request frames

**Feature:** F002
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/fleet_bootstrap.py, tests/test_fleet_bootstrap.py
**Dependencies:** T002, T011

Implement only the fixed receiver request framing contract above. Reuse canonical JSON, exact enum/identifier validators and existing fixed bootstrap errors. Keep the frame codec independent of target configuration I/O, ZIP parsing, authorization, transport and installation. The configuration digest is a required value on non-identity requests, not permission to choose target paths.

**Acceptance criteria:**

- All five operations round-trip through exact canonical length-prefixed bytes with only their closed fields and immutable validated values.
- Length, type, canonical JSON, UUID, node and digest defects refuse with fixed metadata-safe errors before any side effect.
- Stage requires exactly the declared bounded payload and matching SHA-256; non-stage requests forbid all trailing bytes.
- Target configuration identity is mandatory on every non-identity request and cannot be replaced by an arbitrary path, command or environment field.

**Verification:**

- `python scripts/run_tests.py tests/test_fleet_bootstrap.py -x -q`
- `python -m ruff check anvil_serving/fleet_bootstrap.py tests/test_fleet_bootstrap.py`

### T004.2: Define trusted target configuration identity

**Feature:** F002
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/fleet_bootstrap.py, anvil_serving/topology.py, tests/test_fleet_bootstrap.py
**Dependencies:** T003, T011, T004.1

Implement the pure target-configuration value, canonical private serializer and plan-derived digest described above. Relocate the existing pure bootstrap path helpers into the standalone bootstrap module and preserve topology imports and behavior. This makes the future embedded receiver independent of the candidate package and topology loader without copying path rules.

**Acceptance criteria:**

- Exact canonical private bytes and digests round-trip for Windows and Linux with no file, environment or network access.
- Closed field sets, exact types, safe paths, root separation and platform pairing reject malformed inputs with fixed privacy-safe errors.
- Target identity binds every local configuration field but excludes full-plan artifact/topology/protocol/catalog identity; the full plan still binds all of those separately.
- The shared path implementation preserves existing topology/plan behavior and the target decoder imports no topology, targets or controller module.
- Configuration flags remain data and never authorize an operation; no provisioning, permission or deployed-state claim is made.

**Verification:**

- `python scripts/run_tests.py tests/test_fleet_bootstrap.py tests/test_topology.py tests/test_targets.py tests/test_topology_defaults.py -x -q`
- `python -m ruff check anvil_serving/fleet_bootstrap.py anvil_serving/topology.py tests/test_fleet_bootstrap.py`

### T004.4: Bind rollback requests to their initiating failure

**Feature:** F002
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/fleet_bootstrap.py, tests/test_fleet_bootstrap.py
**Dependencies:** T004.1

Implement the closed rollback-only trigger field and exact frame revalidation above. Preserve the other four operation wire bytes and all stage framing/digest limits. This pure fix-forward does not authorize or execute rollback.

**Acceptance criteria:**

- Rollback requires and round-trips one exact non-cleanup fixed initiating error; every other operation forbids the field.
- Missing/null/extra/wrong-type/subclass and tampered frame values refuse before serialization with fixed privacy-safe errors.
- Existing identity/stage/activate/status bytes and stage payload validation remain unchanged.
- No filesystem, transport, rollback action or authorization behavior is introduced.

**Verification:**

- `python scripts/run_tests.py tests/test_fleet_bootstrap.py -x -q`
- `python -m ruff check anvil_serving/fleet_bootstrap.py tests/test_fleet_bootstrap.py`

### T004.3: Define bounded receiver result frames

**Feature:** F002
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/fleet_bootstrap.py, tests/test_fleet_bootstrap.py
**Dependencies:** T002, T004.1, T004.2, T004.4

Implement only the closed immutable result variants, canonical bounded codec and request/result matcher above. Results report receiver-owned current state; they neither replace BootstrapReceipt nor claim controller acceptance. Reuse existing enum, ID, JSON and fixed-error idioms without adding IO or receiver execution.

**Acceptance criteria:**

- All result variants round-trip through exact bounded canonical length-prefixed bytes with closed field/type/phase contracts.
- Malformed or mismatched responses fail with fixed invalid-contract or receiver-mismatch errors without retaining private raw data.
- Identical stage retries can truthfully report advanced bound state, and rollback results bind their original triggering failure.
- Identity permissions/digests are typed evidence only; no codec or result confers operation authorization or deployment acceptance.
- Literal fixtures and negative matrices cover every documented result, framing, binding, permission and tampering boundary.

**Verification:**

- `python scripts/run_tests.py tests/test_fleet_bootstrap.py -x -q`
- `python -m ruff check anvil_serving/fleet_bootstrap.py tests/test_fleet_bootstrap.py`

### T004: Implement the fixed receiver protocol and target validation

**Feature:** F002
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/fleet_bootstrap.py, anvil_serving/control_plane/bootstrap_shim.py, tests/test_fleet_bootstrap.py
**Dependencies:** T008, T011, T004.1, T004.2, T004.3, T004.4

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

Add authenticated `POST /admin/bootstrap/stage` as a bounded `application/octet-stream` endpoint. Validate `node-admin:bootstrap`, local policy, expected node, exact Content-Length, UUID, plan digest, target configuration digest, and bundle digest headers before reading the body; then call the fixed receiver contract.

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
- Identity contains only its schema, operation and expected node; all other frames bind operation ID, plan digest and target configuration digest. Non-stage operations forbid trailing bytes, while stage length equals exact ZIP bytes.
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
