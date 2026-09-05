# Proposed implementation PRDs

These PRDs turn source reviews and concrete Anvil Serving product gaps into
bounded changes. They are proposed implementation contracts, not shipped features.
Each includes requirements, source pointers, small tasks, negative tests, and
completion evidence for an agent starting without the original conversation.

## Router and fleet delivery order

**Reviewed:** 2026-09-05. **Anvil baseline:**
`76137fa292951a4e9495447346c74d190cec63c2`.

These four PRDs mirror named Anvil State partitions in `reviewed` status. Review
establishes structural completeness; it is not permanent approval, planning,
implementation, qualification, promotion, or deployment.

| Order | PRD | Outcome | Suggested executor | Dependencies |
| --- | --- | --- | --- | --- |
| 1 | [Qualified same-host replica sets](qualified-replica-sets.md) | One alias can use an explicit closed set of exactly equivalent, independently ready endpoints on one host. | Terra high for config/router concurrency; medium for docs | None |
| 2 | [Capacity-aware scheduling for qualified replicas](replica-capacity-scheduler.md) | Atomic least-loaded member selection without choosing a model, tier, or host. | Terra high for scheduler/admission edges | PRD 1 must be implemented and reviewed first |
| 3 | [Unified workload visibility](workload-visibility.md) | One bounded read-only view of active and recent work with honest partial fleet results. | Terra medium; high for router stream cleanup and concurrent stores | Independent; reuse PRD 1 member IDs only if already shipped |
| 4 | [Managed fleet node enrollment](fleet-node-enrollment.md) | Previewable, transactional bootstrap for one declared node with exact acceptance and rollback. | Terra high for transport/path security; medium for CLI/docs | Independent; credentials and machine prerequisites stay separate |

PRD 1 is the narrow routing foundation and PRD 2 is its explicit follow-on.
PRDs 3 and 4 can proceed independently. Execute one task at a time and keep
each implementation PR-sized; do not combine all four contracts into a router
or control-plane rewrite.

## Miles-informed delivery order

