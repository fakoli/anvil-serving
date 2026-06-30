> **ARCHIVED — pre-pivot design history.** This spec was written before the repo pivoted to the quality-gated router product. It describes a planned anvil-refiner agent that was never built; the router (`anvil_serving/router/`) is what shipped instead.

# SPEC: anvil refiner — Claude Agent SDK stub

**Status:** spec only — no code yet. **Depends on:** `SPEC-anvil-core.md`, `docs/DISCOVERY-AND-REFINEMENT.md`.
**One line:** the thinnest Claude Agent SDK app that turns "a new model appeared" into "an analyzed (and optionally tuned) serving config + a note," human-gated. The agent is the OUTER loop; `anvil_core`/CLI are its deterministic tools. It replaces the hand-written Cowork scheduled task.

## Shape (use what the SDK already gives you)
- **Agent model:** cloud Opus (judgment: card prose, source conflicts, failure diagnosis). `# ponytail: don't run a local 35B as the optimizer — research says it's weak at self-optimizing config.`
- **Tools = the anvil CLI**, exposed via the SDK's command tool. No custom tool wrappers. The agent literally runs `anvil-serving models sync`, `anvil-serving analyze <id>`, `anvil-serving deploy ...`, `anvil-serving preflight ...`, `anvil-serving benchmark ...`, plus file write. `# ponytail: CLI is the tool surface; build no plumbing.`
- **Trigger = the existing scheduled task** (fast loop). The slow "re-tune" loop is the *same agent* invoked weekly. No daemon, no MCP. `# ponytail: reuse the scheduler you have.`

## What it does (v1)
1. `anvil-serving models sync` → read `NEW_MODELS:`.
2. For each new **LLM** (skip embeddings/image): read its card + `anvil-serving analyze` (deterministic baseline) → write `model-library/notes/<id>.md` (the **analyze feature**: SGLang-loadable?, sampling presets, thinking on/off + how to disable, serving flags, fit). Stop here unless tuning was requested.
3. **(opt-in) Tune a candidate** — only when asked for a deployable config:
   - propose config from `analyze` (+ `recipe`); `anvil-serving deploy` it on the target GPU.
   - **correctness preflight FIRST** — must pass (golden prompts, thinking disabled). A garbled config is disqualified before any perf number. `# NOT lazy here — keep this gate.`
   - `anvil-serving benchmark` against the measured request distribution.
   - if an SLA gate (P99 TTFT/E2E, no-OOM) fails: adjust **one** knob heuristically (mem-fraction down, or context/max-running down) and re-run. **Max 3 iterations.** `# ponytail: 3-step single-knob walk; swap in Optuna×GuideLLM ONLY if this plateaus below SLA.`
   - validate the winner on a couple of held-out prompt shapes (guard vs benchmark-overfit); apply a conservatism margin on mem-fraction.
4. Write a **versioned** candidate (`examples/<host>/candidates/<id>-<ts>.compose.yml`) + the note, emit a one-line summary, and **STOP for human approval**. Never auto-promote. `# NOT lazy here — human gate.`

## Cuts (YAGNI, named)
- No Optuna / GuideLLM in v1 — `anvil-serving benchmark` + the 3-step heuristic is the loop. Add the Bayesian stack only when the heuristic demonstrably can't hit SLA.
- No MCP server, no daemon, no multi-model tuning matrix, no auto-promote.
- analyze uses the agent's own model (Opus) — no separate analysis model/endpoint.

## Build it with the SDK toolkit
Scaffold with the `agent-sdk-dev:new-sdk-app` skill (Python). The whole app is: a system prompt (below), an allowed-tools list, and the loop as prompt instructions. Run the `agent-sdk-dev:agent-sdk-verifier-py` agent after.

### System prompt (sketch)
```
You maintain a local LLM serving tier via the `anvil-serving` CLI. Goal: turn newly-downloaded
models into correct, well-tuned serving configs — without guessing.
TOOLS: the anvil-serving CLI (models sync / analyze / deploy / preflight / benchmark) + file write.
RULES:
- Always get the deterministic baseline from `anvil-serving analyze` before adding judgment.
- Thinking-default models: ALWAYS pass enable_thinking:false for preflight/benchmark or they return empty.
- Tuning loop: preflight (correctness) MUST pass before you benchmark. SLA gates are filters, not goals.
  Adjust ONE knob at a time, max 3 tries. Never ship the OOM-edge mem-fraction.
- Validate the winner on held-out prompts. Write a versioned candidate. STOP and ask a human before promoting.
Output: a serving note per new model + (if tuning) a candidate config + a one-line summary.
```

### Permissions
Allow: anvil CLI, read/write under the repo + `model-library/`. **Ask before:** `docker compose up/down` / container restarts. `# correctness/safety boundary — not lazy.`

## Acceptance
1. Given a new model in the cache, one agent run writes a correct `notes/<id>.md` (matches `docs/MODEL-SETTINGS-EXAMPLE.md` depth).
2. With `--tune`, it produces a preflight-passing, benchmarked candidate config, held-out-validated, left for approval — using only anvil CLI calls (no new infra).
3. Re-run finds no spurious "new" models (uses `models sync`'s `NEW_MODELS` state).
4. The hand-written scheduled task is retired in favor of this agent on the same trigger.
