# Bounded recipe loads from Windows Docker Desktop

Status: implemented and independently verified.

An unqualified offloaded-model recipe needs explicit RAM and RAM-plus-swap
limits. The existing preflight rejected every Windows named-pipe endpoint,
even when the local Docker Desktop Linux daemon enforced those limits.

Accept only the known local Desktop pipes, Linux/WSL engine identity and
enforced cgroup-v2 memory/swap capabilities. Bind the VM observation to the
daemon's exact memory total. Check available VM memory and swap, Windows
available physical memory, and the VM ceiling against Windows physical RAM.
Keep the existing recommended Windows reserve separate from the recipe's VM
reserve. Missing, inconsistent or remote observations fail closed.

Existing known-good recipes without declared limits retain their behavior.
No limits are removed to make a candidate load. Focused regression coverage
checks both memory boundaries, unknown endpoints and malformed observations.

Validation: 84 focused recipe/containment tests passed. Independent review
identified missing swap observations and inconsistent VM totals; both now fail
closed. A managed live CPU allocation canary requested 128 MiB under a 32 MiB
RAM/32 MiB total ceiling. Docker recorded the exact limits, OOMKilled=true and
exit 137. Managed unload removed the canary; existing model/controller identity
and readiness remained unchanged. The inherited GPU device request and cache
mount were not used by the canary. Private receipts retain exact host details.