**Reviewed:** 2026-09-05. **Anvil baseline:** `f50dca489780b95d0cd98dee59cf620618c4ccd1`.
**Miles baseline:** [`d2fc97ce581577e255e494801d7568747d5a10d7`](https://github.com/radixark/miles/tree/d2fc97ce581577e255e494801d7568747d5a10d7).
Source inspection is the basis for the proposals; no Miles performance or local
GPU qualification is claimed.

| Order | PRD | Outcome | Suggested executor | Dependencies |
| --- | --- | --- | --- | --- |
| 1 | [Fence readiness observations across invalidation](readiness-generation-fencing.md) | Old probes cannot restore invalidated readiness. | Terra high for concurrency implementation; medium for fixtures/docs | None |
| 2 | [Protocol and session correctness corpus](session-protocol-corpus.md) | Reproducible tool, retry, streaming, and history compatibility checks. | Terra high for dialect changes; medium for corpus wiring | None; shares router tests with PRD 1 |
| 3 | [Correlated benchmark timeline](benchmark-timeline.md) | Explain benchmark time using measured phases, turns, and resource samples. | Terra medium for schema/UI; high for persistence and cancellation edges | None; consumes existing jobs and telemetry |
| 4 | [Isolated Harbor environment evaluation](harbor-environment-evaluation.md) | Run independently graded terminal/custom tasks through managed benchmark jobs. | Terra medium after the pinned adapter contract is proven | Its own compatibility spike must pass before adapter integration |

The readiness-fencing and session-corpus PRDs are the best first Miles-informed
implementation batch. The correlated timeline is the largest operator-facing
improvement. The Harbor adapter expands evaluation coverage but has a larger
external compatibility surface. Harbor can ship without the timeline; the
timeline must not depend on installing Harbor.

## Instructions for an implementing agent

1. Read the selected PRD, the current `AGENTS.md`, `README.md`, and
   `CLAUDE.md`. Use the installed **`resolve-anvil-serving-workspace`** skill
   before choosing an operator or product path. Follow the invocation and
   fail-closed result checks in [Workspace resolution](#workspace-resolution).
2. Reconcile the listed baseline with current source. Symbols and tests below
   were inspected; proposed files and commands are explicitly marked. A future
   implementation may already satisfy a requirement. Preserve that behavior
   and record the evidence rather than recreating it.
3. Work on an isolated branch from current `origin/main`. Execute one task at
   a time and preserve unrelated changes. Task dependencies are local to a PRD;
   IDs such as T001 are not globally unique across these documents.
4. Read the full files you will edit. Start with the named failing/negative
   scenario, then make the smallest implementation change that passes it.
   Avoid a generic plugin framework, a new serving stack, or a router rewrite.
5. Proposed verification commands naming new tests become runnable after that
   task creates those tests. Existing-test commands are regression gates, not
   proof that a proposed feature already exists.
6. Use injected clocks, fake transports, and synthetic fixtures for ordinary
   tests. Local loopback test servers are permitted. Production endpoints,
   installed model clients, real GPU workloads, and fleet mutations are not test
   fixtures. Optional measured acceptance is separately labeled.
7. Keep the Anvil runtime stdlib-only. External harness dependencies belong in
   a pinned, isolated worker environment. Keep raw payloads, credentials, logs,
   runtime databases, and machine identities out of tracked files.
8. Report changes, tests actually run, incomplete requirements, and any
   remaining compatibility gate. Do not equate passing mocks with a working
   external harness or a model qualification.

## Workspace resolution

`resolve-anvil-serving-workspace` is an installed agent skill, not an
`anvil-serving` CLI subcommand and not a module shipped by this public repo.
Locate that exact skill in the current agent's available-skills catalog, read
its `SKILL.md`, and use the directory containing that file as `resolverSkillRoot`.
Do not infer its installation directory or the paired private repository from
this checkout's parent directory. On PowerShell, the invocation is:

```powershell
# Set this to the actual directory reported by the installed skill catalog.
$resolverSkillRoot = '<resolved directory containing the skill SKILL.md>'
$resolverScript = Join-Path $resolverSkillRoot 'scripts/resolve_workspace.py'
python $resolverScript --cwd (Get-Location).Path --json
```

The angle-bracket value is a substitution instruction, not a runnable default.
Require exit 0 and JSON `ok: true`; use returned `product_root` for public code
and docs, and `operator_root` / `operator_home` only for private operator work.
Check the returned public and private repository identities against the
[public/private ownership policy](../OPERATOR-PRIVACY.md). Never publish the
machine-local values returned by the resolver. A missing skill, script, registry,
or unsuccessful resolution is a stop condition: ask the operator to provide or
repair the installed skill/registry; do not guess a sibling checkout, bootstrap
private state, or silently substitute an unrelated tool. These PRDs describe
public product changes, not changes to active private assignments.

## Shared verification and completion contract

For implementation work, run focused tests first, then the repository's required
checks appropriate to the changed surfaces:

```powershell
python scripts/run_tests.py tests/ -x -q
python -m ruff check anvil_serving tests
python scripts/check_markdown_links.py --root .
python -m mkdocs build --strict
git diff --check
```

The Markdown checker uses tracked paths. Include only the intended new files in
its index scope; never stage unrelated work to make a check pass. New CLI/MCP
surfaces also require command-manifest, schema, help, and packaged-wheel checks.
Use current repository tooling rather than copying historical release commands.
Source review/publication and package or live deployment are separate outcomes.

Each implementation handoff must map requirement IDs to tests or artifacts,
include a negative control showing a critical assertion detects its intended
failure, and state rollback behavior. Numerical limits in these PRDs are proposed
product bounds; they are not measured performance results.

## Existing work to preserve

- `docs/adr/0034-fleet-control-plane-and-node-runtime-classes.md` explicitly
  excludes a cross-host request scheduler. Same-host replica membership must
  not weaken that decision.
- `[router.model_routes]` maps one caller alias to exactly one logical tier.
  Replica selection remains inside that already selected tier, with no hidden
  model, capability, host, or fallback decision.
- Existing direct-to-replica benchmark evidence is a performance prior, not a
  qualified load balancer or route. Routed qualification and promotion remain
  separate gates.
- `docs/STRATEGY-MAKE-DIVERGENCE-LOUD.md` tracks `fleet bootstrap` as open.
  A PRD or reviewed source change must not mark it shipped before implementation
  and exact node acceptance exist.
- [Issue 379](https://github.com/fakoli/anvil-serving/issues/379) owns fleet
  environment stamping in benchmark evidence. The timeline consumes such a
  stamp when available and labels its absence; it does not duplicate that work.
- [Issue 453](https://github.com/fakoli/anvil-serving/issues/453) owns recipe
  discovery and GPU ownership reconciliation. None of these PRDs infers GPU
  ownership from utilization or adds a competing registry.
- Existing context, agentic, and SWE jobs, independent scoring, official SWE
  grading, direct alias routing, auth, SSE, admission, and public/private
  boundaries remain the foundations.

## Deliberately deferred

- Full TITO session-server adoption, token ownership, prefix rewriting, and
  training-data collection. The selected session PRD borrows correctness tests.
- Automatic engine restart, health debouncing that admits failed requests,
  replacement-model fallback, and a new recovery supervisor.
- Miles' Ray/Megatron/FSDP training stack, MoE routing replay, and weight-update
  infrastructure.
- Speculative-decoding or prefill/decode performance claims. Those require
  separate pinned managed recipes, hardware-specific feasibility, and local A/B
  evidence.

## Document status

The PRD skill informed the requirement/feature/task structure. The four
router/fleet documents are public mirrors of named Anvil State partitions in
`reviewed` status; they are not permanently approved or planned. Before later State
planning, compare the repository source to the named partition and reconcile
any drift explicitly. The four Miles-informed files remain repository drafts;
they have not been imported into Anvil State, approved as State partitions, or
submitted as GitHub issues.
