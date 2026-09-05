# Project: Bounded Controller Diagnostics

## Summary

Give the fleet operator read-only product surfaces for diagnosing a declared Docker controller when
its HTTP endpoint cannot be trusted or reached. Deployment preflight found an
unrelated Windows HTTP listener at the expected ports while the actual controller
continued answering its internal health check. Existing controller status cannot
retrieve the owning service's logs or distinguish configured Docker port bindings
from observed publication. These diagnostics close that concrete gap without
adding a lifecycle supervisor or bypassing endpoint authentication.

## Goals

- Inspect one explicitly selected controller container through Anvil Serving.
- Report configured and observed publication independently, without credentials,
  raw container configuration, or automatic endpoint substitution.
- Retrieve bounded structured controller audit events without raw log content.
- Keep every subprocess, byte count, returned collection, and error bounded.
- Supply executable tasks and negative controls for a focused executor.

## Non-Goals

- Repairing ports, firewall rules, HTTP reservations, Docker, WSL, or unrelated listeners.
- Restarting containers, changing routes/models, installing packages, or promoting.
- Reading environment values, mounts, command lines, raw tracebacks, or arbitrary logs.
- Native Windows service, macOS launch agent, or systemd log adapters in this slice.
- Treating internal container health, an image tag, or port configuration as live identity.
- Adding a diagnostic endpoint to the public data plane.

## Requirements

- R001: Add proposed `controller inspect --container <name>` and `controller logs --container <name>` commands, with explicit required target names. Validate one Docker name/ID using `[A-Za-z0-9][A-Za-z0-9_.-]{0,127}`; reject options, paths, whitespace and control characters before subprocess execution.
- R002: Inspect only fixed Docker template fields: immutable container ID, running state, exit code, health status, Compose service label, configured TCP port bindings and observed TCP port bindings. Never request or emit environment, command, mount, image configuration, health-check commands or health-check output.
- R003: A selected container must assert Compose service label exactly `controller`; absent or mismatched identity yields a fixed unsupported result. Resolve its immutable ID before logs, then use that ID for the log command so same-name replacement cannot redirect the read. A label is deployment ownership metadata, not cryptographic trust.
- R004: Return separate configured/observed binding rows with internal port, external port and bind class (loopback, wildcard, private, public, unknown), not literal addresses. Bound each collection to 64 rows. Never infer publication from configured bindings when the observed collection is empty.
- R005: Log reads use fixed `docker logs --tail N <immutable-id>`, with strict integer N in 1..200, default100, no follow/since/free-form argument. Accept only complete bounded JSON audit lines and project fixed known operation/event/error codes, HTTP status and elapsed milliseconds. Drop arbitrary tool names, request IDs, addresses, paths, messages, and unknown fields; count dropped/unknown/truncated input honestly. No free-form text mode.
- R006: Each Docker child has a 10-second wall deadline and combined stdout/stderr capture ceiling of 256 KiB; retained raw capture never exceeds that ceiling (bounded decoded projections and fixed read buffers are separate). Concurrent drains enforce one shared ceiling. Logs merge stderr into stdout at process creation to preserve captured stream order. Overflow/timeout terminates only the child created by this invocation, uses one bounded terminate/kill escalation, joins readers and reaps it. Errors never echo argv, stderr or exception text. No temporary log files. Pin the local daemon explicitly as defined below; environment/context must never redirect diagnostics to another host.
- R007: Every library operation returns the exact allowlisted `controller-diagnostics/v1` schema below. Fixed states are ok, unsupported, unavailable, timeout, output-limit, malformed. A nonzero inspect exit is conservatively unavailable: without reading stderr, this slice cannot distinguish an absent container from daemon or permission failure. Malformed/partial data never becomes healthy.
- R008: Expose the same read-only implementation through `controller_inspect` and `controller_logs` MCP tools and command-tree operation contracts. They target the controller resource on its owning native/Docker runtime, never require a model GPU role, and do not gain SSH fallback. Existing operator/controller authentication applies; the new workload-read and bootstrap scopes do not grant these legacy operator tools.
- R009: Document that local CLI diagnosis is required when the remote controller is unavailable; remote MCP cannot diagnose its own unreachable transport. Neither a successful inspect/log result nor an image label proves node/build identity or deployment readiness.
- R010: Unit, CLI, MCP, help/manifest, privacy and full repository gates must pass; a negative control must demonstrate at least the subprocess bound and unknown-field redaction tests fail when those guards are removed.
- R011: The entire root CLI output for controller inspect/logs, local or remote, must retain only the canonical diagnostic result and fixed input-free errors. Use an operand-free command label, literal null context, empty warnings and structured data; never wrap printed JSON or expose topology, raw argv, transport details or nested unvalidated response fields.

