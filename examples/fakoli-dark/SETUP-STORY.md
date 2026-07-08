# SGLang Overnight Setup — Morning Report (2026-06-27, ~01:40)

> Historical setup story only. Do not use this as the current Fakoli Dark or
> OpenClaw wiring guide; current operations go through `anvil-serving serves`,
> `anvil-serving router`, and `anvil-serving harness sync openclaw`.

> ## ✅ UPDATE 10:47 — IT'S LIVE
> Docker came up (62.8 GB VM), the container loaded cleanly (no OOM — used ~42 GB RAM during load, which is exactly what the old 46 GB cap killed), and the endpoint is serving:
> `health=200`, `/v1/models` → **`qwen35-awq-local`** @ 64K ctx on the 96 GB card (~60 GB KV pool). A real completion returned correct code.
>
> **Two catches to know:**
> 1. **This model thinks compulsively** — with thinking ON (default) it burns the whole token budget reasoning and returns *empty* content. **Fix: send `chat_template_kwargs={"enable_thinking": false}`** (then it answered `sum(xs)` cleanly). Wire this into OpenClaw for the specialist tier, or it'll waste tokens and return blanks. (Note: `preflight.py`/`benchmark.py` will also need this flag against this model.)
> 2. **It's slow** — ~21 tok/s warm decode + high per-request latency. That's the AWQ-on-Blackwell + hybrid-GDN-Triton-kernel tax. Confirms the AWQ-35B is a **stopgap**; the final-model decision (an FP8 coder for SGLang, or running your GGUF coders — Ornith/Qwen3-Coder-30B — under llama.cpp) still stands.
>
> Cosmetic fix already applied to the compose: `--cuda-graph-max-bs` → `--cuda-graph-max-bs-decode` (your deprecation warning).


**Short version:** The deployment is **built, reproducible, and the hard problems are diagnosed + fixed.** The ONE thing left is mechanical: **Docker Desktop did not finish restarting on its own** after the WSL memory change (~9 min, processes up but engine not responding — it likely needs a single manual nudge). **Action for you:** open Docker Desktop (or quit + relaunch it once); when the whale icon is green, run `sglang-up.ps1`. With the new 64 GB WSL RAM the model should now load cleanly (no OOM). Everything else is staged. Finish steps at the bottom — ~5 minutes.

> **Why Docker needs a nudge:** applying the `.wslconfig` memory change required `wsl --shutdown` + a Docker restart. Docker Desktop's engine got stuck re-initializing unattended (no GUI to click). This is cosmetic, not a real failure — the config is correct and the WSL VM already rebooted at 62 GB.

---

## What's done and working
- **Docker Compose deployment** at `projects/claude-usage-analysis/deploy/` — `docker-compose.yml` + `sglang-up.ps1` / `sglang-down.ps1` + `README.md`. Container name is still `sglang`, so your `SGLang-OFF-Gaming.bat` / `sglang-mon.bat` keep working.
- **Image pulled:** `lmsysorg/sglang:latest` (43 GB).
- **GPU pinning works:** container loads on **GPU 1 = RTX PRO 6000 96 GB** (`avail mem 93.6 GB`); the 5090 stays free for gaming.
- **Your HF cache, no container volume:** confirmed working — SGLang found the model in your mounted cache ("skipping download"). Per your request, nothing is stored in a container volume.
- **Metrics on** (`--enable-metrics` → Prometheus `/metrics`).

## Two real problems I hit (and what I did)
1. **Qwen3-Coder-Next-FP8 (~75 GB) hangs post-load on this SGLang build.** It loads to GPU then freezes during MoE weight setup on sm_120 (no "Load weight end"). Also, at 75 GB it leaves only ~8 GB for KV — bad for your fan-out anyway. **Abandoned it.** (Matches the deep-research note that SGLang has sm_120 MoE rough edges.)
2. **OOM during load → SIGKILL restart loop.** Root cause: your **WSL2 VM was capped at ~46 GB RAM** (`.wslconfig` had no `memory=` and `swap=0`). Loading without mmap pulls the whole model into RAM and the OS OOM-killer fired (`scheduler died, exit code -9`). **Fixed:** raised WSL to **`memory=64GB` + `swap=16GB`** (backup at `.wslconfig.bak-presglang`; your custom ZFS kernel + `networkingMode=mirrored` preserved). Verified the VM now has **62 GB**.

