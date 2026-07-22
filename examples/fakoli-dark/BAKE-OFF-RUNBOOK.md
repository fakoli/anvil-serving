# Historical Fakoli-Dark bake-off runbook — 2026-06-28

> **Historical record — do not execute as the current Fakoli Dark runbook.** The model IDs,
> topology, and planning recommendation below describe the 2026-06-28 bake-off. Current Heavy is
> `gpt-oss-puzzle-88b`; use `anvil-router.live.toml`, the current Compose files, and
> [`docs/BENCHMARKS.md`](../../docs/BENCHMARKS.md) for present-day configuration and qualification.

**This is the operational instantiation of Anvil's B50 bake-off** (`anvil/docs/how-to/bake-off.md`)
on *this* machine. It answers one question with data, not metaphor:

> When work is routed to the local GPUs instead of the frontier, is the output **accepted**
> (low false-pass / rework) and does it **relieve real capacity pressure** (throttling,
> spillover) — enough to justify the hardware and any further serving build?

Everything bigger in `anvil-serving` (the P0 `anvil_core` refactor, `analyze`, `tune`,
the refiner agent) is **gated on this**. Owning the hardware changes the *supply* side,
not the *demand* question. Run this first.

> ### Revised 2026-06-28 — what the discovery changed (read this first)
> Two measured public findings reshaped the goals below.
> **Read them before running:**
> - [`2026-06-28-anvil-integration-audit`](../../docs/findings/2026-06-28-anvil-integration-audit.md)
>   — Anvil is **not** an LLM gateway. It has a **single** `custom_base_url` that backs only its
>   *planning augmentation* (`plan/score/expand`); it ships **no router** and cannot do heavy-vs-fast
>   endpoint routing. The critic is a Claude Code subagent (its model comes from frontmatter /
>   `CLAUDE_CODE_SUBAGENT_MODEL`), **not** from `llm_provider: custom`.
> - [`2026-06-28-planning-capability-eval`](../../docs/findings/2026-06-28-planning-capability-eval.md)
>   — On Anvil's real PRD→tasks prompt, local quality is **~55–65% of frontier** (frontier 24.75/25,
>   fast 16.0, heavy 13.25), with the gap in **dependency/ordering reasoning**. Planning is also *free*
>   on the Claude subscription. So routing Anvil's planner/critic to local is a **quality downgrade
>   for no cost saving** — the decision at that time was **failover-only**, not a steady-state arm.
>
> **Net effect on the goals:**
> 1. The bake-off thesis narrows to **capacity-relief via the execution-runner role** (the high-volume
>    "spend idle tokens on bounded work" play), **not** local-as-planner/critic.
> 2. Heavy/fast role routing moves **out of Anvil and into a runtime proxy** (LiteLLM /
>    claude-code-router) in front of `:30000`/`:30001`. **T010 (router spec) is promoted from
>    deferred to core — it is the actual integration point.** T013 ("wire both tiers into Anvil")
>    is retired; only the single-endpoint *planning failover* wiring survives, in the Appendix.
> 3. Promotion of any work-class to local is gated by a **shadow-eval against a measured quality
>    bar** (the planning eval is the template), per work-class — never all-or-nothing.

---

## The box, as two tiers (historical deployed snapshot, 2026-06-28)

| Tier | GPU | Status | Role |
|---|---|---|---|
| **Heavy local** (Sonnet-equiv) | RTX PRO 6000, 96GB (GPU 1) | Observed live then — `qwen3-coder-local` (Qwen3-Coder-30B-A3B AWQ) @ SGLang `:30000`, 128K ctx | execution runner for long-context, eligible packets · Tier-0 planning failover |
| **Fast local** (Haiku-equiv) | RTX 5090, 32GB (GPU 0) | Observed live then — `gpt-oss-20b` (MXFP4) @ vLLM `:30001`, ~256 tok/s | execution runner for bounded, low-blast packets |

> Both serves are already up (`docker ps` → `sglang`, `vllm-gptoss`). The earlier runbook listed
> `qwen35-awq-local` and "fast tier not stood up" — both stale. The remaining real build is **not a
> serve, it's the router** (Phase 2 below).