## Acceptance Criteria

- Given a configured binding and no observed publication, when inspect runs, then its returned configured_bindings has one row and observed_bindings is empty, with no guessed URL.
- Given a non-controller service label, when logs is requested, then state=unsupported and the log subprocess call count is zero.
- Given same-name replacement after inspection, when logs runs, then the captured argv contains only the previously resolved immutable container ID.
- Given credential-shaped values in unapproved log fields, when projection runs, then the returned JSON contains none of those values and unknown_fields increases.
- Given a child flooding both streams, when the combined ceiling is reached, then state=output-limit, retained bytes never exceed262144, and only that child is reaped.
- Given equivalent CLI/MCP inputs, when both run, then their result dictionaries are equal and model/GPU mutation spy call counts remain zero.
- When the local managed inspect/log commands are re-run against the affected controller, then the ticket records the observed publication and request-arrival conclusions, without closing unresolved endpoint identity or deployment gates.

## Risks

- Docker output can be hostile or malformed, including a single enormous log line.
- Container labels and internal health can be mistaken for verified controller identity.
- A read-only command still risks memory growth, secret exposure and hung child processes.
- Unavailable remote control requires a local execution context, not an authentication bypass.

## Open Questions

None for this bounded slice. Native service log adapters and repairs remain outside it.

## Assumptions

### A001: Docker Compose service identity is available for the affected managed controller.

**Rationale:** The generic controller Compose service and the observed affected container declare the service label `controller`. Requiring it prevents this diagnostic surface from becoming an arbitrary container log reader.

**Requirements:** R002, R003, R009

### A002: Metadata-only audit logs answer this incident's request-arrival question.

**Rationale:** Controller HTTP already emits structured operation/status records. Raw startup tracebacks may require a future explicitly bounded private diagnostic contract; this change must not silently expose them.

**Requirements:** R005, R007

## Closed implementation contract and breadcrumbs

- Proposed module: `anvil_serving/controller_diagnostics.py`; proposed focused
  tests: `tests/test_controller_diagnostics.py`. Stdlib only.
- Every invocation prefixes Docker argv with `--host` and a platform-fixed local
  endpoint: Windows `npipe:////./pipe/docker_engine`, Linux/container
  `unix:///var/run/docker.sock`. Other platforms are unsupported in v1. Remove
  every `DOCKER_` environment override from the child environment; never use
  the current context or accept a caller-provided daemon URL. Tests set hostile
  remote context/host/TLS overrides and prove every executed child still targets
  only the fixed local daemon. No context-discovery or SSH child is allowed.
- `anvil_serving/router_endpoint.py::_inspect_container_binding` shows fixed
  Docker inspection and configured-versus-observed pitfalls. Do not reuse its
  fallback-to-default URL behavior or unbounded capture for these diagnostics.
- `anvil_serving/guard.py::terminate_then_kill` owns the one-attempt escalation
  idiom. Inject the process factory and deadline clock for hermetic tests;
  bound cleanup separately (at most one second per terminate/kill wait).
- Use a fixed Docker Go template yielding only the R002 fields. Strictly validate
  its exact dictionary keys and values. Do not run full `docker inspect` and
  then discard secret-bearing fields after collection.
