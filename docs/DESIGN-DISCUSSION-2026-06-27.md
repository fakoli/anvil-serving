# Product-design discussion — where anvil-serving fits (2026-06-27)

A faithful account of a working session on what anvil-serving *is*, what it should
**not** become, and how it relates to its sibling project [Anvil](https://github.com/fakoli/anvil).
Private repo, so this is candid. The session reversed direction several times; the
reversals are the useful part, so they are recorded as back-and-forth, not as a clean
after-the-fact narrative.

**TL;DR of where it landed:** anvil-serving is the **supply-side tuner** for a local
serving tier, nothing more. Anvil owns routing, model tiers, cost framing, and
outcome-measurement. The whole question of whether anvil-serving should grow past a
personal tool is **gated on a bake-off** (see [BAKE-OFF-RUNBOOK.md](../examples/fakoli-dark/BAKE-OFF-RUNBOOK.md)).
Hardware is owned; that changes the *supply* side, not the *demand* question.

---

## How it started

The session opened as a mundane "review the handoff doc, what are we working on here."
The handoff framed anvil-serving as a five-capability tool (profile / models sync /
deploy / preflight / benchmark) with a P0 refactor (`anvil_core`) queued. The brainstorm
that followed never touched that refactor, because a prior question surfaced: *does the
thing we are about to build the right thing?*

---

## The back-and-forth (the reframes)

### 1. Three products in a trenchcoat

**Position (Sekou):** the goal is to optimize multi-agent workflows so one person builds
software at a rapid rate, then transfer that success to others, against a backdrop of a
degrading frontier ("the tokens we get back as individuals are becoming less and less
performant", rising cost, censorship, security fear).

**Pushback:** that is three different products wearing one coat — (a) a personal
throughput *amplifier*, (b) a *transfer* mechanism for others, (c) a *sovereign substrate*
(local serving). anvil-serving as it exists is (c), but the stated primary goal is (a).
Building the substrate (c) for a workload (a) that is not pinned down is the engine of
"redoing": every workflow change invalidates substrate work. You are building the power
plant before you know what you are plugging into it.

**Where it landed:** the spine is throughput/orchestration; serving is one input to it,
not the product.

### 2. The transfer switch was already built (Anvil's E13 fleet thesis)

We reframed local-vs-frontier as **grid-tie with a transfer switch**: do not go off-grid;
fall to your own generator when the grid is expensive, down, or censoring. Then we pulled
the Anvil repo and found Sekou had **already written exactly this**, six days earlier, as
epic **E13 / the "fleet thesis"**: capacity-bound (not per-token-cost-bound), drain
several flat-rate pools in parallel, route overflow to a zero-marginal local box. Plus a
full research doc (`docs/research/2026-06-20-agent-fleet-pull-market-landscape.md`) that is
effectively anvil-serving's business case.

**Where it landed:** the orchestration spine is not hypothetical — Anvil is v0.3, ~40
prior releases, 2,200+ tests, published. The "should I build orchestration" fork was
already resolved by Sekou's own shipped work. Anvil even *refuses* to be a model router
("it never selects or names a model"), leaving a deliberate supply-side vacuum.

### 3. The real problem is the undrawn boundary, not the vision

**Reframe:** the worry was "I'm scattered, I need direction / less redoing." The research
said the opposite — the vision is unusually coherent. The actual waste is that **two repos
are independently re-growing the same organs**: model-tier vocabulary, cost/capacity
framing, and a benchmark/bake-off harness all exist (or are specced) in *both* Anvil and
anvil-serving. (Tell: the repos had even drifted on the reference hardware — Anvil's docs
said "RTX 5090, 200 tok/s", anvil-serving said "96GB RTX PRO 6000, qwen35-awq".)

**Where it landed:** "less redoing" is not more building — it is **drawing one boundary
line** and shrinking anvil-serving to the supply-side tuner. See the boundary table below.

### 4. Continuity got receipts: the Fable/Mythos ban

Mid-session we verified, against real sources, that on **2026-06-12** a US export-control
directive led Anthropic to disable its two most capable models (Fable 5 / Mythos 5) for
**all** customers overnight, no restoration date; and that GPT-5.6 shipped the same week
gated to trusted partners first.

**Pushback (important):** this validates *provider/continuity risk*, but the obvious lesson
is the wrong one. The ban hit the *most capable* models; the pragmatic first answer to "my
provider went dark" is **multi-provider failover** — which Anvil already has (Anthropic /
Bedrock / custom OpenAI-compatible). Local is the *floor you control*, not a frontier
replacement. Confusing the floor for the ceiling is how you justify a wrong build with a
real headline. Also flagged: the ban's trigger was *coding capability*, so a strong local
coder is exactly the capability regulators are watching — frame the floor as continuity,
not evasion ("function within the boundaries of what's allowed").

**Where it landed:** continuity is a **layered stack** (see below). Anvil's
provider-neutrality became the timely headline feature, not anvil-serving.

### 5. "I already have the hardware" — supply vs demand

**Position (Sekou):** it is not a future purchase; Fakoli-Dark is built and paid for, so
the math changes.

**Pushback (the line that held):** ownership changes the **supply** side, not the
**demand** question. The capital is sunk either way. The open question was never "can I
afford a local box" — it is "does a local model do the routed-eligible work at an
acceptable false-pass / rework rate?" That is **unchanged** by ownership; it just got
cheaper and more urgent to answer. Do not let "I own it" become "therefore it's good
enough."

### 6. The hardware IS the architecture

The two asymmetric cards are not a compromise; they are a **two-tier local fleet** that
maps onto Anvil's opus/sonnet/haiku routing:

- **RTX PRO 6000, 96GB** → heavy local tier (Sonnet-equivalent): big KV, long-context
  agentic work; `qwen35-awq-local` is live on it.
- **RTX 5090, 32GB** → fast local tier (Haiku-equivalent): the "fast model fed a tight
  packet" runner. (Currently idle / free for gaming — the one real build the runbook adds.)

Engineering facts that shape the build: **do not pool the cards** (asymmetric VRAM, PCIe
x8, no NVLink → run one model per card, independent roles); **stagger the loads**
(`--weight-loader-disable-mmap` doubles RAM spike); power is fine under the 1600W PSU. This
also resolved the "drift" from §3 — both numbers were true; it is one box, two cards, each
repo had only seen one.

### 7. The supply-led trap: "monetize the capacity" is the wrong frame

**Position (Sekou):** the box "needs to pay for itself in usefulness" — convert idle token
capacity into cash flow, more room at work, or a community that becomes a product. "Plenty
of people are in this very same boat" (bought local hardware on the bet that local keeps
improving and the frontier keeps gating).