A local model can play **two distinct roles** — keep them separate when reading the numbers:

1. **Anvil's planning-augmentation backend** (`llm_provider: custom`) — the LLM that does *Anvil's
   own* `plan` / `score` / `expand --use-llm` calls. This is the **only** thing `.anvil/config.yaml`
   routes, and it is a **single** endpoint. The eval shows local here is materially worse than the
   (free) subscription, so this role is **failover-only** — see the Appendix. **Note:** this does
   **not** route the critic. Anvil's critic is a Claude Code subagent; to put a local model in the
   critic seat you must point the *runtime* (not Anvil config) at the local endpoint.
2. **An execution runner** — a coding agent (Claude Code / Codex / an `anvil next -q` loop) pointed
   at a local endpoint that *does the code*. This is the "spend the idle tokens on real work"
   throughput play, and it is **the actual subject of this bake-off**. Anvil deliberately doesn't run
   this itself — it hands out model-neutral, risk-bounded packets via `anvil next`; the **runtime
   router** (Phase 2) decides which local tier executes which packet.

---

## Phase 1 — Day-0 capacity sanity check (1 day): does anything even throttle?

**This is now the first phase to run** (the old Phase 0 planner wiring is demoted to the Appendix).
Per B50: get the cheapest possible refutation of the whole thesis before any build.

- Drive the **real** backlog with `anvil next -q` (the loop seam), green CI as the gate, B48
  strict-evidence + signed proofs on.
- Count rate-limit/429s per flat-rate pool. **If the pools never throttle, the capacity premise is
  already in doubt** — note it and shorten the run. (Cheapest refutation of the sovereignty-for-
  capacity thesis; do not skip it.)

---

## Phase 2 — stand up the runtime router (the one real build) — T010

The serves exist; **routing across them does not.** Anvil cannot do it (single `custom_base_url`,
no router — see the integration audit), so the heavy-vs-fast / role / work-class routing lives in a
**proxy in the runtime**, in front of both endpoints, exposing one OpenAI-compatible base_url to the
execution runner.

- Use **LiteLLM** or a community router (`musistudio/claude-code-router`, noted in
  `anvil/docs/model-strategy.md`). Map **work-class → tier**:
  - bounded / low-blast / high-volume packet → **fast** (`gpt-oss-20b`, `:30001`)
  - long-context or eligible heavier packet → **heavy** (`qwen3-coder-30b`, `:30000`)
  - hard reasoning / architecture / critic → **stay on cloud** (do not route local)
- The runner (Claude Code / Codex / `anvil next -q`) points at the proxy's base_url, **not** at
  `:30000`/`:30001` directly and **not** at Anvil.
- Failover: proxy falls back fast→heavy→cloud (or cloud→local for the Tier-0 continuity case).

**Quality gate before any work-class is allowed onto local — shadow-eval (required):** replay real
packets of that class to the candidate tier, grade outputs against cloud on a held-out set (reuse the
harness in `docs/findings/eval-data/2026-06-28-planning-capability/`). A class is promoted to local
**only if** it clears the bar; otherwise it stays cloud. This is the same `preflight`/`benchmark`
correctness-gate philosophy — a measured promotion, not a config flip.

---

## Phase 3 — the two-week measure (daily)

**In-engine** — once a day, append to a log:

```bash
python anvil/benchmarks/bakeoff_snapshot.py <state_dir> >> bakeoff-log.jsonl
# captures: needs_review_depth (review debt), status counts,
#           per-runner accept-rate (is local work accepted or reworked?),
#           packet right-sizing savings (B51 as_routed_savings_pct)
```

