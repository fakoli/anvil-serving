# ADR-0034 — Fleet control plane on the gateway host, and node runtime classes

- **Status:** Accepted
- **Date:** 2026-08-08
- **Relates to:** ADR-0017; ADR-0027; ADR-0028; ADR-0030; ADR-0031; ADR-0032
- **Fires the deferred trigger in:** ADR-0033 §3.4 (fleet registry, relay, per-client tokens)
- **Amends:** ADR-0033 §1, the *Supervision* row of the durability table

This ADR is written for a project intended to be publicly usable. Product
concepts below are stated generically; Fakoli Dark / Mid-Mod / Mini / AI-MBP25
appear only as the reference instance that motivated them, and the concrete
host names, GPU identities, and route assignments remain private operator state
per ADR-0032. Backward compatibility is explicitly **not** a constraint on this
decision: where the existing shape is wrong for a multi-host, multi-runtime
fleet, it is changed rather than extended.

## Context

The reference topology outgrew its own description. It now spans four machines
with materially different roles:

- `fakoli-dark` — two RTX PRO 6000 (sm_120), Linux containers under WSL2. The
  serving host and, today, the de facto control host.
- `fakoli-mid-mod` — RTX 5090. The permanent voice host since 2026-08-07:
  STT/TTS and the low-latency voice LLM.
- `fakoli-mini` — OpenClaw gateway and audio proxy. Model-free by capacity
  policy, and already declared `roles = ["gateway", "operator", "proxy"]`.
- `ai-mbp25` — 48 GB M4 Max MacBook Pro. Evaluation worker for durable-context,
  agentic-recovery, and pinned SWE jobs. Capable of MLX serving; also runs
  Docker.

Three problems motivated this decision.

**The map disagrees with the territory.** Dark's `operator-topology.toml`
declares only mini and dark. It never mentions mid-mod, even though Dark's own
live router routes audio to mid-mod's tailnet address. `fakoli-mid-mod` carries
a second, independent topology document. There is no fleet-level view, and the
live router configuration is more accurate than any committed topology.

**Live state is anchored to mutable git checkouts.** On 2026-08-08 three
separate layers were found to depend on disposable or drifting checkouts: the
promoted primary's recipe resolved into a git worktree on an unrelated branch;
the operator CLI is an editable install targeting a scratch worktree, so a
branch switch silently changes the control plane; and `ANVIL_SERVING_HOME` was
a working tree six commits behind `main` while serving production. Centralizing
control over a fleet in this condition would multiply the fragility by the
number of hosts.

**The serve lifecycle assumes containers.** `load_manifest` requires
`container` on every `[[serve]]` entry. On Apple Silicon, Docker runs a Linux
VM with no passthrough to the unified-memory GPU, and MLX links against Metal
on the host side of that boundary. A model-capable macOS node is therefore
impossible to express today, independent of any anvil design choice.

Working in the system's favour: the upper layers already anticipate
heterogeneity. `topology.py` declares `_HOST_OSES = {"linux", "macos",
"windows"}`. `CommandSpec` carries `execution_host_os`, `execution_runtime_roles`,
`supported_transports` (`local` / `controller` / `ssh`), and an execution policy
of `resource-owner` — a command runs where its resource lives. Topology already
separates `native` from `docker` runtimes, `evaluate_capacity_policy` already
gates experimental model workloads on model-free hosts, and the packaged
TypeScript bridge is documented for split-host controller mode. The container
assumption is confined to the middle layer.

## Decision

### 1. Control plane on Mini; data plane stays on the nodes

`fakoli-mini` becomes the operator: authoritative fleet topology, operator home,
and dispatcher. Mini is never in the inference request path. Each model-capable
host keeps its own router and serves its own traffic.

Loss of Mini costs management, never serving. No node may depend on Mini to keep
serving, and every node remains independently recoverable from its own host.

### 2. The controller is the node agent

No new daemon. `anvil-serving-controller` is promoted from a Dark-specific
service to the node agent every host runs, with its exposed operation catalog
scoped by that host's declared roles. `ai-mbp25` already demonstrates the model:
its controller is restricted to benchmark and job operations.

### 3. Typed operations only — no remote Docker socket

Mini causes container changes on each node; Mini never speaks Docker to a node.
A remote Docker socket forfeits the allowlist, the operation-keyed audit trail,
dry-run and preview, and the fail-closed transactional semantics that
`serves mode enter` already implements. On `ai-mbp25` it would also be actively
misleading, since that Docker daemon cannot run the models at all.

### 4. Transport: controller primary, SSH recovery-only

`controller` over the tailnet with per-node bearer tokens is the normal path.
`ssh` remains `recovery_capable` and requires an explicit fallback flag after a
proven pre-dispatch controller failure. SSH repairs a node whose controller is
down; it is not a general transport.

### 5. No cross-host arbitration

Voice ownership of `fakoli-mid-mod` is **sacred**. Mini may not reclaim mid-mod
for general serving.

Consequently the fleet requires no cross-host scheduler. `dual-gpu-exclusive`
remains host-scoped: Dark being exclusive says nothing about mid-mod. Fan-out
means declaring desired state per capability and converging **per host
independently**, reporting N independent outcomes. Partial success is the normal
result in a heterogeneous fleet and must not be reported as failure.

What is fleet-level is the capability question — is `llm.primary` served
anywhere, and by what — not placement.

### 6. Node capability is declared along three orthogonal axes

Capacity policy stops being a single opaque label. A host declares:

