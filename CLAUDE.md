# anvil-serving — context for Claude Code

> Resume packet. This repo was extracted from a Cowork session that set up a local SGLang serving tier on `fakoli-dark` and then generalized the work into a reusable tool. Source project: `cowork-env/projects/claude-usage-analysis/`.

## What this is
A pip-installable CLI + library that turns a developer's coding-agent **usage** into a right-sized local **serving** setup. Five capabilities (see README): `profile`, `models sync`, `deploy`, `preflight`, `benchmark`. Stdlib-only by design.

## Architecture
```
anvil_serving/
  cli.py            dispatch (profile/models/deploy/preflight/benchmark)
  config.py         cross-platform auto-detect: Claude logs dir, HF cache roots, model dirs
  profile.py        -> _aggregate_usage.py + _role_split.py  (usage percentiles, role split)
  models.py         -> _sync.py  (scan caches, pull HF cards, extract serving facts, INDEX.md)
  deploy.py         render templates/docker-compose.yml.tmpl for a given gpu+model
  preflight.py      correctness gate vs an OpenAI-compatible endpoint
  benchmark.py      replay the measured request distribution
templates/  configs/  docs/  examples/fakoli-dark/
```
Generalization done: hardcoded user paths replaced by env/auto-detect — `ANVIL_CLAUDE_LOGS`, `ANVIL_HF_ROOTS`, `ANVIL_MODEL_DIRS`, `ANVIL_MODELS_OUT`.

## The hard-won gotchas (don't relearn these)
1. **WSL2 load OOM:** no `memory=` in `.wslconfig` → VM caps at ~50% host; `--weight-loader-disable-mmap` then loads the whole model into RAM → OOM-kill (`scheduler died, exit code -9`). Fix = raise WSL memory (we used 64 GB on a 96 GB host).
2. **mmap over virtiofs** (Windows bind mount → Linux container) is pathologically slow; disable it, but then watch RAM (see #1).
3. **FP8 80B (Qwen3-Coder-Next) hung** post-load on `lmsysorg/sglang:latest` sm_120; a 4-bit 35B-A3B (AWQ/compressed-tensors → Marlin) loaded fine and leaves far more KV.
4. **GGUF can't be served by SGLang** — only llama.cpp. The best local coders the user had (Ornith-1.0, Qwen3-Coder-30B) are GGUF.
5. **Qwen3.5 thinks by default** → empty content with small `max_tokens`. Disable via `chat_template_kwargs:{enable_thinking:false}`. It's also multimodal → serve with `--language-only`.

## Roadmap (TODO — finish the library here)
- [ ] Refactor `_aggregate_usage.py` / `_role_split.py` / `_sync.py` from shelled-out scripts into importable functions with clean APIs (cli.py currently subprocess-invokes them).
- [ ] `deploy` should optionally **launch** (`docker compose up -d`) + tail health, and emit per-card recommended sampling into the compose/notes (wire the model-card analysis the Cowork scheduled task does).
- [ ] Cross-platform `deploy`: today the GPU/`device_ids` block + bind-mount assume Docker+NVIDIA; add native + vLLM backends.
- [ ] `profile` should also print the sizing recommendation (context cap, concurrency, model tier) directly, not just JSON.
- [ ] Tests + CI; package on PyPI; turn `docs/` into a proper docs site.
- [ ] Optional: `analyze <model>` subcommand that LLM-summarizes a model card into serving settings (the scheduled-task behavior, as a one-shot).

## Run / dev
`pip install -e .` then `anvil-serving <cmd> --help`. No network needed except `models sync` (fetches HF cards).
