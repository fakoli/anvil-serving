# ADR-0035 — Fleet configuration reconciliation: git as the state store, controller-mediated install and adopt

- **Status:** Accepted
- **Date:** 2026-08-08
- **Relates to:** ADR-0027; ADR-0032; ADR-0033; ADR-0034
- **Extends:** ADR-0034 §2–§4 (the controller as the node agent; typed
  operations; transport policy)
- **Schedules:** the per-client-token work deferred in ADR-0033 §3.4

Companion design document: [Fleet state: relationships, planes, and what
replicates](../FLEET-STATE.md) — the gateway hierarchy, the binding rule,
and the full replication matrix.

This ADR is written for a project intended to be publicly usable. The
four-host reference instance appears only as motivation; concrete host names,
addresses, and route assignments remain private operator state per ADR-0032.

## Context

One day of fleet operation surfaced every failure mode this decision
addresses. A single router's vocabulary lived in four layers — three files
plus the installed volume — and two of the file layers still described a
topology retired the previous day. The canonical fleet topology document was
never committed anywhere. Relocating one capability (voice) required editing
live operator homes on three hosts by hand — shell-quoted, host-by-host,
each with hand-named backups. Two operator sessions edited the same
repository concurrently. CLI versions spanned 0.13.1 to 0.30.0 across four
hosts because no document names a canonical version. And configuration is
not the only thing that drifts: a host's Python interpreter was removed out
from under a live CLI mid-operation, which no file synchronization would
have detected — but one read-only version probe did.

The operator's question was direct: should the fleet run a
ZooKeeper-class replication service, or should the controller be extended to
handle replication between workstations?

Two prior decisions constrain any answer. ADR-0033 requires state to be
**derived by inspection, never written down and trusted**, and prohibits
self-healing daemons. The topology custody rule (reaffirmed by ADR-0034)
requires drift to be **reported, never auto-fixed** — installing a live
host's configuration is a reviewed operation. Two same-day incidents showed
why these rules extend to any synchronization mechanism: a topology field
(`expected_node`) is fail-closed coupled to a process flag (`--node-id`), so
a file replicated without its process restart takes the node offline; and a
router file edit does nothing until a separate install writes the volume.
File movement and process choreography are one operation, and only a typed,
domain-aware verb can know that.

## Decision

### 1. No coordination service. Git is the fleet configuration state store.

The fleet has one writer (the operator) and a handful of nodes. Consensus
buys nothing at one writer, and symmetric file replication is actively
wrong: per-host views legitimately differ (identity lines, real device
UUIDs, parked-entry blocks), and secrets must never transit a store. The
private operator repository is already a durable, ordered, audited
replication log whose conflict resolution is human merge — proven by
concurrent operator sessions landing interleaved changes without loss. The
repository IS the desired-state store; nothing new is run.

### 2. Reconciliation over replication.

Drift is computed by inspection and reported; convergence is an explicit,
confirm-gated, audited operation. Nothing applies configuration on a timer.
The no-self-healing rule extends from serve supervision (ADR-0033/0034) to
configuration: an unattended writer is a self-healing daemon for config and
is prohibited for the same reason.

### 3. Three controller verbs close the last mile.

All three follow the pattern the router already proved
(`router install-config --confirm`): typed, allowlisted per host,
operation-keyed in the audit log, SSH never required.

1. **`config-inventory` / `config-export`** *(exists — the typed read
   surface)*: enumerate and export a host's operator-home files, bounded
   and fail-closed on unsupported formats.
2. **`config-install`** *(new — the forward arrow)*: fingerprint the live
   file, write a dated backup, derive the per-host view from the repository
   copy (applying the allowed per-host substitutions), install, and return
   a diff receipt. Domain-aware installers own the coupled process actions
   — a router install writes the volume; a transport-identity install
   restarts the controller with its matching flag. Always operator-invoked
   with `--confirm`.
3. **`config-adopt`** *(new — the reverse arrow)*: capture a live file into
   the repository mirror with provenance. This is how the store stays
   truthful when reality legitimately moves first (emergency repair,
   another session's live fix). Adoption is a commit, so it is reviewed by
   construction.

### 4. The drift surface is scheduled, read-only, and layered.

`fleet drift` and `fleet version` run on a schedule from the always-on
operator seat and produce dated reports (evidence artifacts, per ADR-0027).
The drift comparison extends beyond file layers to **derived and runtime
layers**: the router's installed volume, supervision units (launchd agents,
scheduled tasks), and interpreter/tool versions — because facts drift too,
and inspection is the only mechanism that catches a removed interpreter or
a dead launcher shadowing a healthy install on PATH.

