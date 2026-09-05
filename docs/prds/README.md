# Proposed improvements informed by Miles

These PRDs turn the useful parts of the Miles review into bounded Anvil Serving
changes. They are proposed implementation contracts, not shipped features.
Each includes requirements, source pointers, small tasks, negative tests, and
completion evidence for an agent starting without the original conversation.

**Reviewed:** 2026-09-05. **Anvil baseline:** `f50dca489780b95d0cd98dee59cf620618c4ccd1`.
**Miles baseline:** [`d2fc97ce581577e255e494801d7568747d5a10d7`](https://github.com/radixark/miles/tree/d2fc97ce581577e255e494801d7568747d5a10d7).
Source inspection is the basis for the proposals; no Miles performance or local
GPU qualification is claimed.

## PRDs and delivery order

| Order | PRD | Outcome | Suggested executor | Dependencies |
| --- | --- | --- | --- | --- |
| 1 | [Fence readiness observations across invalidation](readiness-generation-fencing.md) | Old probes cannot restore invalidated readiness. | Terra high for concurrency implementation; medium for fixtures/docs | None |
| 2 | [Protocol and session correctness corpus](session-protocol-corpus.md) | Reproducible tool, retry, streaming, and history compatibility checks. | Terra high for dialect changes; medium for corpus wiring | None; shares router tests with PRD 1 |
| 3 | [Correlated benchmark timeline](benchmark-timeline.md) | Explain benchmark time using measured phases, turns, and resource samples. | Terra medium for schema/UI; high for persistence and cancellation edges | None; consumes existing jobs and telemetry |
| 4 | [Isolated Harbor environment evaluation](harbor-environment-evaluation.md) | Run independently graded terminal/custom tasks through managed benchmark jobs. | Terra medium after the pinned adapter contract is proven | Its own compatibility spike must pass before adapter integration |

PRDs 1 and 2 are the best first implementation batch. PRD 3 is the largest
operator-facing improvement. PRD 4 expands evaluation coverage but has a larger
external compatibility surface. The Harbor adapter can ship without the timeline;
the timeline must not depend on installing Harbor.

## Instructions for an implementing agent

1. Read the selected PRD, the current `AGENTS.md`, `README.md`, and
   `CLAUDE.md`. Resolve the public/private workspace using the installed
   workspace resolver before choosing an operator or product path.
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

The PRD skill informed the requirement/feature/task structure. These files are
repository drafts; they have not been imported into Anvil State, approved as
State partitions, or submitted as GitHub issues.