- Internal/external port numbers are strict integers 1..65535; reject bool.
  Binding classification parses with stdlib `ipaddress` offline, never DNS.
  Predicate order is exact unspecified (wildcard), 127.0.0.0/8 or ::1
  (loopback), RFC1918/100.64.0.0/10/fc00::/7 (private), then explicit reserved
  ranges (unknown), otherwise public. Reserved v4 ranges are 0.0.0.0/8,
  169.254.0.0/16, 192.0.0.0/24, 192.0.2.0/24, 198.18.0.0/15,
  198.51.100.0/24, 203.0.113.0/24 and 224.0.0.0/3; reserved v6 includes
  2001:db8::/32 and every address outside 2000::/3 after the earlier matches.
  IPv4-mapped IPv6 is unknown. Do not use version-dependent is_private or
  is_global. Invalid addresses/ports make the inspection malformed; binding
  collections over64 rows return output-limit, never a silently shortened list.
- Container IDs require 64 lowercase hexadecimal characters. State fields have
  exact primitive types; health is one of healthy/unhealthy/starting/none.
- Fixed audit operations initially health, healthz, tools/list, tools/call, mcp;
  fixed events initially operation_interrupted_recovered and audit_file_write_failed.
  Fixed error codes are authentication_error, authorization_scope_denied,
  origin_not_allowed, header_mismatch, unknown_tool, request_timeout,
  payload_too_large and internal_error, not arbitrary string grammar.
  Unknown codes/operations contribute counts only.
  Status is strict int 100..599, elapsed_ms finite number 0..3600000.
- Parse logs only after bounded capture; each line at most16 KiB, each accepted
  object at most 32 keys. Strict JSON rejects duplicate keys and non-finite values.
  Preserve line order, cap returned events at 200, and use counters instead of
  copying rejected lines. Decode invalid UTF-8 as a rejected line, not raw text.
- Common result keys are exactly schema_version, kind, state, error_code,
  container_id and truncated; schema_version is the literal string
  controller-diagnostics/v1, kind is inspect/logs, container_id is null until
  validated, error_code is null for ok and otherwise diagnostic_<state> (hyphens
  replaced with underscores), truncated is a strict bool. Inspection adds
  running (bool/null), exit_code (int0..255/null), health
  (healthy/unhealthy/starting/none/null), configured_bindings and observed_bindings.
  Each binding has exactly container_port, host_port, bind_class. Failure uses
  null inspection scalars and empty binding arrays; no raw process exit code.
- Logs add events, line_count, returned_events, rejected_lines, unknown_fields,
  unknown_codes and counters_saturated. Event keys are a subset of operation,
  event, error_code, status, elapsed_ms; at least one known operation/event is
  required. Unknown operation/event/error-code values reject that entire line
  and increment unknown_codes for each unknown code plus rejected_lines once.
  Unknown keys are counted in unknown_fields and dropped; otherwise valid
  records may survive. Malformed JSON, duplicate keys, invalid types, oversized
  lines and records with no known operation/event increment rejected_lines.
  line_count counts all captured complete/final partial lines; returned_events
  equals len(events). Counters are strict ints0..8388608, saturate at that bound,
  and set counters_saturated=true if saturation occurs. More than200 valid events
  returns the first200 and truncated=true. Failed/truncated capture exposes no
  partial events and uses empty arrays/zero counters with truncated=true for
  timeout/output-limit. Inspect failure before logs preserves its fixed state.
  A successful bounded log child returns state=ok even when every line is
  rejected: counters describe projection coverage, not controller health.
  Malformed inspect data returns malformed; rejected individual log lines do
  not change the collection state. Empty logs likewise return ok with zero counts.
- `anvil_serving/commands/control_plane.py::commands` owns controller command
  declarations; use a new module entrypoint so fleet T009's controller serve CLI
  need not be edited. `anvil_serving/control_plane/mcp/tools/operations.py::build_family`
  can expose the two handlers using the existing schema/result idioms.
- The command manifest is generated by
  `python -c "from anvil_serving.commands.spec import write_manifest; write_manifest()"`.
  Running the module alone is not a generator invocation.
