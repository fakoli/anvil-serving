# Fakoli-Dark bake-off runbook — does the local box earn its keep?

**This is the operational instantiation of Anvil's B50 bake-off** (`anvil/docs/how-to/bake-off.md`)
on *this* machine. It answers one question with data, not metaphor:

> When Anvil routes eligible work to the local GPUs instead of the frontier, is the
> output **accepted** (low false-pass / rework) and does it **relieve real capacity
> pressure** (throttling, spillover) — enough to justify the hardware and any further
> serving build?

Everything bigger in `anvil-serving` (the P0 `anvil_core` refactor, `analyze`, `tune`,
the refiner agent) is **gated on this**. Owning the hardware changes the *supply* side,
not the *demand* question. Run this first.

---

## The box, as two tiers

| Tier | GPU | Status | Role in Anvil |
|---|---|---|---|
| **Heavy local** (Sonnet-equiv) | RTX PRO 6000, 96GB (GPU 1) | **LIVE** — `qwen35-awq-local` @ `:30000`, 128K ctx | planner/critic backend **and** execution runner for long-context, eligible tasks |
| **Fast local** (Haiku-equiv) | RTX 5090, 32GB (GPU 0) | **NOT STOOD UP** (free for gaming) | cheap scorer/critic + bounded-packet execution runner, 200+ tok/s |

A local model plays **two distinct roles** — keep them separate when reading the numbers:

1. **Anvil's planning/critic backend** (`llm_provider: custom`) — the LLM that does *Anvil's
   own reasoning*: task generation, scoring, and **critic verdicts**. The bake-off's
   "false-pass rate" measures the local model *as a critic* (does it wave through bad diffs
   a Sonnet critic would catch?).
2. **An execution runner** — a coding agent (Claude Code / Codex / a `anvil next -q` loop)
   pointed at the local endpoint that *does the code*. This is the "spend the idle tokens on
   real work" throughput play. Anvil deliberately doesn't run this itself — it hands out
   model-neutral, risk-bounded packets via `anvil next`; the runner decides which local
   model executes.

---

## Phase 0 — wire the live 96GB serve into Anvil (today, ~30 min)

Proves the heavy tier is usable by Anvil **and** gives you the Tier-0 failover target (if a
cloud model goes dark — see the Fable/Mythos precedent — Anvil falls to this endpoint).

```bash
pip install 'anvil-state[custom]'        # adds the openai SDK for the /v1 path
```

```yaml
# .anvil/config.yaml  — point Anvil's planner/critic at the local heavy tier
llm_provider: custom
custom_base_url: http://localhost:30000/v1
llm_model: qwen35-awq-local              # pass-through verbatim; must match --served-model-name
# no api key needed — SGLang local serve is unauthenticated (provider defaults key to "EMPTY")
```

Smoke test against the seeded sample (no cloud, no API key):

```bash
anvil init --with-sample
anvil expand T001 --use-llm              # routes to qwen35-awq-local; expect valid subtasks
```

