# Local Serving Setup — Decision Log

Running record of the build decisions, locked one by one. Companion to `LOCAL-SERVING-STACK-BLUEPRINT.md`.

| # | Decision | Choice | Status | Date | Rationale / ref |
|---|---|---|---|---|---|
| 1 | **Harness** (main + workflow loops) | **OpenClaw** (gateway) running the **fakoli-claw crew** as the workflow loops | ✅ LOCKED | 2026-06-27 | Already your stack; native per-agent routing to SGLang/vLLM; leanest per-call context (you author each specialist prompt); cloud Opus stays orchestrator. Alternatives (OpenHands, Codex CLI) kept as turnkey-coder options via ACP. See `HARNESS-COMPARISON-2026.md`. |
| 2 | **SGLang deployment style** | **Docker Compose**, single instance pinned to the 96GB card (device 1), port 30000, `--enable-metrics`, FP8 KV + RadixAttention/HiCache, **on-demand toggle** + OpenClaw cloud fallback, Tailscale-reachable. **Phase 2:** add SGLang Model Gateway router + register 5090 worker; promote 96GB to always-on. | ✅ LOCKED | 2026-06-27 | Reuses your Docker/Tailscale/toggle/watchdog; reproducible in-repo; OpenClaw preflight+fallback removes the on-demand downside. |
| 3 | **Context window + model** | main=cloud/1M; local workflow **64K cap (stopgap)**; model **Qwen3.5-35B-A3B-AWQ (stopgap)** — final coder model OPEN (FP8-80B hangs on sm_120; best coders are GGUF→llama.cpp). | ◐ partial | 2026-06-27 | Overnight: FP8-80B hung (sm_120 MoE); WSL RAM OOM fixed (46→64GB). See `deploy/MORNING-REPORT.md`. |
| 4 | **GitHub project** | public repo, name TBD (workshop) | ⏳ | — | candidates: fakoli-serving / fakoli-forge / fakoli-hearth / fakoli-relay |