1. **Runtime class** — `docker` or `native`. What the lifecycle driver speaks.
2. **Memory model** — `discrete` (a device pool sized independently of host RAM)
   or `unified` (a budget carved out of host RAM, where taking memory for a model
   starves the operating system). These need different reserve arithmetic;
   `vram_mib` against a discrete pool is not a correct model for unified memory.
3. **Availability class** — `continuous` (always-on, thermally stable) or
   `opportunistic` (sleeps, changes networks, runs on battery, throttles).

**Promotion eligibility is derived, not declared.** Only a `continuous` host may
back a promoted alias. This follows directly from ADR-0027 and ADR-0028: the
product's value is reproducible evidence, and a throughput number measured on a
throttling, battery-powered, sleeping machine is not reproducible. Allowing an
`opportunistic` host to back a promoted alias would let a non-reproducible
measurement become a published claim, which is the exact failure the evidence
boundary exists to prevent.

A host may also carry a **reserved capability**, meaning its resources serve one
declared purpose and are not reclaimable for general serving.

In the reference instance: the two-GPU Linux host is `docker` / `discrete` /
`continuous` and promotion-eligible; the single-GPU voice host is the same but
carries a reserved voice capability; the Apple Silicon laptop is `native` /
`unified` / `opportunistic`, so it is model-capable but not promotion-eligible,
and its serve is `residency = "evictable"` behind its evaluation role; the
gateway host is model-free.

### 7. Serve lifecycle gains a required runtime discriminator

A `[[serve]]` entry declares `runtime` explicitly. `container` is required when
`runtime = "docker"` and forbidden when `runtime = "native"`. Because backward
compatibility is not a constraint here, `runtime` is **required rather than
defaulted**: an implicit default is what let the container assumption become
invisible in the first place, and a manifest should state which lifecycle it
expects.

Both runtimes satisfy one contract — start, stop, health, model identity — so
the router is unchanged. An OpenAI-compatible endpoint is already a legal tier
under existing `RelayBackend` and `model_identity` handling, whatever produced it.

**This amends ADR-0033's supervision row.** That ADR states "Docker itself is the
ledger. No pidfiles, no service registry, no self-healing daemon." That remains
correct and unchanged for `runtime = "docker"`. It cannot hold for
`runtime = "native"`, which has no Docker to ask. Native supervision therefore
requires a host-level supervision ledger — and the constraint that survives from
0033 is the *principle*, not the mechanism: supervision state must be **derived
by inspection, never written down and trusted**. A native serve's liveness and
memory footprint are read from the operating system at query time, exactly as
Docker-backed state is read from Docker. `serves down` must still be able to
*prove* the process stopped, preserving the fail-closed property exclusive mode
depends on. The "no self-healing daemon" prohibition stands for both runtimes.

### 8. Two-engine parity is a first-class evidence artifact

MLX on Apple Silicon and vLLM on sm_120 are independent implementations. Given
the project's repeated encounters with sm_120 kernel defects, a same-model
same-quantization comparison across the two is the cheapest available oracle for
distinguishing a model defect from a kernel defect. The benchmark evidence
schema gains a two-engine comparison artifact now, while the evidence contract
is being extended, rather than being retrofitted later.

### 9. Operator home: git as record, materialized for deployment

Fleet configuration is reviewed and versioned in git, then **materialized** to a
stable directory that is not a branch-switchable working tree. The live system
reads the materialized copy. This preserves history and promotion review while
removing the class of failure observed on 2026-08-08.

## Consequences

**Positive.** Mini failure degrades management only. The absence of cross-host
arbitration removes the main source of correlated multi-host failure and deletes
a scheduler from the design. Typed operations keep the existing fail-closed and
audit properties across the network unchanged. The runtime discriminator makes
heterogeneous hosts expressible without weakening the container path. Most of
the required abstraction — topology, targets, transports, capacity policies,
controller, bridge — already exists.

**Negative.** Per-node tokens on the operator host concentrate blast radius and
must be independently rotatable. Two lifecycle implementations and two
supervision derivations are more surface than one. Materializing the operator
home adds a deployment step between review and effect. A reserved capability
means that host's resources sit idle whenever the reserved purpose is idle; this
is accepted deliberately as the price of removing cross-host arbitration.

**Breaking changes accepted.** `runtime` becomes a required field on every serve
entry, so existing manifests must be updated. Capacity policy moves from a single
label to three declared axes. These are chosen over compatible extensions because
the implicit-container assumption and the opaque capacity label are precisely
what made a heterogeneous fleet inexpressible, and carrying them forward as
defaults would preserve the defect under a new name.

**Public-project posture.** Node classes, runtime classes, memory models, and
availability classes are product vocabulary and ship publicly with generic
examples. Host identities, GPU UUIDs, tailnet addresses, route assignments, and
promoted-stack state remain private operator configuration under ADR-0032.
A third party must be able to describe their own fleet — a single workstation, a
Mac laptop, a rack of Linux GPU hosts — using only the public vocabulary, with no
Fakoli-specific concept required.

**Forbidden by this decision.** A remote Docker socket. The operator host in the
inference path. A fleet-wide exclusive mode or global GPU lock. Promotion of any
alias onto an `opportunistic` host. Publishing measurements from an
`opportunistic` host alongside `continuous`-host results without conditions
stated prominently enough that the two cannot be cross-read.

**Sequencing.** Reconciling one fleet topology and de-anchoring the control
plane from git checkouts are prerequisites, not cleanup; neither depends on the
remaining decisions. Remote mutation is introduced read-only first, then on
mid-mod, and last on Dark's exclusive-mode transitions.
