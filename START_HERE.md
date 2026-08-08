# Start here: qualify the next internet model recipe

Use this guide when an operator brings a model-serving recipe from a post,
model card, issue, repository, video, or benchmark and wants it translated to
Anvil Serving. The external recipe is a research prior. It is not proof that the
model fits, runs correctly, performs well, or is safe to promote on this host.

## Before starting another campaign

Point-in-time promotion state, open defects, and carry-forward context are
recorded where they stay current, not inline in this durable guide:

1. The private operator repository (selected through `ANVIL_SERVING_HOME`)
   holds the active/promoted route assignments and live topology state.
2. `docs/findings/README.md` lists dated public findings chronologically, and
   the model dossiers under `docs/benchmarks/models/` carry each family's
   latest qualified evidence and remaining gates.
3. `.tickets/` records product defects; open tickets stay at the top level and
   resolved tickets move to `.tickets/closed/`. Read the open tickets before
   relying on a lifecycle path they touch.

Recorded evidence is never permission to assume current host state. Inspect
the live mode, serve owners, router identity, and shared memory
(`anvil-serving serves status`, `anvil-serving host shared-memory status`) at
the start of every session.

## Read before touching the GPUs

Read, in order:

1. `AGENTS.md`, `README.md`, and `CLAUDE.md`.
2. `skills/anvil-serving-llm-qualification/SKILL.md` and its evidence contract.
3. `skills/anvil-serving-benchmark-docs/SKILL.md` and its publication contract.
4. `skills/anvil-serving-kernel-tuning/SKILL.md` if logs report a missing
   hardware-specific MoE/GEMM configuration or a measured kernel bottleneck.
5. `docs/OPERATOR-PLAYBOOKS.md`, `docs/MODEL-LIFECYCLE.md`, and the dossier for
   the closest existing model family.

Use the repository skills as the controlling contract if this guide becomes
stale.

## Step 1: preserve the source before interpreting it

Capture the original URL and, when possible, a stable revision, archive, raw
text excerpt, or commit-specific link. Record:

- publisher and publication or observation date;
- age class: current (0-60 days), aging (61-120), or stale;
- evidence class: official, benchmark, community recipe, issue, or discussion;
- exact checkpoint, quantization, engine, image, CUDA, flags, context, batch,
  topology, and claimed metrics;
- hardware/runtime similarity and every translation the local run will need;
- which decision the source changes, if any.

Research current official model and engine sources before running anything.
Community posts and model cards guide experiments; only retained local evidence
can qualify this hardware.

## Step 2: translate to this host explicitly

The reference host has two equivalent 96 GB RTX PRO 6000 Blackwell Max-Q GPUs
over PCIe without NVLink. VRAM is sharded TP capacity, not unified memory. The
product supports two modes:

- `split`: UUID-backed `dark-compute-a` and `dark-compute-b` admit compatible
  independent workloads;
- `dual-gpu-exclusive`: one declared TP=2 serve owns both cards and all other
  GPU inference is offline.

Do not translate these roles back into Fast/Heavy or primary/auxiliary physical
cards. vLLM and community recipes often omit the RTX PRO 6000 and SM120, so
expect to translate recipes written for B200, GB300, DGX Spark, native Linux,
NVLink, or a different GPU count. Treat their flags and memory estimates as
priors until local startup logs and measurements confirm them.

Write a translation table before the first pull or load:

| Upstream assumption | Local value | Evidence or change required |
|---|---|---|
| Checkpoint and revision | exact 40-character commit | Pin model and tokenizer. |
| Image and engine | tag, digest, engine commit | Never silently retag or substitute. |
| GPU/topology | SM120, two cards, PCIe, TP size | Record NCCL/P2P behavior. |
| Quantization and KV | exact weight/KV formats | Confirm from startup logs. |
| Context/concurrency | declared ladder | Reserve output and reasoning headroom. |
| Parsers and tools | exact reasoning/tool parsers | Gate visible answers and tool validity. |
| Speculation | exact method and depth | Require a matched no-spec control. |
| CPU/KV offload | size, backing, mmap policy | Inspect ownership and cleanup. |

## Step 3: isolate the work and snapshot the starting state

Create a clean `codex/` worktree from current `origin/main`; never build a new
campaign in a stale or dirty operator checkout. Record branch, HEAD, dirty
state, installed CLI identity, cache inventory, live serves, mode, reservations,
router status, GPUs, and shared memory before mutation.

Representative read-only commands are:

```powershell
git fetch origin main
git worktree add -b codex/<recipe-slug> C:\path\to\anvil-serving-wt-<recipe-slug> origin/main
anvil-serving serves status --manifest <serves.toml>
anvil-serving serves mode status --manifest <serves.toml>
anvil-serving host gpus
anvil-serving host shared-memory status
anvil-serving models cache inventory --output <before-inventory.json>
```

Prefer the controller/MCP surfaces from the workbench skill when they are
available. If they are unavailable, state that controller coverage is missing
and verify that the CLI resolves to the intended checkout or installed version.