- Preserve public/private boundaries. Tests use synthetic IDs and addresses only.
  Production diagnostics remain untracked; ticket updates contain conclusions,
  fixed codes and test evidence, never raw host identities or logs.

## Features

### F001: Bounded controller inspection and audit projection

Provide a fixed-target, fixed-output diagnostic core.

**Requirements:** R001, R002, R003, R004, R005, R006, R007

### F002: Product CLI, MCP and documented acceptance

Make diagnostics usable through supported operator surfaces and prove parity.

**Requirements:** R008, R009, R010, R011

## Tasks

### T001: Add bounded child capture and safe diagnostic types

**Feature:** F001
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/controller_diagnostics.py, tests/test_controller_diagnostics.py

Implement strict argument validation, fixed result/error dictionaries and a
bounded subprocess adapter. Support separate-pipe inspection with two shared-budget
readers and stderr=STDOUT log capture with exactly one reader of the merged pipe.
Both use a shared byte counter, stop event and wall deadline; no shell. Do not
collect environment or persist output. Follow guard's cleanup ladder for the owned child.

**Acceptance criteria:**

- Combined stdout/stderr limits hold under concurrent flooding and giant lines.
- Timeout, overflow, missing executable and malformed arguments are fixed failures.
- Child/reader cleanup is bounded and no unrelated process is targeted.
- Separate-pipe flooding enforces the combined ceiling; merged interleaved log output preserves captured order.

**Verification:**

- `python scripts/run_tests.py tests/test_controller_diagnostics.py -x -q`
- `python -m ruff check anvil_serving/controller_diagnostics.py tests/test_controller_diagnostics.py`

### T002: Add fixed inspection and metadata-only controller logs

**Feature:** F001
**Priority:** high
**Type:** feature
**Dependencies:** T001
**Likely files:** anvil_serving/controller_diagnostics.py, tests/test_controller_diagnostics.py

Implement the fixed inspect template, service-label/immutable-ID gate, separate
binding projections and strict allowlisted audit-line parser. Inject child
responses; preserve unknown/truncation counts and never infer endpoint identity.

**Acceptance criteria:**

- Configured and observed publication diverge honestly in a regression fixture.
- Non-controller identity and malformed fields fail before logs.
- Same-name replacement cannot redirect the immutable-ID log read.
- Unknown fields and all raw content are absent from results.

**Verification:**

- `python scripts/run_tests.py tests/test_controller_diagnostics.py -x -q`
- `python -m ruff check anvil_serving/controller_diagnostics.py tests/test_controller_diagnostics.py`

### T003: Wire explicit controller diagnostic CLI commands

**Feature:** F002
**Priority:** high
**Type:** modify
**Dependencies:** T002
**Likely files:** anvil_serving/controller_diagnostics.py, anvil_serving/commands/control_plane.py, tests/test_controller_diagnostics.py

Add module CLI inspect/logs and initially local-only controller-resource command
nodes with exact container/tail options. Return structured results, print only in
the wrapper. Preserve all existing controller serve/status behavior. T004 adds
remote mappings only when their tool handlers exist.

**Acceptance criteria:**

- Help requires a target and describes read-only metadata-only behavior.
- Invalid arguments execute no child; no arbitrary subprocess options pass through.
- Command nodes are local-only, controller-owned, GPU-independent reads.

**Verification:**

- `python scripts/run_tests.py tests/test_controller_diagnostics.py -x -q`
- `python -m ruff check anvil_serving/controller_diagnostics.py anvil_serving/commands/control_plane.py tests/test_controller_diagnostics.py`

### T004: Expose equivalent bounded MCP diagnostic tools

**Feature:** F002
**Priority:** high
**Type:** modify
**Dependencies:** T003
**Likely files:** anvil_serving/control_plane/mcp/tools/operations.py, anvil_serving/commands/control_plane.py, tests/test_controller_diagnostics.py

Add controller_inspect/controller_logs to the operation family with closed
schemas and shared implementation. Add controller transport and declared remote
mappings using these exact tool names in the same change as the handlers.

**Acceptance criteria:**

