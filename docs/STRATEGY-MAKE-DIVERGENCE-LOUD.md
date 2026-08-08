# Strategy: make divergence loud

**Date:** 2026-08-08
**Status:** Active program
**Relates to:** ADR-0027; ADR-0028; ADR-0032; ADR-0033; ADR-0034

## Why this document exists

On 2026-08-08 a single session of routine work surfaced six independent live
defects on a system that reported itself healthy throughout. That is the signal
worth designing around, and it is more informative than any feature wishlist.

This document records a SWOT, the strategic conclusion drawn from it, and the
ten-feature program that follows. It exists so the program is executable by
anyone, in any order, without reconstructing the reasoning.

## SWOT

### Strengths

- **Evidence discipline is real.** Promotion is human-gated, benchmark claims
  require recorded artifacts, and readiness is explicitly not qualification
  (ADR-0027, ADR-0028). Very few local-serving stacks have this, and it is the
  product's actual moat.
- **Fail-closed transactional lifecycle.** `serves mode enter` drains routed
  competitors, stops GPU inference, rechecks both roles, starts the owner, and
  restores on failure. This worked correctly under test today.
- **A real control plane, not scripts.** A typed operation catalog, a controller
  with idempotency and audit, and an MCP surface — with a declared plane
  contract (ADR-0033) naming every legal coupling point.
- **Derived state cannot drift.** GPU reservations derive from Docker; supervision
  is Docker itself. Nothing is a written-down number that quietly goes stale.
- **The topology layer already models heterogeneity.** Hosts, runtimes, resource
  roles, transports, capacity policies, and `execution_host_os` covering
  linux/macos/windows exist today.
- **Stdlib-only core.** Portable, auditable, no dependency drift.

### Weaknesses

Every item below was **observed live on 2026-08-08**, not hypothesised.

- **Silent duplicate shadowing.** Two `[[serve]]` entries sharing a name are
  accepted; one silently wins. Caused two incidents in six days, the second of
  which left the promoted primary unmanageable by `serves down`.
- **No fleet-level view.** Answering "is `llm.voice` actually served?" required
  SSH to another host. The router advertised three routes whose backing serves
  had been off for hours, with no signal anywhere.
- **Live state anchored to mutable git checkouts.** The promoted model's recipe
  resolved inside a disposable worktree; the operator CLI is an editable install
  targeting scratch space; the live operator home was six commits behind main
  while serving production.
- **Rollback paths are unverified fictions.** Two were found broken: a referenced
  router profile that did not exist, and a restore-group image pinned to an
  evicted nightly tag. A rollback that cannot run is worse than a declared
  absence.
- **Undetected config drift.** A host's live operator home and the repository
  snapshot of it diverge with nothing reporting the difference.
- **Undetected version skew.** A second host ran a release two minors old and
  therefore took a *different code path* for transport resolution, producing a
  confusing error far from its cause.
- **No node agent on three of four hosts**, so none of the above is observable
  remotely by design.

### Opportunities

- **Public release.** The vocabulary is already close to generic; ADR-0034
  removed the last host-specific concepts from the product layer.
- **Two-engine parity as a correctness oracle.** MLX on Apple Silicon and vLLM
  on sm_120 are genuinely independent implementations. Given repeated sm_120
  kernel defects, a same-model cross-engine check is the cheapest available way
  to separate a model bug from a kernel bug. Nobody else is positioned to do
  this casually.
- **The node-agent design is mostly built.** Controller, bridge, transports, and
  resource-owner execution already exist; they need deployment and completion,
  not invention.

### Threats

- **Single operator, high complexity.** Bus factor of one against a system with
  four hosts, three runtimes, and a promotion pipeline.
- **Upstream instability outside our control.** sm_120 kernel bugs, evicted
  nightly image tags, engine churn.
- **The silent-failure class itself.** This is the dominant threat. Every
  incident today looked healthy right up until someone looked closely. A public
  release carrying these edges would burn first-user trust precisely where the
  product claims its advantage: trustworthy evidence.

## Strategic conclusion

The strengths are all about **being correct when observed**. The weaknesses are
all about **not being observed**. That asymmetry is the whole story.

> The product's dominant failure mode is not incorrectness. It is
> **silent divergence** — reality drifting away from the declaration while every
> surface continues to report success.

This threatens the moat directly. A system whose value proposition is
"trustworthy recorded evidence" cannot afford a class of failure where the
record and the reality disagree and nothing says so.

**Therefore the organising principle for the next program is: make divergence
loud.** Every feature below converts a silent divergence into a loud, early,
specific failure or report. Features are ranked by (incidents closed) ÷ (effort),
not by novelty.

A secondary rule follows from the same analysis: **detection beats prevention
beats automation.** Report a divergence first; refuse the bad state second;
never auto-heal (ADR-0033 forbids a self-healing daemon, and that judgment
stands — automatic repair would recreate silence in a new form).

## The ten-feature program

Ordered by value density. Each names the incident it closes.