## Step 4: make the candidate reproducible before loading it

Create or update a serve recipe containing the exact source identity, image
digest, engine revision, served model name, GPU roles, TP/EP, quantization, KV,
context, concurrency, parsers, speculation, environment controls, mounts,
readiness contract, and source links. Never put credentials or host-specific GPU
UUIDs in published files.

Gate storage before pulling. Remove only an exact repository revision after a
reviewed dry-run; never broad-prune Docker or delete model volumes. Pull one
exact revision with the managed command and check whether its downloader is
still active before retrying a timed-out client.

```powershell
anvil-serving models recipes show <model> --registry <registry.toml>
anvil-serving models pull <repo-id> --dry-run
anvil-serving models pull <repo-id> --confirm
anvil-serving models recipes load <model> --container <name> --registry <registry.toml> --dry-run
anvil-serving models recipes load <model> --container <name> --registry <registry.toml> --confirm
```

Use `models recipes status`, `models recipes logs`, and `models recipes unload`
for the lifecycle. Raw Docker is read-only diagnosis only when the product
surface is broken; create or update a ticket for that gap and return to the
managed lifecycle.

## Step 5: diagnose before changing variables

On failure, preserve the earliest actionable error and follow it down-stack:
caller, router, managed status/logs, container exit/health, then engine or model
download. Distinguish authentication, license/authorization, dependency,
engine/model incompatibility, resource exhaustion, kernel problems, parser
problems, and application-quality failures.

Change one material variable at a time. Record the failure and fix in `.tickets/`
before continuing. Add a regression test or durable CLI/controller behavior
when the failure exposes a product lifecycle gap. Do not bypass a broken
production path with an undocumented shell command.

## Step 6: run the evidence ladder

Use thinking-disabled functional gates first unless the model requires
reasoning. For models with reasoning controls, test every supported level with
separate visible-output and reasoning budgets. A successful HTTP response with
reasoning but no visible answer is a failed attempt.

At minimum, retain:

1. smoke, structured JSON, tool calling, malformed-input, and recovery checks;
2. coding/terminal and multi-step tool tasks for local-agent candidates;
3. context ladders with memory use, time to first output/reasoning, first-visible
   TTFT, queue-inclusive effective prefill, generation/decode rate, E2E, usage,
   finish reason, and visible/reasoning output;
4. declared concurrency points and per-card VRAM reserve;
5. speculative-versus-control A/B data when speculation is proposed;
6. CPU/GPU offload counters and exact replay evidence when offload is claimed;
7. direct endpoint evidence before any router or Pi/OpenClaw integration.

Pass requires exact identity, 100% deterministic assertions, visible answers,
allowed finish reasons, valid tool calls, and no OOM, malformed response, parser
corruption, or unexplained request loss. Never use the candidate model to judge
its own output.

## Step 7: restore first, then publish

Unload the candidate through the managed recipe, reclaim WSL memory only through
the Anvil lifecycle, and restore the exact starting split/exclusive mode and
serve owners. Verify live state, shared memory, and cache after restoration.

Publish every meaningful success or failure as one transaction:

- immutable dated finding plus raw evidence links;
- `docs/findings/README.md`;
- `docs/benchmarks/runs.md`;
- the model dossier;
- the measured hardware page;
- `docs/BENCHMARKS.md` or the portal only when guidance changes.

Run the benchmark-publication validation matrix, Ruff, and the full pytest
suite. Documentation, qualification, cache presence, or a healthy candidate do
not authorize promotion. Finish with `no-promotion` unless the operator gives a
separate explicit promotion instruction after reviewing the evidence.

## Known-good reference implementations

- [DeepSeek 0731 r16 DSpark qualification](docs/findings/2026-08-01-deepseek-v4-flash-0731-r16-dspark-qualification.md)
- [DeepSeek 0731 native KV offload and 256K qualification](docs/findings/2026-08-02-deepseek-v4-flash-0731-native-kv-offload-256k.md)
- [DeepSeek 0731 650K Primary promotion](docs/findings/2026-08-02-deepseek-v4-flash-0731-primary-promotion.md)
- [DeepSeek model dossier](docs/benchmarks/models/deepseek-v4-flash.md)
- [Dual-PRO TP=2 campaign](docs/findings/2026-08-01-dual-pro-tp2-model-campaign.md)
- [Managed model lifecycle](docs/MODEL-LIFECYCLE.md)
- [Operator playbooks](docs/OPERATOR-PLAYBOOKS.md)

## End-of-session handoff checklist

Before context is cleared, save a project handoff containing:

- exact worktree, branch, HEAD, dirty state, PR, and CI status;
- current live mode, serve owners, router state, cache/downloader state, and
  `/dev/shm` status;
- exact model/image/engine identities and the last managed command;
- completed gates and direct links to raw artifacts;
- failures, tickets, and the next one to three executable actions;
- explicit promotion state and whether Pi/OpenClaw was intentionally untouched.