- Equivalent CLI and MCP inputs produce equivalent library result dictionaries.
- Unknown arguments, wrong types and out-of-range limits fail before child execution.
- Operation discovery reports read-only controller ownership, no GPU requirement.

**Verification:**

- `python scripts/run_tests.py tests/test_controller_diagnostics.py tests/control_plane/test_mcp_runtime.py -x -q`
- `python -m ruff check anvil_serving/control_plane/mcp/tools/operations.py tests/test_controller_diagnostics.py`

### T005: Prove controller exposure and authorization boundaries

**Feature:** F002
**Priority:** high
**Type:** modify
**Dependencies:** T004
**Likely files:** examples/fakoli-dark/docker-compose.controller.yml, tests/test_controller_compose.py, tests/test_controller.py

Add the two exact diagnostic tools to the generic controller Compose allowlist
and regression-check it. Use actual controller HTTP with fake diagnostic children
to prove missing/wrong credentials and restricted allowlists never invoke a
child. Also prove workloads:read/node-admin:bootstrap-only credentials grant neither tool;
fleet-node-enrollment:T009 must be accepted before this external prerequisite
is considered satisfied. Do not edit private Compose or deploy in this task.

**Acceptance criteria:**

- When the generic Compose allowlist is parsed, then both exact tool names appear and existing excluded tools remain absent.
- Given absent/wrong credentials or an allowlist excluding diagnostics, when either tool is called, then the diagnostic child spy call count is zero.
- Given only workloads:read or node-admin:bootstrap scope, when either diagnostic tool is called, then authorization is denied and no child starts.
- Given a valid existing legacy operator credential and an allowlist including both tools, when actual controller HTTP invokes each tool, then its handler executes once, returns the canonical result, and starts exactly the expected fake child sequence (one inspect child for inspect; inspect then immutable-ID logs for logs).

**Verification:**

- `python scripts/run_tests.py tests/test_controller.py tests/test_controller_compose.py -x -q`
- `python -m ruff check tests/test_controller.py tests/test_controller_compose.py`

### T007: Synchronize client transport permissions and scaffold parity

**Feature:** F002
**Priority:** high
**Type:** modify
**Dependencies:** T005
**Likely files:** examples/fakoli-dark/operator-topology.toml, anvil_serving/_scaffold_templates/operator-topology.toml, tests/test_targets.py

Add controller-inspect/controller-logs to the generic controller transport's
allowed_operations and byte-identical packaged topology mirror. Preserve all
other transport permissions. targets.py::resolve_execution_plan selects only
transports allowing the hyphenated command; server-side tool exposure alone
does not make remote CLI resolution work. Use the controller resource and its
owning runtime, with expected-node identity retained and no SSH fallback.

**Acceptance criteria:**

- Given the generic topology and declared controller target, when either diagnostic command resolves, then exactly its declared controller transport is selected.
- Given either permission removed, when that diagnostic command resolves, then it fails closed without selecting SSH or an unrelated controller.
- Example and packaged scaffold remain byte-identical and unrelated allowlists are unchanged.

**Verification:**

- `python scripts/run_tests.py tests/test_targets.py tests/test_init.py -x -q`
- `python -m ruff check tests/test_targets.py`

### T006: Synchronize manifest, documentation and live bounded diagnostic proof

**Feature:** F002
**Priority:** high
**Type:** modify
**Dependencies:** T007, T008, T009, T010
**Likely files:** docs/CLI-COMMAND-MANIFEST.json, docs/cli/control-plane.md, docs/prds/README.md, .tickets/2026-09-05-controller-deployment-access.md

Generate the manifest with write_manifest, document the metadata-only/unsupported
native boundaries, and re-run diagnostics locally against the selected controller.
Retain only sanitized conclusions in the ticket.

**Acceptance criteria:**

- CLI reference, command-tree, strict docs and full repository gates pass.
- The actual managed inspect/log commands replace raw Docker for this incident.
- Ticket retains any unresolved publication or endpoint-identity gate; no false closure.
- Document both negative controls and separate deployment acceptance.

