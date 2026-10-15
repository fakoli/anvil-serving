# Docker model pulls need paired requested memory ceilings

**Status:** Resolved by paired Docker CLI ceilings; campaign evidence retains the
existing CLI JSON envelope and token-free human command output.

## Problem

`anvil-serving models pull` downloads into a named Docker volume through the
legacy Docker path. It previously had no way to request Docker memory or
memory-plus-swap ceilings for a large acquisition. The existing CLI JSON
envelope preserves the token-free human plan output, including the exact Docker
argv, so a separate receipt format is unnecessary for this bounded addition.

## Required behavior

1. Add paired `--memory-mib` and `--memory-swap-mib` Docker-only flags. Both
   are positive integers, and memory-plus-swap is at least memory.
2. Render their exact byte values in Docker `--memory` and `--memory-swap`.
   Equal values request no extra host swap for the container.
3. Preserve the legacy human output and unbounded Docker default when no
   ceilings are configured. Native-cache pulls reject the Docker-only flags.
4. State limits as requested Docker configuration. Docker flag acceptance does
   not establish an actual cgroup readback.

## Acceptance

- Hermetic tests cover paired positive MiB inputs, equal values (zero extra
  host swap), invalid or partial pairs, exact byte argv rendering, native
  rejection, and CLI JSON capture of the token-free plan.
- Documentation distinguishes configured Docker limits from measured cgroups.
- Preview has no Docker, network, credential-value, or volume-write side
  effect.
