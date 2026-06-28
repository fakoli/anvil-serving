# SPEC: anvil_core

**Status:** spec only — no code yet. **Reference:** `docs/DISCOVERY-AND-REFINEMENT.md` (findings + verdict).
**One line:** factor the *already-working* bundled scripts into importable, deterministic, stdlib-only functions returning plain dicts. This is a **refactor, not new behavior** — the CLI and the agent both call it; nothing shells out anymore.

## Why it exists
Today `cli.py` subprocess-invokes `_aggregate_usage.py`, `_role_split.py`, `_sync.py`, `preflight.py`, `benchmark.py`. The agent (separate spec) needs the same logic as callable functions, not subprocesses. So: lift the logic into `anvil_core`, leave thin frontends on top. (git → libgit2 lesson.)

## Non-goals (cut on purpose)
- No classes/dataclasses where a dict works. No provider abstraction, no plugin system, no async, no DB, no new dependencies. `# ponytail: dicts in/out; add a schema only when a second consumer needs validation.`
- No LLM in core. The LLM `analyze --llm` enrichment is a ~15-line `urllib` POST in the CLI, optional, behind a flag — **not** in core.
- Don't reinvent serving recipes — `recipe_lookup()` just GETs `recipes.vllm.ai/<id>.json` and returns it (or None).

## Module map (stdlib only)
```
anvil_core/
  usage.py     profile(logs_dir=None) -> dict          # merge aggregate + role_split into ONE pass
  models.py    discover(roots=None) -> list[dict]
               fetch_card(repo) -> str | None          # urllib GET HF raw README
               analyze(model: dict, card: str|None=None) -> dict   # DETERMINISTIC serving facts
               recipe_lookup(repo) -> dict | None       # urllib GET recipes.vllm.ai/<id>.json
               write_index(models: list[dict], out: str) -> None
  deploy.py    render_compose(**knobs) -> str           # move existing render(), unchanged
  eval.py      preflight(base_url, model, **opts) -> dict   # RETURN result (today it prints)
               benchmark(base_url, model, **opts) -> dict   # RETURN metrics
  families.py  THINKING: dict                            # per-family reasoning toggle table
```

## Contracts (the parts an implementer must not guess)

### usage.profile(logs_dir=None) -> dict
`logs_dir` defaults to `$ANVIL_CLAUDE_LOGS` or `~/.claude/projects`. One walk over `*.jsonl`. Returns the existing shape, merged:
```python
{
  "window": {...}, "totals": {...}, "model_mix": {...},
  "context_per_call": {"p50":.., "p90":.., "p95":.., "p99":.., "max":..},
  "generation_per_call": {...}, "concurrency": {...}, "throughput": {...},
  "by_role": {"main": {...}, "subagent": {...}},          # from role_split
  "subagent_context_coverage_pct": {16384:.., 32768:.., 65536:.., 131072:.., 262144:..},
}
```
`# ponytail: one file walk feeds both aggregate + role-split; don't run two passes.`

### models.analyze(model, card=None) -> dict  (deterministic — NO LLM)
Pull only what's mechanically extractable (see findings §1):
```python
{
  "id": "owner/repo", "format": "safetensors|GGUF", "sglang_loadable": True,
  "sm120_caveat": "FP8-MoE hangs post-load on sm_120 (sglang#16816)" | None,  # str when a Blackwell quant+arch hazard applies, else null
  "context": 262144, "quant": "compressed-tensors", "quant_bits": 4,
  "sampling": {"temperature":1.0,"top_p":0.95,"top_k":20},  # from generation_config.json
  "family": "qwen3", "thinking_default": True,
  "thinking_disable": {"chat_template_kwargs": {"enable_thinking": False}},  # from families.THINKING
  "multimodal": True,                                       # -> implies --language-only for text serving
  "recipe": {...} | None,                                   # recipe_lookup(), best-effort
  "serving_flags": ["--reasoning-parser qwen3","--tool-call-parser qwen3_coder","--language-only",
                    "--kv-cache-dtype fp8_e5m2","--weight-loader-disable-mmap"],
}
```
Family detection = substring match on `architectures`/`model_type`/id against `families.THINKING` keys. Everything here is in the bundled `_sync.py` already except `family`/`thinking`/`recipe` — small additions.

### families.THINKING  (small hand table — the only "judgment" baked in deterministically)
```python
THINKING = {
  "qwen3":   {"default_on": True,  "disable": {"chat_template_kwargs": {"enable_thinking": False}},
              "reasoning_parser": "qwen3", "tool_call_parser": "qwen3_coder"},
  "gpt-oss": {"default_on": True,  "effort": ["low","medium","high"], "reasoning_parser": "gpt-oss"},  # not boolean
  "deepseek":{"default_on": True,  "reasoning_parser": "deepseek-r1"},   # toggle is engine flag, not kwarg
}
# ponytail: 3 families now; add a row when a model that isn't covered shows up. No registry, no plugin.
```

### eval.preflight / eval.benchmark
Same logic as the bundled scripts, but **return** a dict instead of printing; the CLI wrapper prints it. Preflight returns `{"passed": bool, "tests": [{"name","ok","detail"}]}`. Benchmark returns `{"ttft_p50","ttft_p95","e2e_p50","throughput_tok_s","prefix_cache_hit","n"}`. For thinking-default models, preflight/benchmark MUST send `analyze(...)["thinking_disable"]` params or they'll time out on empty content (the bug we hit).

### deploy.render_compose(**knobs) -> str
Move existing `deploy.render()` verbatim. Knobs already correct (mmap-off, fp8 KV, parsers, language-only, gpu pin, mem-fraction). No change.

## Frontends stay thin
- `anvil_serving/cli.py`: each subcommand = parse args → call one `anvil_core` function → print. Delete the subprocess plumbing.
- `analyze --llm` (optional): `anvil_core.models.analyze()` for the baseline, then ONE `urllib` POST to `$ANVIL_LLM_BASE_URL/chat/completions` with the card text + baseline + a JSON-schema request; validate the JSON; on any failure return the baseline. `# ponytail: ~15 lines, urllib, no openai SDK; add the SDK only if you need streaming/retries.`

## Acceptance
1. `from anvil_core import usage, models, deploy, eval, families` — all import, stdlib only, zero new deps.
2. Each function returns the documented dict; `python -m anvil_core.eval` / a `__main__` self-check asserts shapes on a fixture. `# ponytail: one assert-based self-check per module, no test framework.`
3. `anvil-serving <profile|models sync|deploy|preflight|benchmark>` behave as before (now via imports, not subprocess).
4. Net diff is mostly *moved* code; new code = `family`/`thinking`/`recipe` additions + the merge in `profile`.
