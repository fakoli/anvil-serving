# Recipe-loaded containers have no typed discovery surface

**Status:** Open

## Problem

`models recipes status` and `models recipes unload` require both the exact
recipe model and container name, but Anvil Serving has no typed command that
lists currently running recipe-loaded containers. On 2026-08-15, the selected
operator manifests reported every model serve absent and both GPU roles
unowned while router transition status independently proved two ready Qwen3.8
tiers and GPU telemetry showed roughly 90 GiB allocated on each card.

The Qwen3.8 MTP-depth qualification therefore required one narrow read-only
Docker inspection to recover the existing containers' names and recipe labels
before returning to managed `models recipes status` and `models recipes unload`
operations. Docker was not used to start, stop, remove, or modify a container.

## Required behavior

1. Add a typed read-only `models recipes running` surface to the CLI and the
   restricted controller/MCP catalog.
2. Discover only containers carrying Anvil recipe identity labels and report
   container name, model/revision, registry or recipe digest, image digest,
   served identity, bound port, GPU selection, and running/health state.
3. Let `models recipes status` and `unload` select an unambiguous discovered
   container without requiring the operator to obtain its name out of band.
4. Reconcile recipe-loaded containers into `serves status` and GPU ownership,
   or explicitly classify them as unmanaged-by-manifest recipe owners instead
   of reporting both roles free.
5. Never expose environment values, credentials, raw commands, or private
   endpoint addresses in human or JSON output.

## Acceptance

- Hermetic tests cover no containers, one candidate, two candidates using the
  same model, malformed/missing labels, exited containers, and non-Anvil
  containers.
- Human and JSON output are stable, bounded, and secret-free.
- A ready recipe-loaded model can be discovered, inspected, and unloaded using
  only Anvil Serving commands.
- `serves status` no longer reports a GPU role as free when a discovered
  recipe-loaded candidate owns it.
