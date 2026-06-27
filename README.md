# anvil-serving

**Right-size and run a local LLM serving tier from your own coding-agent usage.**

You already generate the perfect spec for your local inference setup every day — it's sitting in your Claude Code / Codex logs. `anvil-serving` reads that usage, tells you how to size a local serve, generates a tuned SGLang deployment for *your* GPU and model, catalogs the models you have, and validates the result. The hard-won gotchas (the ones that cost a night of debugging) are baked in as defaults.

## What it does
```
anvil-serving profile      # parse ~/.claude logs -> usage baseline (context/gen/concurrency percentiles, role split)
anvil-serving models sync  # scan your HF caches -> card catalog + INDEX (GGUF vs safetensors, ctx, quant, license, thinking)
anvil-serving deploy       # render a tuned SGLang docker-compose for YOUR gpu + model
anvil-serving preflight    # correctness gate against any OpenAI-compatible endpoint (sm_120-aware)
anvil-serving benchmark    # replay YOUR measured request distribution (TTFT, throughput, prefix-cache hit)
```

## Install
```bash
pip install -e .          # stdlib-only; no required deps
anvil-serving --help
```

## Quickstart
```bash
# 1) understand your usage
anvil-serving profile --out-dir .

# 2) see what models you have and which a server can actually load
anvil-serving models sync --out ./model-library
#    -> ./model-library/INDEX.md  (the ✅/❌ "SGLang-loadable" column is the one that saves you)

# 3) generate a deployment for a local model on GPU 1
anvil-serving deploy --model /path/to/model-dir --gpu 1 --context 131072 --served-name local-specialist
docker compose -f docker-compose.yml up -d

# 4) validate + benchmark
anvil-serving preflight --base-url http://localhost:30000/v1 --model local-specialist --needle-ctx 60000
anvil-serving benchmark --base-url http://localhost:30000/v1 --model local-specialist --burst 20
```

## What's baked in (the knowledge, not just code)
- **Load-time OOM fix:** loading a model without mmap pulls it fully into RAM — on WSL2 the default ~50%-of-host cap OOM-kills the scheduler (SIGKILL/-9). Raise the WSL VM memory; the deploy uses `--weight-loader-disable-mmap` (fast sequential reads vs catastrophic mmap-over-virtiofs).
- **GGUF ≠ SGLang.** GGUF is llama.cpp-only; SGLang/vLLM need safetensors. The catalog flags this up front.
- **Thinking-by-default models** (Qwen3.5 etc.) return *empty* content with a small `max_tokens` — they spend the budget reasoning. Disable per request with `chat_template_kwargs:{enable_thinking:false}`, or give ≥4096 tokens. See `docs/MODEL-SETTINGS-EXAMPLE.md`.
- **GPU pinning** (`device_ids`) so one card serves while another stays free (gaming / second job).
- **Blackwell sm_120 caveats:** some FP8 MoE paths hang on load; AWQ/compressed-tensors via Marlin works; NVFP4 large-prefill is still rough. Pre-flight before you trust throughput.

## Worked example
`examples/fakoli-dark/` is a real instance: a 96 GB RTX PRO 6000 serving a 35B-A3B specialist, with the actual compose, the `.wslconfig` fix snapshot, the model index, and the full setup story (`SETUP-STORY.md`).

## Methodology docs
`docs/` — `BLUEPRINT.md` (how to size a serve from usage), `USAGE-BASELINE-METHOD.md`, `MODEL-LANDSCAPE.md`, `HARNESS-COMPARISON.md`, `MODEL-SETTINGS-EXAMPLE.md`.

MIT licensed.