**Out-of-engine** — by hand / from harness logs. Metrics are framed around the **execution-runner**
role (the bake-off's actual subject), per work-class:

| Metric | How | The question it answers |
|---|---|---|
| Per-pool throttle frequency | count 429s per flat-rate pool/day | *does the capacity case even exist?* |
| Spillover frequency to local | how often a throttled cloud pool → work runs on the local proxy | *does naive spillover suffice (no pool concept)?* |
| **Per-work-class accept + rework** | per-runner accept-rate from `bakeoff_snapshot.py`, sliced by the proxy's work-class routing | *the local-quality tax, where it actually lands* |
| Review-minutes per task | wall-clock human review per accepted/rejected task | *does local create hidden review debt?* |
| Cloud-tokens before/after | same work, with vs without local spillover | *what does the box actually save?* |

> **On "local as critic false-pass":** measuring it requires routing the *critic subagent* through
> the runtime to local (Anvil config can't do it — see audit). We already have a strong prior from
> the planning eval (local ≈55–65% of frontier on the same reasoning-shaped task); only run the
> dedicated critic-false-pass test if a work-class's accept-rate makes a local critic tempting.

---

## Phase 4 — decide, per work-class (write it up in `anvil/docs/research/`)

Reuse B50's decision gate, but **decide per work-class**, not globally:

- A class throttles **and** local clears its shadow-eval bar **and** spillover routes it correctly →
  **promote that class to local.** If naive spillover suffices across classes, keep the lean model:
  **no pool concept, no big anvil-serving build** — the box is an overflow target.
- A class throttles **and** spillover mis-routes (measured) → *now* a capacity-pool concept (and the
  `anvil-serving` `analyze`/`tune` work) is justified — build the minimum the data demands.
- Pools rarely throttle → capacity premise is weak. **Refocus on packet quality + the trust layer
  (Anvil's evidence gate), not the GPUs.** The box stays a personal-throughput tool.
- **Planning / critic are never auto-promoted on quality grounds** — the eval already shows the gap.
  They go local only as Tier-0 failover (Appendix), accepting the measured quality tax for continuity.

Then, and only then, decide whether anvil-serving graduates from "my private power plant" to a
productized thing worth showing the people-in-the-same-boat.

---

## Appendix — Tier-0 failover + the only surviving Anvil-config wiring (continuity, free today)

The Fable/Mythos export-control ban (2026-06-12) disabled Anthropic's two most capable models for all
customers overnight, no restoration date. The cheap insurance is **provider failover**. This is the
**one** place Anvil's `llm_provider: custom` wiring still earns its keep — for *continuity*, not
quality:

```yaml
# .anvil/config.yaml — Anvil's planning-augmentation FAILOVER target (single endpoint, NOT the fleet)
llm_provider: custom
custom_base_url: http://127.0.0.1:30000/v1   # HEAVY: qwen3-coder-local
llm_model: qwen3-coder-local                  # pass-through; must match --served-model-name
custom_api_key_env: ANVIL_LOCAL_KEY           # any non-empty value; local serve is unauthenticated
```

```bash
pip install 'anvil-state[custom]'             # adds the openai SDK for the /v1 path
anvil init --with-sample
anvil expand T001 --use-llm                   # routes to qwen3-coder-local; expect valid subtasks
```

- This routes **only** Anvil's own planning calls, and to a **single** endpoint. It does **not** route
  the fleet (that's the Phase 2 proxy) and does **not** route the critic (Claude Code frontmatter).
- **Drill it:** kill the primary mid-loop, confirm `anvil next → claim → execute → evidence → apply`
  survives on the local serve. Accept that planned output is lower-quality during failover (eval:
  ~55–65% of frontier) — it's continuity insurance, not a quality upgrade.
- **Gotcha (still relevant):** Qwen-family reasoning models split `reasoning_content` vs `content`;
  keep `max_tokens` generous (Anvil's `CustomEndpointProvider` defaults to 4096). Anvil's OpenAI path
  does not send `chat_template_kwargs`, so disable thinking at the serve if content comes back empty.

---

## Task deltas vs the original plan (for the tracker)

- **T010 (runtime router spec)** — promoted **deferred → core**; it is the integration point (Phase 2).
- **T013 (wire both tiers into Anvil)** — **retired.** Anvil can't hold two endpoints or route by role.
  Its only surviving piece is the single-endpoint planning *failover* (Appendix).
- **T014 (two-week measure)** — reframed to **per-work-class execution-runner** acceptance (Phase 3);
  local-as-critic is conditional, not a headline metric.
- **T015 (decide-gate)** — reframed to a **per-work-class** promotion decision (Phase 4).
