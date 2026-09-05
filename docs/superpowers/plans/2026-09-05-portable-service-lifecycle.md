# Portable Service Lifecycle Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Operate declared macOS launchd and cross-platform Docker services through the same Anvil CLI and MCP lifecycle, preserving model and topology authority.

**Architecture:** Service bindings select a supervisor independently of engine and endpoint contracts. A shared planner validates identity, topology, dependencies and admission before bounded adapter execution. Domain commands reuse the planner.

**Tech Stack:** Python 3.11+, standard library only, pytest, launchd, Docker/Compose.

**Spec:** ../specs/2026-09-05-portable-service-lifecycle-design.md

## Global Constraints

- Windows: Docker. macOS: native MLX or Docker. Linux: Docker.
- Existing macOS speech services may be adopted as legacy bindings; no implicit engine migration.
- Provider integrations are deferred; no cloud provisioning.
- Exact topology ownership, confirmed mutation, bounded secret-free output, no hidden fallback.
- No routing changes, auto-promotion, raw remote shell, daemon, or runtime dependency.
- Tests do not mutate unrelated live services.

## Task 1: Shared binding contracts

Files: `service_runtime/contracts.py`, `manifest.py`, `tests/service_runtime/test_manifest.py` under the existing package/tests roots.

- [x] Add failing tests for strict schema, duplicate identities, dependency cycles, unsafe values and workload classification.
- [x] Implement `ServiceError(code, message)` and `load_manifest(path) -> dict[str, dict]`.
- [x] Bindings use `id`, `resource`, `manager`, `engine`, `support`, `dependencies`, `endpoint`, `model`, `definition`, `definition_sha256`, `label`, `owner_uid`, `container`, `image_id`, `identity_labels`, `startup_policy` as appropriate.
- [x] Run `.venv/bin/python -m pytest tests/service_runtime/test_manifest.py -q`.

```python
def test_rejects_unknown_supervisor(tmp_path):
    path = tmp_path / 'services.toml'
    path.write_text('schema = "anvil-services/v1"\n[[service]]\nid = "voice"\nresource = "voice"\nmanager = "systemd"\nengine = "mlx"\n')
    with pytest.raises(ServiceError, match='manager'):
        load_manifest(path)
```

## Task 2: Supervisor adapters

Files: `service_runtime/launchd.py`, `docker.py`, `tests/service_runtime/test_launchd.py`, `test_docker.py`.

Consumes `ServiceError`. Both modules expose `Adapter(*, run=subprocess.run)` with `inspect(binding) -> dict`, `plan(binding, action, observed) -> list[list[str]]`, `logs(binding, tail) -> list[str]`, `discover() -> list[dict]`, and `describe(binding) -> dict`.

- [x] Test running/unloaded/disabled/failed/inaccessible states using independent OS fixtures.
- [x] Observe failing tests, then implement exact identity validation and bounded reads; plans never mutate.
- [x] Map up/down/restart/enable/disable to supervisor semantics and report unsupported guarantees explicitly.
- [x] Describe adoption sources by immutable identity/hash, excluding environments and raw argv.
- [x] Run `.venv/bin/python -m pytest tests/service_runtime/test_launchd.py tests/service_runtime/test_docker.py -q`.

```python
state = adapter.inspect(binding)
assert state['running'] is True
assert adapter.plan(binding, 'up', state) == []
```

## Task 3: Engine inspection and lifecycle execution

Files: `service_runtime/engine.py`, `operations.py`, `tests/service_runtime/test_operations.py`, `test_engine.py`.

Consumes bindings/adapters. Produces `execute(action, service=None, **options) -> dict`, with manifest/topology/identity/confirm/dry_run/tail/timeout and injected process/HTTP boundaries.

- [x] Test zero mutation for preview/unconfirmed/wrong-owner requests, identity races, conflicts, dependencies, and stop proof.
- [x] Implement locks, topology validation, owner reinspection, bounded execution, readiness and rollback limited to newly started instances.
- [x] Keep process, readiness and model-residency observations separate.
- [x] Adopt definitions atomically without process changes; install only exact declared definitions with no implicit startup/download.
- [x] Preserve existing model admission and container ownership when invoked from generic host utilities.
- [x] Run `.venv/bin/python -m pytest tests/service_runtime -q`.

## Task 4: CLI and typed tool parity

Files: `service_runtime/cli.py`, `commands/host_services.py`, `commands/host.py`, `control_plane/mcp/tools/services.py`, tool composition, controller contracts, `tests/service_runtime/test_cli_mcp.py`.

- [x] Add failing tests for CLI/MCP results, errors and mutation guards.
- [x] Implement status/discover/capabilities/logs/adopt/install/up/down/restart/enable/disable and explicit typed MCP declarations.
- [x] Preserve owner-selected operator home; remote requests cannot choose arbitrary definition paths.
- [x] Refuse self-controller stop/restart over its synchronous transport.
- [x] Regenerate command manifest/reference using existing maintenance scripts.
- [x] Run service-runtime and existing command/MCP/controller tests.

## Task 5: Domain integration and docs

Files: `serves.py`, `voice/config.py`, `voice/cli.py`, MCP voice tools, `models.py`, `operator_config.py`, examples/scaffold, focused regression tests.

- [x] Test and implement service-backed native serves/voice while preserving existing external and owned-process behavior.
- [x] Accept native serves only with supported service bindings; reject unsupported promotion/exclusive operations before mutation.
- [x] Preserve recipe identity and admission. Extend private configuration dependency closure.
- [x] Document actual command shapes, lifecycle semantics, matrix, and migration.
- [x] Run Docker, voice, model and media ownership regression gates.

## Task 6: Review and verification

- [x] Independent review of contracts/adapters/integration, then fix actionable findings.
- [x] Run `.venv/bin/python scripts/run_tests.py tests/ -x -q` and generated-doc checks.
- [x] Verify read-only local inventory and an isolated benign launchd lifecycle smoke.
- [ ] Validate private workspace registry before real adoption; preserve existing service state and startup policy.
- [x] Report unavailable platform/deployment evidence explicitly; do not claim unverified rollout.

Verification: 5,019 tests passed and 18 skipped. The opt-in macOS launchd smoke
passed separately, including an independent PID-exit check; temporary definitions
were removed. CLI reference and scaffold audits passed. Windows/Linux Docker
contract fixtures passed, but those live platforms were not tested. Real adoption
remains pending a validated private operator workspace; no application service
was changed during implementation.
