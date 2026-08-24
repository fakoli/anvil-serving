---
name: anvil-serving-candidate-operations
description: Prepare, recover, switch, test, and restore an Anvil Serving model candidate through managed recipes and serves. Use for RTX 5090 lane preparation, Windows/WSL GPU-memory discrepancies, HF token handoff, unhealthy recipe-loaded containers, Q4/MTP or other profile switches, live endpoint checks, and bounded candidate tests that must preserve the starting state.
---

# Anvil Serving Candidate Operations

Run candidate work as one evidence-backed transaction. Container health,
endpoint readiness, preflight, benchmark qualification, and route promotion are
separate states; never infer one from another.

## Start state

1. Read `README.md`, `CLAUDE.md`, the selected recipe, and
   `skills/anvil-serving-llm-qualification/SKILL.md` before mutation.
2. Record repository revision and dirty state, command host/runtime, topology
   target, router and serve state, reservations, loaded recipe containers, GPU
   inventory, candidate recipe revision, and exact state to restore.
3. Use `operation_contracts` and the structured controller/MCP tools first.
   Use the documented CLI commands below only when the wrapper is absent and
   report that product gap.

## Preflight the host boundary

1. Resolve credentials by variable name only. If the general
   `host-operations:credential-source-diagnostics` plugin skill is installed,
   use it to trace the shell, Windows/WSL, service, Compose, and container
   boundary. Otherwise check presence without printing, hashing, or copying the
   value. A `.env` file is not proof that the candidate process receives
   `HF_TOKEN` or the recipe's declared credential variable.
2. Run `anvil-serving host gpus --json` for product inventory. On Windows/WSL,
   use `host-operations:windows-gpu-lane-hygiene` when installed; otherwise
   capture Windows and WSL NVIDIA views, exact PIDs, managed workloads, active
   allocation, and residual WDDM/driver reservation. Do not claim the lane is
   empty from a utilization percentage or an empty CUDA process table alone.
3. Stop for a human gate before terminating a process, changing Windows
   graphics preferences, restarting Docker/WSL, unloading a live serve, or
   changing a deployment role. Prefer per-app iGPU placement and exact managed
   workload lifecycle over global graphics changes.

## Select one lifecycle path

Use recipe lifecycle for an isolated candidate:

```text
anvil-serving models recipes show MODEL
anvil-serving models recipes load MODEL --container NAME --gpu-device GPU --dry-run
anvil-serving models recipes load MODEL --container NAME --gpu-device GPU --confirm
anvil-serving models recipes status MODEL --container NAME
anvil-serving models recipes logs MODEL --container NAME --tail 200
anvil-serving models recipes unload MODEL --container NAME --dry-run
anvil-serving models recipes unload MODEL --container NAME --confirm
```

Use `anvil-serving serves switch ROLE MODEL --dry-run` only for a declared
deployment-role change. Apply with `--confirm` only after the preview identifies
the exact source artifacts and rollback state. A recipe load does not change
router policy, and an isolated test does not require an alias change.

## Recover an unhealthy candidate

1. Run recipe `status`, then bounded recipe `logs`. Capture the earliest
   actionable startup error and classify authentication, authorization/license,
   missing dependency, incompatible engine/model, resource exhaustion, or
   failed health contract.
2. Do not "fix unhealthy" by immediately recreating the container. First prove
   the owning cause and whether the recipe, image, environment handoff, health
   check, or product status surface is defective.
3. If replacement is justified, verify the exact model/container identity,
   preview `unload`, obtain the mutation gate, unload, re-check the GPU lane,
   preview `load`, load, and follow bounded logs from first startup.
4. Use raw Docker only for the narrowest read-only diagnosis when the Anvil
   surface cannot expose required status or logs. Record that missing capability
   as a product gap and return to managed commands for mutation.

## Qualify the candidate

1. Require recipe status and the candidate's native health endpoint to pass.
2. Run `anvil-serving eval preflight` against the exact direct endpoint/model,
   starting with thinking disabled and the smallest deterministic checks.
3. Expand to the profile's context, tools, streaming, modality, and concurrency
   gates only after smoke passes. Use the LLM qualification skill for capacity,
   repeated quality evidence, and publication.
4. Keep external recipe claims advisory. If the test came from a gist or
   runbook, use `workflow-intake:external-procedure-intake` when installed to
   retain its exact revision and the local adaptation matrix.

## Restore and report

Stop the candidate through its owning recipe/serve lifecycle, restore the exact
starting serve and router state, and prove the GPU returns to the recorded
baseline. Do not remove model caches or Docker volumes as cleanup.

Return the start-state fingerprint, credential presence boundary, GPU lane
classification, recipe/profile identity, preview and confirmation gates,
health and log cause, preflight/benchmark artifacts, restoration checks,
product gaps, and `promoted=false`. Promotion remains a separate human gate.
