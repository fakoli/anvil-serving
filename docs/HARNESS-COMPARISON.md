# Agentic Coding Harness Comparison (2026) — for the Fakoli local-specialist stack

**Owner:** Sekou - **Date:** 2026-06-27 - **Decision:** which harness drives the **main (orchestrator)** and **workflow (specialist)** loops, optimizing for least context overhead + native local-model routing to SGLang.
**Method:** 4-way parallel deep research across ~20 harnesses (official docs/repos/2026 changelogs).

> **Key framing — it's two layers, not one.** Your "harness" is really (A) an **orchestration/gateway layer** (always-on, per-agent model routing, parallel lanes, cron) and (B) a **coding harness** that does the actual edits. **OpenClaw is layer A** (you already run it). The decision is whether the *workflow loops* run as **OpenClaw's own crew agents** (lowest overhead, already built) or an **external coding harness driven via ACP** (more turnkey coding ability). The table below scores both kinds.

## 2026 status flags (read first)
- **Roo Code — SHUT DOWN (May 15, 2026).** Don't build on it; its orchestrator/mode model lives on in **Kilo Code**.
- **Gemini CLI — being retired (June 18, 2026)** into **Antigravity CLI** (Go, reportedly closed-source, weak BYO-local). Migration risk unless on Code Assist Enterprise.
- **Renames:** `sst/opencode` → **`anomalyco/opencode`**; `block/goose` → **`aaif-goose/goose`** (Linux Foundation). Both are continuations.
- **Backend-locked (can't host your local specialists):** **Cursor**, **Amp**, **Devin** — agent execution runs on their cloud/managed models only.

---

## Master comparison
Legend: ✅ strong / native · ⚠️ partial / caveated · ❌ none. "Local→SGLang" = can point the agent at your self-hosted OpenAI-compatible endpoint.

| Harness | License | Local→SGLang | Per-role model routing | Parallel subagent fan-out | MCP | Headless / gateway / cron | Fit for your setup |
|---|---|---|---|---|---|---|---|
| **OpenClaw** ⭐ | OSS | ✅ names SGLang/vLLM | ✅ native per-agent registry (+fallbacks) | ✅ specialist lanes + `sessions_spawn` | ✅ (+A2A, ACP) | ✅✅ full gateway + cron + multi-channel | **Your orchestration/gateway layer — keep it** |
| **OpenHands** ⭐ | OSS (MIT) | ✅✅ docs SGLang/vLLM; recommends **Qwen3.6-35B-A3B** | ✅ controller vs delegated | ✅ TaskToolSet + parallel sandboxes | ✅ | ✅✅ headless + REST + K8s | **Top turnkey coding harness for local** |
| **Codex CLI** ⭐ | OSS (Rust CLI) | ✅ `model_providers` / `--oss` | ✅✅ per-agent TOML model+provider (cloud+local in one config) | ✅ subagents (max_threads 6) + CSV batch | ✅ | ✅ `codex exec`, SDK, app server | **Cleanest native cloud+local split** |
| **Qwen Code** | OSS | ✅✅ explicit vLLM/SGLang docs | ✅✅ cross-provider subagent `model:` | ✅ named + fork subagents, prompt-cache forks | ✅ | ✅ headless, SDKs, cron, chat channels | **Most local-model-friendly CLI** |
| **opencode** | OSS (MIT) | ✅ any OpenAI-compat | ✅✅ best-in-class per-agent | ✅ mature (Task tool, Explore/Scout) | ✅ (+LSP/ACP) | ✅ `opencode serve` + SDK; no cron | **Best single coding harness** (drive via ACP) |
| **Cline** | OSS (Apache) | ✅ any OpenAI-compat | ✅ team specialists; Plan/Act | ✅ teams + Kanban parallel + worktrees | ✅ | ✅ CLI/SDK headless + cron + channels | **Excellent headless multi-model** |
| **Forge / ForgeCode** | OSS (Rust) | ✅ OpenAI-compat adapters | ✅✅ per-agent provider+model | ✅✅ agent-as-tool recursion | ✅ | ⚠️ scriptable, no daemon | **Sleeper strong fit** |
| **Goose** | OSS (Apache) | ✅ OpenAI-compat | ✅ per-recipe `goose_model` | ✅ subagents / sub-recipes | ✅ 70+ ext | ✅ scheduler/cron; no chat gateway | Strong all-in-one |
| **Kilo Code** | OSS | ✅ names vLLM/SGLang | ✅ per-mode (extension); ⚠️ CLI = global model | ✅ Orchestrator mode (Roo's heir) | ✅ (+marketplace) | ✅ Kilo CLI 1.0 | Strong in VS Code; CLI per-mode pending |
| **Continue** | OSS (Apache) | ✅✅ dedicated vLLM provider | ✅✅ per-role `config.yaml` | ❌ no native fan-out | ✅ | ✅ `cn -p` headless | Great routing, **no orchestration** |
| **Claude Code** | Closed (npm) | ⚠️ via `/v1/messages` shim or LiteLLM | ✅ per-subagent (Claude aliases); cloud+local needs a gateway | ✅✅ teams, nested (depth 5), forks | ✅ inline per-subagent | ✅ `-p` headless + Agent SDK | Best orchestration; needs gateway for local |
| **Aider** | OSS (Apache) | ✅ OpenAI-compat base | ⚠️ architect/editor/weak (2-3 roles) | ❌ | ❌ native (RFC open) | ⚠️ scriptable only | Leaf 2-model specialist |
| **Crush** | Source-avail (FSL) | ✅ `openai-compat` | ⚠️ large/small split | ⚠️ depth-2, unstable | ✅ | ❌ | Leaf coder; license caveat |
| **Factory / Droid** | Closed SaaS (BYOK CLI) | ✅ `generic-chat-completion-api` | ✅✅ custom droids per-model | ✅ Mission Mode + worktrees | ✅ | ✅ `droid exec` (SaaS-tethered) | Best *closed* BYOK fit |
| **RA.Aid** | OSS | ✅ OpenAI-compat | ⚠️ staged research→plan→impl | ⚠️ staged, not parallel | ✅ | ⚠️ scriptable | Niche staged pipeline |
| **Tabby** | OSS (Apache) | ✅ self-hosted inference | n/a (not an agent) | ❌ (Agent in preview) | ⚠️ | ✅ server (completions/RAG) | Inference/RAG layer, not an orchestrator |
| **Gemini CLI → Antigravity** | OSS→closed | ❌ Gemini-centric (local only via MCP) | ⚠️ Gemini variants only | ✅ subagents (no recursion), A2A | ✅ | ✅ headless | Weak local + **retiring June 18** |
| **Cursor** | Closed | ❌ agent/Composer backend-locked | ❌ | ✅ cloud subagents + bg agents | ✅ | ⚠️ headless but cloud-locked | **Non-fit** for local specialists |
| **Amp** | Closed SaaS | ❌ managed-models-only | ❌ | ✅ subagents + Oracle | ✅ | ⚠️ `amp -x` (SaaS) | **Non-fit** (no local/BYO) |
| **Devin** | Closed SaaS | ❌ | ❌ | ✅ parallel Devins | ✅ | API/Slack | **Non-fit** (reference only) |

---

## Shortlist for your "cloud orchestrator + local SGLang specialists"
1. **OpenClaw (keep as layer A).** It already does what no coding-harness does alone: always-on gateway, per-agent routing to SGLang/vLLM, parallel specialist lanes, cron, multi-channel. Your fakoli-claw crew *is* the workflow loop, tier-routed to local. **Lowest context overhead** because you author each specialist's prompt — nothing forces a heavy harness preamble.
2. **OpenHands (layer B, if you want a turnkey coder).** Only harness whose docs explicitly cover SGLang/vLLM *and* recommend **Qwen3.6-35B-A3B** (your model). Headless + REST + K8s = always-on friendly. Strong if you want a battle-tested SWE agent over hand-rolled crew agents.
3. **Codex CLI / Qwen Code (layer B, native cloud+local).** Both let you declare cloud + local providers and assign a different model per agent in one config — the exact orchestrator-cloud/specialist-local split, no translation proxy. Codex = cleanest TOML; Qwen Code = most explicit local-endpoint docs + prompt-cache-sharing forks.
4. **opencode / Cline (layer B alternatives).** Top open coding harnesses with per-agent routing + parallel subagents; drive either under OpenClaw via ACP.

**Avoid as the specialist host:** Cursor, Amp, Devin (backend-locked), Roo Code (dead), Gemini/Antigravity (weak local + migrating).

## Recommendation
**Keep OpenClaw as the harness/gateway and run the workflow loops as your fakoli-claw crew agents** — it's the least new work, has native per-agent routing to your SGLang endpoint, and gives you the leanest per-call context (you control each specialist's prompt). If you'd rather not hand-maintain crew agents, the cleanest turnkey swap for the *coding* layer is **OpenHands** (documents your exact model) or **Codex CLI** (native per-agent cloud+local), driven by OpenClaw via ACP.

The one thing worth measuring before locking in: the **actual fixed context overhead** (system + tool-schema tokens) each option injects per specialist call — that's the prefill tax on 111K calls/month. I can measure OpenClaw-crew vs Codex vs OpenHands empirically if you want it hard-numbered.

## Sources
**Vendor CLIs:** Claude Code subagents/model-config https://code.claude.com/docs/en/sub-agents · Codex config/subagents https://developers.openai.com/codex/config-advanced https://developers.openai.com/codex/subagents · Gemini→Antigravity transition https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/ · Qwen Code model providers https://qwenlm.github.io/qwen-code-docs/en/users/configuration/model-providers/
**Open/local-first:** OpenClaw multi-agent + local models https://docs.openclaw.ai/concepts/multi-agent https://docs.openclaw.ai/gateway/local-models · Aider OpenAI-compat https://aider.chat/docs/llms/openai-compat.html · Goose (AAIF) https://github.com/aaif-goose/goose · opencode agents https://opencode.ai/docs/agents/ · Crush https://github.com/charmbracelet/crush
**IDE agents:** Cline https://github.com/cline/cline · Roo shutdown https://nerova.ai/news/roo-code-shutting-down-may-15-2026-what-users-should-do-next · Kilo OpenAI-compatible (vLLM/SGLang) https://kilo.ai/docs/ai-providers/openai-compatible · Cursor local-model limits https://cursor.com/docs/subagents · Continue vLLM provider https://docs.continue.dev/customize/model-providers/more/vllm
**Open platforms:** OpenHands LLMs (SGLang/vLLM, Qwen3.6-35B-A3B) https://docs.openhands.dev/openhands/usage/llms/llms · Amp manual https://ampcode.com/manual · Factory BYOK https://docs.factory.ai/cli/byok/overview · Forge agent config https://deepwiki.com/antinomyhq/forgecode/2.1-agent-and-workflow-configuration · Devin 2 https://cognition.com/blog/devin-2 · Tabby https://github.com/TabbyML/tabby

### Changelog
- **2026-06-27** — Created. 4-way parallel research across ~20 harnesses; master capability table scored for the cloud-orchestrator + local-specialist topology; flagged Roo (dead), Gemini CLI (retiring), and backend-locked non-fits.