**Pushback (the hardest of the session):** "monetize the capacity" is *supply-led*
thinking — reasoning from the asset you own toward a use — which is how products die.
Selling raw tokens is a commodity race Sekou is the worst-placed person to win (datacenters
beat a tower on $/token). The scarce asset was never the silicon; it is the **knowledge of
how to make a local box do trustworthy agentic work**, wrapped in Anvil's **evidence gate**
(the thing that makes a small model's "done" actually mean done). The GPU is the commodity;
the verified record is the moat.

**Triage of the three monetization paths:**
1. **Job leverage** — do now; certain ROI; the path where the scarce assets (Anvil + the
   working fleet + the knowledge) create value, not the silicon.
2. **Community → product** — the real upside, but a *bet on a segment*. Validate cheaply
   (publish the honest account; see if "people in this boat" surface) before building.
   The transferable product is Anvil (no GPU), not anvil-serving.
3. **Sell tokens** — drop it.

### 8. The decision: prove it before you scale it

Same answer as Sekou's own June 20 red-team, sharper because the box is now real: do not
sell tokens, do not productize, do not build the fleet. Wire the live serve into Anvil as
one more bucket + failover, run the real backlog, and **measure**. The bake-off is finally
runnable instead of imagined, and that measurement gates everything downstream.

---

## Decisions

1. **anvil-serving = supply-side tuner only.** Given a model + a GPU, stand up the fastest
   *correct* SGLang/vLLM endpoint and hand Anvil a healthy `base_url`. ~1/3 the surface the
   current spec implies; Anvil already does the rest.
2. **The B50 bake-off gates the rest of anvil-serving.** No `analyze` / `tune` / refiner
   build, and no productization, until the bake-off shows the local box earns its keep. See
   [BAKE-OFF-RUNBOOK.md](../examples/fakoli-dark/BAKE-OFF-RUNBOOK.md).
3. **The P0 `anvil_core` refactor is deferred behind the bake-off**, not cancelled. The
   refiner agent would still need callable functions; just not yet.
4. **Continuity ships in tiers** (below). Tier 0 (Anvil provider failover) is free and
   immediate; Tier 1 (local floor) is gated on the bake-off.
5. **Don't pool the GPUs.** Two independent serving roles, one per card.
6. **Frame the local floor as continuity, not evasion** — stay inside export-control /
   dual-use boundaries.
7. **The transferable product is Anvil**, not anvil-serving. anvil-serving is personal
   infra until/unless the bake-off + a validated segment justify more.

---

## The boundary (who owns what)

| Capability | Owner | Notes |
|---|---|---|
| Task routing / eligibility / "what's safe to run local" | **Anvil** | `anvil next --max-blast --max-review-risk`; Anvil never names a model. |
| Model-tier vocabulary (opus/sonnet/haiku → local) | **Anvil** | `MODEL_TIERS`; anvil-serving should consume, not re-define. |
| Cost / capacity framing | **Anvil** | The fleet thesis lives there. |
| Outcome measurement (bake-off, accept-rate, false-pass) | **Anvil** | `benchmarks/bakeoff_snapshot.py`, `critic_falsepass.py`. |
| Evidence gate / trust layer | **Anvil** | The moat; makes local "done" mean done. |
| Provider failover (the integration socket) | **Anvil** | `custom` OpenAI-compatible provider. |
| Stand up + right-size the local serve (model/quant/KV/flags) | **anvil-serving** | The gotchas live here; this is the supply side. |
| Per-GPU serving config + health | **anvil-serving** | One model per card. |

---

## The continuity stack

| Tier | Buys | Lives in | Status |
|---|---|---|---|
| **Tier 0 — provider failover** | survive one provider/model going dark | Anvil (built) | ship/document now, ~free |
| **Tier 1 — sovereign local floor** | survive the metered/cloud frontier being unreliable/restricted | anvil-serving | gated on the bake-off |

---

## What gates what (open questions the bake-off answers)

- **Q1 — do the pools throttle enough? PARTLY ANSWERED (2026-06-27).** Yes on the
  *availability* axis: the runs logged API 529 overloads mid-flight that killed subagents and
  forced retries. Plus a throughput-equivalence: ~46.8M generated tokens over ~52 h cloud
  wall-clock would take ~65 h to emit locally at the measured 200 tok/s (less on the 96GB box,
  unbenchmarked). Capacity is throughput-feasible; quality is not yet proven. See
  [findings/2026-06-27-capacity-throughput.md](findings/2026-06-27-capacity-throughput.md).
- **Q1b — quota vs availability (NEW).** 529s prove availability failures; do the flat-rate
  *quotas* also cap during a mega-run? Measure 429/quota-exhaustion distinctly from 529s.
- Is the local model's "done" actually done, and does a tighter packet move that number?
- Does naive spillover suffice, or is a capacity-pool concept (and the bigger anvil-serving
  build) justified by *measured* mis-routing?
- Is the "people in this boat" segment real? (Cheapest test = publishing the honest
  account, not building a product.)
- **Q7 — 96GB aggregate throughput (NEW).** Actual sustained aggregate tok/s under
  concurrent-agent load? The 200 tok/s anchor is the 5090; the 96GB box is unbenchmarked.
- **Q8 — local prefix-cache hit rate (NEW).** Cloud economics rest on ~95% of tokens being
  cheap cache reads; does the local serve's prefix cache hit comparably for long, varied agent
  contexts? Key local-economics variable.
- **Q9 — concurrency, not just throughput (NEW).** The cloud run compressed wall-clock with
  ~890 parallel agents. Can local batching match that concurrency, or does serial-ish local
  execution stretch wall-clock past usefulness even when total throughput suffices?

---

## Pointers

- Bake-off runbook: [examples/fakoli-dark/BAKE-OFF-RUNBOOK.md](../examples/fakoli-dark/BAKE-OFF-RUNBOOK.md)
- Sibling project (the spine): [github.com/fakoli/anvil](https://github.com/fakoli/anvil) — esp.
  `docs/_positioning.md` (fleet thesis), `docs/how-to/bake-off.md` (B50), `docs/llm-providers.md`
  (the `custom` socket).
- The public honest-journey writeup of this session: blog PR
  [fakoli/sekoudoumbouya#74](https://github.com/fakoli/sekoudoumbouya/pull/74)
  ("The Box Is Paid For. That Is Exactly the Trap.").
- Hardware: Fakoli-Dark — RTX PRO 6000 (96GB) + RTX 5090 (32GB), Ryzen 9800X3D, 96GB DDR5,
  1600W. One box, two tiers.