## Current model (a stopgap — see decision needed)
I switched to **Qwen3.5-35B-A3B AWQ 4-bit** (copied from your WSL cache to `C:\Users\sdoum\models\qwen35-awq`, ~24 GB). Why: 35B-A3B at 4-bit leaves ~60 GB for KV → real concurrency for your fan-out, and it loads ~3× faster than the 75 GB FP8. **Caveats:** it's actually a *multimodal* Qwen3.5 (not a dedicated coder), and AWQ is slower on Blackwell than FP8/NVFP4. Treat it as "get something serving," not the final pick.

## Important finding about your cached models
Most of your good coders are **GGUF** (`Ornith-1.0-35B`, `Qwen3-Coder-30B`, `Qwen-AgentWorld`) — **GGUF is llama.cpp-only; SGLang can't load it.** For SGLang you need safetensors (FP8/AWQ/NVFP4). So **decision 3 (final model) is still open**, with three real paths:
- **(a)** Keep the AWQ-35B (serving now, multimodal, not coding-specialized).
- **(b)** Get a *safetensors* coder SGLang can load on sm_120 (e.g. an FP8/AWQ build of Qwen3-Coder-30B; the FP8 80B hangs, so avoid).
- **(c)** Run your GGUF coders (Ornith / Qwen3-Coder-30B) under **llama.cpp** instead of SGLang — you already have `llama.cpp` built, and Ornith is benchmarked on OpenClaw. This may be the better route given what you actually have cached.

## Finish steps (morning, ~5 min)
1. **Confirm Docker is up:** `docker version` (server line). If Docker Desktop is still "starting," click it / give it a minute; if needed, quit & relaunch it once.
2. **Start (or confirm) the serve:**
   `powershell -ExecutionPolicy Bypass -File C:\Users\sdoum\ai-code\cowork-env\projects\claude-usage-analysis\deploy\sglang-up.ps1`
   then watch: `docker logs -f sglang` until **"The server is fired up and ready to roll!"** (with 62 GB RAM it should load in ~1–2 min, no OOM).
3. **Health:** `curl http://127.0.0.1:30000/health` -> expect `200`; `curl http://127.0.0.1:30000/v1/models`.
4. **Validate + benchmark** (Python is on your PATH):
   `python C:\Users\sdoum\ai-code\cowork-env\projects\claude-usage-analysis\scripts\preflight.py --base-url http://127.0.0.1:30000/v1 --model qwen35-awq-local --needle-ctx 60000`
   `python C:\Users\sdoum\ai-code\cowork-env\projects\claude-usage-analysis\scripts\benchmark.py --base-url http://127.0.0.1:30000/v1 --model qwen35-awq-local --burst 20 --shared-prefix-tokens 8000 --ctx-tokens 32000`
5. **Wire OpenClaw** to `http://100.87.34.66:30000/v1` (model `qwen35-awq-local`) over Tailscale once you've picked the final model.

## If it still hangs after "using attn output gate!"
That was the OOM (now fixed). If it recurs with the 62 GB VM, lower `--mem-fraction-static` to 0.85 and keep `--weight-loader-disable-mmap`. If the *FP8-80B* style hang returns on a different MoE, that's the sm_120 issue — try the Blackwell image `lmsysorg/sglang:deepseek-v4-blackwell` or go the llama.cpp/GGUF route (path c).

## Decisions log
1. Harness = **OpenClaw** ✅  2. Deploy = **Docker Compose, single instance, 96 GB card, on-demand** ✅  3. Context/model = 64K cap + **AWQ-35B stopgap** (final model open — see above).