**Verification:**

- `python scripts/run_tests.py tests/test_command_tree.py tests/test_docs_command_invocations.py tests/test_controller_diagnostics.py -x -q`
- `python scripts/check_markdown_links.py --root .`
- `python -m mkdocs build --strict`
- `python scripts/run_tests.py tests/ -x -q`

### T008: Preserve integer input grammar through controller CLI dispatch

**Feature:** F002
**Priority:** high
**Type:** modify
**Dependencies:** T004
**Likely files:** anvil_serving/cli.py, tests/test_cli.py, tests/test_controller_diagnostics.py

Fix `cli.py::_remote_scalar` so schema-declared integer values require an optional ASCII minus sign followed by one or more ASCII digits before conversion. Preserve leading zeros as the existing local parsers do; numeric schema bounds still determine whether negatives or zero are allowed. Reject plus signs, whitespace, underscores, Unicode digits, empty values and invalid forms with a fixed UsageError. Do not change string or floating-point behavior. This closes the confirmed local-versus-controller diagnostic input gap documented in `.tickets/2026-09-05-remote-integer-cli-parity.md`; keep the correction generic rather than hard-coding a diagnostic command name.

**Acceptance criteria:**

- Local and controller diagnostic CLI paths both reject `+1`, `1_0`, leading-space input and Unicode digits before diagnostic or request transport invocation.
- Real CLI dispatch tests with fake topology/resolution/transport prove default100,1,200 reach the shared library unchanged and refused input sends no operation request.
- Remote integer options retain signed-negative and leading-zero behavior where their schemas allow it; string and floating-point parsing remain unchanged.
- Existing remote CLI, MCP and diagnostic regressions pass; errors contain no raw input value.

**Verification:**

- `python scripts/run_tests.py tests/test_cli.py tests/test_controller_diagnostics.py -x -q`
- `python -m ruff check anvil_serving/cli.py tests/test_cli.py tests/test_controller_diagnostics.py`

### T009: Protect structured diagnostic results at the complete CLI boundary

**Feature:** F002
**Priority:** high
**Type:** modify
**Dependencies:** T007, T008
**Likely files:** anvil_serving/controller_diagnostics.py, anvil_serving/commands/control_plane.py, anvil_serving/cli.py, tests/test_cli.py, tests/test_controller_diagnostics.py

Close `.tickets/2026-09-05-controller-diagnostic-envelope-privacy.md` through the actual root CLI, not only the diagnostic core. Add `controller_diagnostics.validate_public_result(value, *, expected_kind)` that returns a fresh allowlisted dictionary or raises a fixed input-free ValueError. Require exact built-in dictionary/list/scalar types in the core result, exact top-level keys, the existing v1 schema/state/error/truncation invariants, bounded hexadecimal container IDs, exact inspection binding rows and strict port ranges/classes. Validate log event keys/enums, finite elapsed values, collection bounds, strict counters, returned-events equality, and empty/null/zero failure forms. Reject the entire malformed response rather than retaining nested unknown values. Reuse the existing constants and public result contract; do not invent a second diagnostic schema.

Add a thin `command(argv=None) -> CommandResult` handler using `operator_output.CommandResult`. Redirect parser error output and translate parser refusal to fixed `invalid_diagnostic_arguments` with exit two and no data. Retain real zero-child help and the existing focused module `main` behavior. Validated non-ok diagnostic results retain their structured data and fixed `diagnostic_<state>` error with exit one. Set only the two diagnostic command nodes' existing `handler_attribute` to `command`.

In `cli.py`, recognize only the exact `controller inspect` and `controller logs` leaves. Both local and remote paths use canonical operand-free command names, null context, empty warnings, and only validated structured data. Suppress generic plan/context/warnings and captured parser/exception output at every output branch for those leaves, including early target-resolution failures. Other controller commands keep their existing behavior. Follow the exact protected offline topology leaf for literal-null JSON serialization; the generic renderer normally expands null into an unrelated empty context shape.