**Watch-item (CLAUDE.md gotcha #5, it bites HERE):** Qwen3.5 thinks by default. The serve
runs `--reasoning-parser qwen3`, so SGLang splits reasoning into `reasoning_content` and the
answer into `content` — content should be non-empty **as long as `max_tokens` is generous**
(Anvil's `CustomEndpointProvider` defaults to 4096, which is fine). If you see empty/truncated
plans, the thinking budget ate the answer: raise `max_tokens` or disable thinking at the serve.
Anvil's OpenAI path does **not** send `chat_template_kwargs`, so you can't disable thinking
per-call from the Anvil side — fix it at the serve if it recurs.

---

## Phase 1 — Day-0 sanity check (1 day): does anything even throttle?

Per B50: get ~80% of "pull → self-select → verify" for free before the full run.

- Drive the **real** backlog with `anvil next -q` (the loop seam), green CI as the gate, B48
  strict-evidence + signed proofs on.
- Count rate-limit/429s per flat-rate pool. **If the pools never throttle, the capacity
  premise is already in doubt** — note it and shorten the run. (This is the cheapest possible
  refutation of the whole sovereignty-for-capacity thesis; do not skip it.)

---

## Phase 2 — stand up the fast tier on the 5090 (~half day) — the one real build

The 5090 is idle. A small coder there is the Haiku-equivalent runner: fed a tight Anvil
packet, it skips exploration and executes bounded work fast.

```yaml
# examples/fakoli-dark/docker-compose.fast.yml  — fast tier on GPU 0 (RTX 5090 32GB)
services:
  sglang-fast:
    image: lmsysorg/sglang:latest
    container_name: sglang-fast
    restart: unless-stopped
    shm_size: "16g"
    ports: ["30001:30001"]
    volumes:
      - "C:/Users/sdoum/models/<fast-coder-awq>:/models/fast"
    deploy:
      resources: { reservations: { devices: [{ driver: nvidia, device_ids: ["0"], capabilities: [gpu] }] } }
    command: >
      python3 -m sglang.launch_server
      --model-path /models/fast
      --weight-loader-disable-mmap
      --kv-cache-dtype fp8_e5m2
      --context-length 65536
      --max-running-requests 8
      --mem-fraction-static 0.85
      --served-model-name fast-local
      --host 0.0.0.0 --port 30001
```

Decisions baked into the gotchas:
- **Model must NOT be GGUF** (gotcha #4 — SGLang can't serve GGUF). The user's best GGUF coders
  (Ornith-1.0, Qwen3-Coder-30B GGUF) are out; use an **AWQ / compressed-tensors / FP8** coder
  in the 14B class (~8–10GB) so 32GB leaves real KV headroom.
- **Stagger the load** vs the 96GB serve. Both use `--weight-loader-disable-mmap`, which loads
  full weights into RAM (gotcha #1); bringing both up at once doubles the RAM spike on the 96GB
  host. Start the heavy serve, let it settle, then start the fast serve.
- Power is fine: PRO 6000 Max-Q (~300W) + 5090 (~575W) + 9800X3D under the 1600W PSU.

Wire it as a second Anvil provider target (a cheaper tier for scoring/critic), or as the
execution endpoint for low-blast packets.

---

## Phase 3 — the two-week measure (daily)

**In-engine** — once a day, append to a log:

```bash
python anvil/benchmarks/bakeoff_snapshot.py <state_dir> >> bakeoff-log.jsonl
# captures: needs_review_depth (review debt), status counts,
#           per-runner accept-rate (is local work accepted or reworked?),
#           packet right-sizing savings (B51 as_routed_savings_pct)
```

**Out-of-engine** — by hand / from harness logs:

| Metric | How | The question it answers |
|---|---|---|
| Per-pool throttle frequency | count 429s per flat-rate pool/day | *does the capacity case even exist?* |
| Spillover frequency to local | how often a throttled cloud pool → work runs local | *does naive spillover suffice (no pool concept)?* |
| **Local false-pass + rework** | `python anvil/benchmarks/critic_falsepass.py` with the **local model as critic backend**, graded vs the Sonnet baseline (`anvil/docs/critic-false-pass-baseline.md`) | *the local-quality tax* |
| Review-minutes per task | wall-clock human review per accepted/rejected task | *does local create hidden review debt?* |
| Cloud-tokens before/after | same work, with vs without local spillover | *what does the box actually save?* |

---

## Phase 4 — decide (write it up in `anvil/docs/research/`)

Reuse B50's decision gate verbatim:

- Pools throttle **and** naive spillover suffices → keep the lean spillover model; **no pool
  concept, no big anvil-serving build.** The box earns its keep as a simple overflow target.
- Pools throttle **and** spillover mis-routes (measured) → *now* a capacity-pool concept (and
  the `anvil-serving` `analyze`/`tune` work) is justified — build the minimum the data demands.
- Pools rarely throttle → capacity premise is weak. **Refocus on packet quality + the trust
  layer (Anvil's evidence gate), not on the GPUs.** The box stays a personal-throughput tool.

Then, and only then, decide whether anvil-serving graduates from "my private power plant" to a
productized thing worth showing the people-in-the-same-boat.

---

## Appendix — Tier-0 failover (the continuity win, free today)

The Fable/Mythos export-control ban (2026-06-12) disabled Anthropic's two most capable models
for all customers overnight, no restoration date. The cheap insurance is **provider failover**,
which Anvil already supports: if the primary errors, point `.anvil/config.yaml` at
`custom_base_url: http://localhost:30000/v1`. Worth a deliberate drill: kill the primary
mid-loop, confirm `anvil next → claim → execute → evidence → apply` survives on the local serve.
This is useful whether or not the bake-off says scale up.