| # | Feature | Closes | Size | Status |
| --- | --- | --- | --- | --- |
| 1 | `serves lint` — static manifest defect detection | duplicate shadowing ×2, missing registries, worktree-anchored paths | S | **shipped** 0.25.0 |
| 2 | Aggregator rejects duplicate serve names | duplicate shadowing (prevention half) | S | **shipped** 0.25.0 |
| 3 | `router fleet-status` — per-alias reachability | unbacked voice routes | M | **shipped** 0.26.0 |
| 4 | `serves rollback-check` — prove rollbacks are usable | missing profile, evicted image tag | M | open |
| 5 | Registry-path validation at manifest load | stale registry surfacing mid-transaction | S | **revised — see below** |
| 6 | Worktree-anchor detection in `doctor` | CLI + operator home in scratch checkouts | S | partial (registries covered by 1) |
| 7 | `fleet drift` — live home vs repository snapshot | undetected config drift | M | open |
| 8 | `fleet version` — cross-host version skew | a host silently on an old code path | S | open |
| 9 | Native runtime lifecycle | MLX unlock; ADR-0034 §7 completion | L | open |
| 10 | `fleet bootstrap` — install/upgrade a node agent | no node agent on three of four hosts | L | open |

### Feature 5 was revised during execution

As specified, feature 5 hard-rejected a missing `--registry` path at manifest
load. On inspection that is the wrong design, and the difference from feature 2
is instructive.

A duplicate serve name is **never** legitimate — there is no workflow where two
entries should share a name, so refusing it has no false positives. A registry
file that does not exist yet **can** be legitimate: an operator may author a
serve entry before creating its recipe, or keep an entry for a serve they have
not set up on this host. Hard rejection would block those workflows and, worse,
would block *every other* serves command over one unrelated entry.

Detection already ships: `serves lint` reports `missing-registry` as an error
and exits non-zero, which is usable as a gate without holding the whole manifest
hostage. The remaining work is to make the failure legible at the point of use —
`serves up` naming the missing registry before it starts a transaction — rather
than to refuse at load.

This is the program's own rule applied honestly: detection beats prevention, and
prevention is only correct where the rejected state has no legitimate form.

### 1. `serves lint`

Static analysis of the loaded manifest set. Reports, without touching Docker:
duplicate `name` or `container` across files (naming the winner and the losers);
`router_config` / `rollback_router_config` paths that do not exist; `--registry`
paths inside `up` commands that do not exist; any live path resolving inside a
linked git worktree. Exit non-zero on defects so it is usable as a gate.

### 2. Aggregator rejects duplicates

`load_manifest_set` refuses two entries sharing a `name` or `container`, naming
both files. Feature 1 finds them; this stops them. Breaking by design: it will
reject manifests that load today, which is the point.

### 3. `fleet status`

Reads the router configuration and topology, probes every configured tier,
purpose model, and audio route, and reports per alias: declared → host →
reachable. Answers "is every configured capability actually served" in one
command. Read-only, no mutation.

### 4. `serves rollback-check`

For every declared rollback — promotion plans and exclusive-mode
`rollback_router_config` — verify the profile exists, validates against its
promoted counterpart, resolves to exactly one serve, and that the serve's image
is locally present. A rollback that cannot run must fail this check loudly.

### 5. Registry-path validation at load

A `--registry` path inside an `up` command is validated when the manifest loads,
in the same pass that already validates router profiles. A registry that cannot
be read is a manifest defect, not a runtime surprise.

### 6. Worktree-anchor detection

`git rev-parse --git-common-dir` differing from `--git-dir` is a cheap exact test
for "this path is inside a linked worktree". Applied to recipe registries, the
operator home, and the running package location, surfaced through `doctor`.

### 7. `fleet drift`

Compare each host's live operator home against the repository snapshot for that
host, reporting files that differ, are missing, or are untracked. Read-only.

### 8. `fleet version`

Report the installed version on every reachable host and flag skew against the
operator host. Version skew changes code paths; it must not be invisible.

### 9. Native runtime lifecycle

Implement `runtime = "native"` end to end: process supervision that can *prove*
a stop, memory-derived reservations for unified memory, and removal of the
load-time rejection added in 0.24.0. Unblocks MLX on Apple Silicon.

### 10. `fleet bootstrap`

Install or upgrade the node agent on a declared host over its declared
transport, read-only first, mutation gated. Turns the node-agent rollout from a
manual SSH exercise into a managed operation.

## Execution rules

1. Each feature ships complete: implementation, tests that fail against a broken
   implementation, documentation, and a merged PR. No half-features.
2. Detection features land before prevention features, so an operator can see
   what a new refusal is about to reject before it starts refusing.
3. Breaking changes are acceptable where the existing shape is the defect
   (ADR-0034 set this precedent). Prefer breaking correctly over compatible and
   wrong.
4. No feature may introduce an auto-healing behaviour.
5. Every feature must be usable on a fleet of one, or it is not fit for public
   release.