For remote diagnostics, `TransportResult.data` is the transport-owned read-only mapping containing the HTTP envelope, not the core result. Require exactly `ok` and `data`, with `ok is True`, before validating its core dictionary. The outer mapping proxy may be copied to inspect these two keys; nested input must still pass the strict validator. Never expose `TransportResult.as_dict`, transport metadata, or an execution plan. Malformed outer/core results yield fixed `controller_diagnostic_response_invalid`, null data and exit four. Transport failures yield fixed `controller_diagnostic_transport_failed`, null data and exit four without exception strings/details, reconciliation, retries or SSH fallback. JSON and human error paths must use the same bounded contract; no validated typed result means no captured text may become data.

The implementation gate deliberately excludes generated-manifest equality: T006 owns deterministic regeneration, command-tree/full-suite checks and final documentation after all diagnostic nodes are complete. This dependency is not permission to weaken those final gates.

Update the old root-dispatch fixture in `tests/test_controller_diagnostics.py` to a complete kind-specific public result. The internal common `safe_result` builder remains valid for its direct callers, but its six-key base alone is not a complete inspect/log response at the external CLI validator. Do not weaken validation to preserve that incomplete test stub. Check types before comparisons or hashing, including non-ok forms; capture every parser refusal, including help-like tokens after a literal separator.

**Acceptance criteria:**

- Actual `cli.main` local inspect/log success returns structured canonical data, exact operand-free command, null context and empty warnings; local non-ok results exit one with their fixed diagnostic code.
- Invalid/missing selectors, malformed tails and resolution options refuse with fixed errors before diagnostics or operation transport; full human/JSON output contains no supplied operand, path, address or raw exception.
- Actual fake-controller dispatch unwraps the legitimate transport mapping and validates the same core schema. Non-ok core results exit one; hostile/malformed responses and transport errors exit four with null data and fixed codes.
- Extra/missing/wrong outer or core keys, nested arbitrary strings, bool-as-int, bad enums/IDs, 65 bindings, 201 events, returned-count mismatches and nonfinite elapsed values are rejected without echoing attacker content. A negative control removing the strict validator makes these regressions fail.
- Real help remains zero-child, no failure retries or chooses SSH, and representative unrelated CLI commands/envelopes remain compatible.

**Verification:**

- `python scripts/run_tests.py tests/test_cli.py tests/test_controller_diagnostics.py -x -q`
- `python -m ruff check anvil_serving/cli.py anvil_serving/controller_diagnostics.py anvil_serving/commands/control_plane.py tests/test_cli.py tests/test_controller_diagnostics.py`
- `git diff --check`

### T010: Register bounded diagnostics in both workbench skill catalogs

**Feature:** F002
**Priority:** high
**Type:** modify
**Dependencies:** T009
**Likely files:** .agents/skills/anvil-serving-workbench/SKILL.md, .claude/skills/anvil-serving-workbench/SKILL.md

Fix the reproduced command-tree skill-catalog gate without weakening it. Add
`controller_inspect` and `controller_logs` to the existing MCP Tool Map in both
skills, with the same short diagnostic playbook. Explain that these are
metadata-only reads for one explicit Docker controller; logs have a bounded
tail and discard raw messages. A configured binding is not observed publication,
and either successful diagnostic is not endpoint identity or deployment proof.
When the controller transport is unreachable, use the verified local CLI on its
owning host; this is an explicit exception to wrapper-missing-only fallback,
not SSH substitution. Native/macOS controller diagnostics remain unsupported.
Preserve the skills' existing different supporting content and all mutation,
credential and promotion gates. Do not replace one whole skill with the other.

**Acceptance criteria:**

- The existing runtime-MCP-versus-skill catalog audit passes for both skills.
- Both skills give the same bounded diagnostic and unreachable-transport guidance.
- No instruction grants repair, lifecycle, credential or promotion authority.
- Removing either new catalog entry makes the existing audit fail.

**Verification:**

- `python scripts/run_tests.py tests/test_command_tree.py -k repo_workbench_surfaces_catalog -x -q`
- `git diff --check`
