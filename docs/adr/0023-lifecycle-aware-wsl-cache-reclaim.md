# ADR-0023 — Lifecycle-aware WSL page-cache reclaim

- **Status:** **Accepted** (2026-07-18)
- **Date:** 2026-07-18
- **Relates to:** [ADR-0021](0021-cli-interaction-contract.md) (CLI interaction contract) ·
  [ADR-0018](0018-router-transition-safety.md) (safe model transitions) ·
  `anvil_serving/host.py`, `anvil_serving/models.py`, `anvil_serving/serves.py`

## Context

Large model downloads and model starts can stream tens of gigabytes through the shared WSL2 VM.
Linux retains those clean file pages in page cache after the operation, so `vmmemWSL` can remain
large even when the model is resident in GPU memory and Docker no longer needs the cached pages.
Windows then experiences memory pressure until WSL's own reclaim catches up or an operator runs
`host reclaim` manually.

Anvil Serving already owns the safe boundaries for its model pulls and managed serve lifecycle.
It knows when an operation was confirmed, when the model is ready, and—during switch or
promotion—when health, identity, preflight, and router-readiness gates have passed. Requiring a
second command after every large operation loses that context and repeatedly leaves the machine
in the same avoidable pressure state.

The reclaim affects the whole WSL VM, not only the initiating container. It can increase later
disk reads, so it must remain an explicit host policy rather than an unconditional product
default. It must also be a best-effort postcondition: cache management may not turn a successful
download or healthy model start into a failed lifecycle operation.

## Considered options

1. **Rely only on WSL `autoMemoryReclaim` or Linux tuning.** Rejected as the complete solution.
   Those mechanisms remain useful, but they do not align reclaim with Anvil's known download and
   readiness boundaries and can lag large bursts.
2. **Add a daemon, timer, or persistent reclaim queue.** Rejected. A background owner adds state,
   shutdown, retry, and observation complexity for work that has a natural synchronous boundary.
3. **Add another lifecycle flag or a new routine CLI leaf.** Rejected. Operators would need to
   remember it on every pull or load, duplicating the consent and lifecycle information the
   existing command already has.
4. **Reclaim through per-container cgroups.** Rejected for v1. Docker Desktop's WSL page cache is
   VM-wide, the required ownership is not reliably represented by one container cgroup, and the
   portability and privilege burden is disproportionate.
5. **Integrate request-triggered multiplexer or request-time ComfyUI loading.** Rejected for v1.
   Those paths lack the same bounded readiness and operator-confirmation boundary and must not add
   synchronous host-wide work to request latency.
6. **Use a persistent machine opt-in at confirmed public lifecycle boundaries.** Chosen.

## Decision

### 1. The policy is persistent, strict, and disabled by default

The config home contains `host.toml`, resolved through `ANVIL_SERVING_HOME` or the normal
`~/.anvil-serving` default:

```toml
schema_version = 1

[cache_reclaim]
enabled = false
distro = "docker-desktop"
threshold_gb = 16
```

A missing file or section means disabled. Once present, unknown fields, invalid types, invalid
values, or an unsupported schema fail before the parent model operation mutates state. This is a
machine policy, not router topology, model recipe, or per-command configuration.

### 2. Existing confirmation authorizes the declared postcondition

No lifecycle flag or additional prompt is added. The dry run for a covered operation discloses
whether automatic reclaim is enabled, its source path, target distro, threshold, and host
applicability. Applying that reviewed command with its existing `--confirm` authorizes both the
model operation and the declared best-effort postcondition.

Covered public boundaries are `models pull`, `models recipes load`, manifest-owned `serves up`,
`serves adopt`, `serves switch`, and `serves promote`, including an explicitly requested
rollback. Ad-hoc `serves up --compose`, voice, ComfyUI request-time loading, and the
request-triggered multiplexer are excluded. Nested serve calls never reclaim independently.

Controller-dispatched serve operations inherit the behavior because they execute the same
resource-owner CLI boundary. There is no separate MCP tool.

### 3. Reclaim is readiness-aware and evidence-gated

The hook records cache before the parent operation. A pull can evaluate immediately after a
successful download. Recipe loads and ordinary managed up/adopt operations wait at most 600
seconds for their declared HTTP health. Switch and promotion reuse their stronger existing
health, exact-identity, preflight, and router-readiness gates.

Reclaim runs only on Windows/WSL when the policy is enabled, both cache samples are readable,
post-operation cache is at least the configured threshold, cache grew by at least 1 GiB, and
growth settles to at most 0.25 GiB/s. A still-growing cache is sampled every two seconds for at
most 30 seconds, then refused as active I/O. Automatic reclaim never has a force override.

The structured result is one of `reclaimed`, `disabled`, `not-applicable`, `below-threshold`,
`no-operation-growth`, `active-io`, `readiness-timeout`, `unavailable`, or `failed`, with before
and after cache values when known. Library code returns the result; CLI boundaries render it.

### 4. Drop page cache only, and keep the parent result authoritative

Both the automatic hook and standalone `host reclaim` run:

```sh
sync && echo 1 > /proc/sys/vm/drop_caches
```

Value `1` evicts clean page cache without also targeting reclaimable slab objects such as dentries
and inodes. The standalone command retains its explicit confirmation, watchdog, active-I/O
refusal, and manual `--force` semantics.

A skipped or failed postcondition emits a warning but preserves the successful parent exit code.
A readiness timeout skips reclaim without stopping a newly started container. Failed or dry-run
parent operations never invoke the hook.

## Consequences

- One host-level opt-in removes a repeated operator chore from the Anvil-owned lifecycle without
  bloating the command tree.
- Reclaim can make a later file access read from storage again. The threshold, growth gate,
  settled-I/O check, and default-off policy make that VM-wide performance tradeoff explicit.
- The implementation is synchronous and bounded: no background service, timer, WSL restart,
  Docker prune, disk-cache deletion, or Linux sysctl mutation is introduced.
- Request-path and ad-hoc lifecycle gaps remain deliberate. They require a separate consent and
  readiness design before inclusion.
- Host status gains additive policy metadata so operators and workbenches can explain what the
  machine will do without creating another control-plane operation.