### 5. Version convergence is artifact distribution, not config sync.

A canonical fleet version is **named in the repository** (its absence is
what allowed a 0.13.1–0.30.0 spread), and a `fleet upgrade` verb installs
it per host using the recorded per-host strategy: staged wheels on Windows
hosts (pip cannot build from git there; wheels are built in short paths and
shipped), pinned-commit or tagged uv installs on macOS. Every install the
fleet runs must be reconstructable from a commit hash.

### 6. Secrets never transit the store or the verbs.

The envfile chain on each host owns values. The repository and the
config verbs carry names and templates only. This is already the ADR-0032
boundary; it is restated here because a naive sync system is exactly how
that boundary would erode.

### 7. Per-client controller tokens precede the write verbs.

`config-install` raises the stakes on the shared static token: a writer
credential valid on every node is a fleet-wide blast radius. The per-client
token and rotation work deferred by ADR-0033 §3.4 becomes a prerequisite
for shipping `config-install`, not a parallel track. The read verbs
(`fleet drift`, `config-adopt` capture) may ship against the current token.

### 8. Bootstrap closes the missing-controller prerequisite, not config sync.

**2026-09-05 amendment — design pending implementation.**
[ADR-0034 section 4](0034-fleet-control-plane-and-node-runtime-classes.md)
defines the normal, bootstrap, acceptance, rollback and manual-recovery
boundaries for the [managed bootstrap PRD](../prds/fleet-node-enrollment.md).
Installing the node runtime is artifact distribution under section 5, not
permission to install/adopt configuration under section 3.

A reachable node remains controller-first. Only explicit recovery of a
declared absent/unavailable controller uses the pinned, fixed-operation SSH
receiver; no host discovery, arbitrary SSH command or credential transfer is
introduced. A read-only plan binds topology and artifact identities. Apply
requires an unchanged plan, confirmation, local bootstrap policy and the
per-client `node-admin:bootstrap` scope. That scope is required for bootstrap
reads too; neither a legacy shared token nor `workloads:read` confers it.
Policy references resolve separately on the operator/node and never travel in
the install bundle.

Immutable generations plus an atomic current pointer preserve the prior
runtime. A flushed operation journal brackets activation and the declared
supervisor restart. Acceptance must inspect the newly authenticated runtime's
exact node, package and immutable build identity, compatible protocol, expected
per-node catalog and health. A copied wheel, successful restart, stale
transport or matching version alone cannot close the transition. Status
reconciles the journal with observed runtime/supervisor state rather than
treating journal intent as liveness. Failure requires verified rollback to the
prior generation, or explicit manual recovery with evidence retained.

Bootstrap does not modify topology, operator-home configuration, credentials,
routes, serves, GPU modes, client profiles or deployment approvals. Config
install/adopt and workload promotion keep their separate managed gates.
Neither a repository merge nor bootstrap acceptance triggers them, and no
timer retries or converges installation automatically. The first bootstrap
adapters cover only a preprovisioned Windows scheduled task or Linux systemd
user supervisor running a wheel-installed venv; Docker/macOS layouts remain
unsupported, without weakening the broader fleet architecture.

## Consequences

- No new infrastructure runs anywhere. The operator seat gains two
  scheduled read-only reports; the nodes gain two allowlisted operations.
- Hand-run cutovers become one audited verb per host, and the
  nine-backup-files convention becomes machinery instead of discipline.
- Drift stops being discovered mid-task by whoever happens to look; it is
  a standing report with a date on it.
- The repository becomes authoritative *because* both arrows exist:
  install makes the fleet look like the repo, adopt makes the repo admit
  what the fleet became. A one-arrow system rots in whichever direction it
  cannot express.
- Rejected: ZooKeeper-class services and file-sync daemons (multiple-writer
  solutions to a single-writer problem, and both would auto-fix), and
  repository-triggered auto-apply (a config self-healing daemon; also
  incompatible with fail-closed config/process couplings).
