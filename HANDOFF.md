# HANDOFF — anvil-serving

Resume packet for Claude Code. **Read in order:** this file → `CLAUDE.md` (architecture + gotchas) → `docs/DISCOVERY-AND-REFINEMENT.md` (the plan) → `specs/` (what to build).

## What this is
A pip-installable CLI + library that turns your coding-agent **usage** into a right-sized local **serving** setup: `profile` / `models sync` / `deploy` / `preflight` / `benchmark`. Stdlib-only. Born from a Cowork session that stood up a real SGLang serve and generalized it.

## State
- **v0 scaffold works** — `pip install -e .`; `anvil-serving --help`; `deploy` renders a correct compose; `models sync` builds a card catalog. Code = generalized copies of the original scripts (cli.py subprocess-invokes them).
- **Specced, not built:** `specs/SPEC-anvil-core.md` (refactor the scripts into importable functions) and `specs/SPEC-agent-refiner.md` (Claude Agent SDK outer loop). These are the next work.
- **Live reference** (the author's box, `examples/fakoli-dark/`): SGLang serving `qwen35-awq-local` (Qwen3.5-35B-A3B AWQ) on a 96GB RTX PRO 6000 at `http://localhost:30000/v1`, 128K ctx. A daily Cowork scheduled task (`model-card-sync-analyze`) refreshes the model catalog — the refiner agent will replace it.

## Do next (in order)
1. **P0 — implement `anvil_core`** from `specs/SPEC-anvil-core.md`. It's a refactor: lift `_aggregate_usage.py`/`_role_split.py`/`_sync.py`/`preflight.py`/`benchmark.py` into importable functions returning dicts; add `family`/`thinking`/`recipe`. Acceptance criteria are in the spec. Highest leverage — every frontend thins out after this.
2. **P1 — `analyze` subcommand** (deterministic baseline + optional ~15-line urllib LLM enrichment).
3. **P2 — `tune`** (preflight gate → benchmark → 3-step heuristic; no Optuna yet).
4. **P3 — refiner agent** (scaffold with `agent-sdk-dev:new-sdk-app`, verify with `agent-sdk-dev:agent-sdk-verifier-py`).

## Don't relearn (full detail in CLAUDE.md / examples/fakoli-dark/SETUP-STORY.md)
- GGUF ≠ SGLang (llama.cpp only). WSL2 load-OOM → raise `memory=` in `.wslconfig`. mmap-over-virtiofs is fatally slow → `--weight-loader-disable-mmap`. Qwen3.5 thinks by default → empty output unless `chat_template_kwargs:{enable_thinking:false}`; it's multimodal → `--language-only`. FP8-80B hangs on sm_120; 4-bit 35B-A3B works.

## Dev
`pip install -e .` · stdlib-only, no network except `models sync`/`analyze` (HF cards) · keep frontends thin, logic in `anvil_core` · mark deliberate shortcuts with `# ponytail:`.
