# Durable capacity campaign jobs and cursor-based recipe logs

**Status:** Open — deferred after the 2026-09-04 benchmark campaign

## Problem

Capacity benchmarking is a synchronous one-cell command. A multi-configuration
campaign therefore requires repeated interactive launch, wait, inspect, record,
and resume steps even though durable benchmark jobs with bounded cursor logs
already exist for context, agentic, and SWE suites.

Managed recipe logs provide a bounded `--tail`, but repeated reads return the
same health/startup history and full data body. There is no cursor or concise
error-first continuation for an agent supervising a long model load. Large
repeated outputs make the earliest actionable failure harder to see and expand
session context.

## Proposed direction

Extend the existing benchmark job/store/worker contracts rather than creating
a second orchestration framework:

- add a capacity campaign specification with immutable cell identities,
  dependency and stop rules, exact launcher/configuration provenance, and a
  deterministic artifact path per cell;
- resume by cell digest without rerunning complete successful cells;
- expose bounded cursor-addressable progress, failure, and artifact events;
- keep live serve/route mutation in existing separately confirmed managed
  lifecycle commands;
- add cursor, limit, and error/warning-only continuation to managed recipe logs,
  preserving the existing bounded tail for compatibility; and
- make terminal job state and incomplete/failed native artifacts survive an
  interrupted client session.

## Acceptance

1. A synthetic capacity matrix submits, completes, and records multiple cells
   through the durable job store without a live endpoint.
2. Re-submitting an identical completed cell digest is idempotent; changing a
   workload or configuration control creates a distinct cell.
3. Interruption followed by resume schedules only pending/failed-as-authorized
   cells and preserves completed native artifacts.
4. A correctness or resource stop rule prevents dependent finalist cells while
   retaining the failed artifact and bounded reason.
5. Recipe-log cursor reads return only newer entries, report truncation, and
   can return warnings/errors without the repeated full log body.
6. Logs and job records reject secrets, private paths, oversized messages, and
   traversal outside the owned run root.
7. Unit tests use synthetic stores and subprocess fakes; they never call Docker,
   a router, a model endpoint, or the network.

## Boundary

This is deferred infrastructure, beyond the current publication and benchmark-tool
hardening change. It does not authorize a continuous scheduler, service mutation, route
change, or benchmark rerun.
