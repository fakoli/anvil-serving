# Native KV-offload cleanup is skipped when the serve container is absent

**Status:** Open; post-merge P1 from PR #336 review

## Problem

`anvil-serving serves down` reports an already-absent container as having
nothing to stop or remove, then returns to the next target. For a serve that
previously used vLLM native KV offload, that early return occurs before the
native-offload ownership and shared-memory cleanup path.

If the container was removed outside the managed command but its
`/dev/shm/vllm_offload_*.mmap` remains, the orphan can accumulate and exhaust
shared memory. A later native-offload load can then fail before serving.

## Evidence

- Post-merge Greptile review:
  <https://github.com/fakoli/anvil-serving/pull/336#issuecomment-5155978026>
- The `st == "absent"` branch in `anvil_serving/serves.py::cmd_down` continues
  before `container_uses_native_kv_offload` and
  `prepare_native_kv_offload_shared_memory` are reached.
- PR #336's existing tests cover running, stopped, preserved, and failed cleanup
  paths, but not an absent container with an orphaned offload mmap.

The report was posted after PR #336 had merged. CI for the merged commit was
green, so this is a missing lifecycle case rather than a failed existing gate.

## Required behavior

1. `serves down` must safely inspect/reclaim an orphaned vLLM offload mmap even
   when the named container is absent.
2. The absent path cannot rely on inspecting the deleted container to prove it
   previously used native offload.
3. Preserve the current fail-closed reclaim contract: do not remove a mapped
   file, do not remove while an active native-offload container exists, require
   two matching inspections, validate exact paths, and verify the postcondition.
4. Do not turn an absent ordinary serve into broad shared-memory deletion.
5. Dry-run and confirmation behavior must remain explicit and bounded.

## Acceptance

- Add a hermetic regression test reproducing an absent container plus one
  orphaned `vllm_offload_*.mmap` and proving the managed cleanup path runs.
- Add negative tests for a live mapping, a running native-offload container,
  changed second inspection, and unrelated shared-memory files.
- Document how the absent path decides it is allowed to request safe orphan
  reclamation without deleted-container metadata.
- Run focused serve/host tests, Ruff, the full suite, strict docs, and CLI
  reference audit.
- Perform a bounded live regression before relying on this path for the next
  native-offload recipe.

## Temporary operator guard

Until fixed, run `anvil-serving host shared-memory status` before and after
every native-offload experiment. Use the confirmed managed reclaim command only
when the report classifies the exact files as safe orphans. Never delete
`/dev/shm/vllm_offload_*.mmap` with a broad shell command.
