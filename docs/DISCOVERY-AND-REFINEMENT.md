# Model Discovery + Self-Refinement — design & architecture

**Question:** should anvil-serving's "model-card discovery + self-refinement" be a feature here, and can we build it on the Claude Agent SDK? **Answer:** yes to both — but split by *determinism and cadence*. Deterministic discovery/analysis = CLI subcommands on a shared core. The LLM-driven self-refinement loop = a **separate thin agent built on the Claude Agent SDK**, triggered by a scheduler. Don't bake the LLM or a scheduler into the CLI; don't build a daemon; skip MCP until a second consumer exists. (Grounded in 2026 research — sources at bottom.)

## 1. The load-bearing insight: most "discovery" is deterministic; the LLM is for judgment
| Need an LLM? | Field / task | Source |
|---|---|---|
| ❌ deterministic | recommended sampling (temp/top_p/top_k/min_p/penalties) | `generation_config.json` (de-facto standard; vLLM/SGLang honor it) |
| ❌ | context length, arch, MoE/active params | `config.json` (`max_position_embeddings`, `architectures`, `num_experts`) |
| ❌ | quant format/bits | `config.json.quantization_config.quant_method` (compressed-tensors/awq/gptq/fp8/modelopt) |
| ❌ | GGUF vs safetensors (SGLang-loadable?) | file scan / GGUF header metadata |
| ❌ | thinking *detection* + parser name | family + `chat_template` (`enable_thinking`); per-family lookup table |
| ❌ | curated launch recipe / per-hardware overrides | **vLLM Recipes** (`recipes.vllm.ai/models.json`) — aggregate, don't reinvent |
| ✅ LLM | free-text "Best Practices" prose (dual thinking/non-thinking param sets, max_tokens advice) | card README markdown (unstructured, inconsistent) |
| ✅ LLM | reconciling source disagreements (card box vs prose vs generation_config) | judgment |
| ✅ LLM | adapting recipes to non-standard hardware (no-NVLink dual-GPU, P2P-disabled) | the catalogs assume datacenter NVLink topologies |
| ✅ LLM | failure diagnosis (read an OOM/startup trace → adjust) + human-readable explanation | judgment |

**Thinking toggle is per-family** (encode as a table, not a boolean): Qwen3/3.5 `chat_template_kwargs:{enable_thinking:false}`; gpt-oss reasoning effort low/med/high; DeepSeek-R1 `--think`. 

## 2. Architecture — factor the core first (git → libgit2 lesson)
```
            anvil_core/   (stdlib-only, deterministic: discover() / analyze() / recipe_lookup()
   ┌──────────────────────┐  / render_deploy() / score()  — no LLM, no scheduler, pure functions)
   └──────────────────────┘
      ▲            ▲             ▲
   thin CLI    SDK refiner    (MCP server, deferred)
  argparse    Claude Agent SDK  add only when a 2nd agent/user needs anvil over a stateful boundary
```
Everything sits on one deterministic core. The CLI, the agent, and any future MCP server are thin frontends — swap transports later without touching the core.

## 3. CLI layer (deterministic + *optional* pluggable LLM)
- `anvil-serving models sync` / `discover` — already deterministic (the card catalog). Add **vLLM Recipes lookup** (fetch `recipes.vllm.ai/<id>.json`) to enrich rows with a curated launch command + variants.
- `anvil-serving analyze <model> [--llm]` — deterministic baseline always; `--llm` enriches via a **pluggable OpenAI-compatible endpoint** (3 settings, env-first): `ANVIL_LLM_BASE_URL` / `ANVIL_LLM_API_KEY` / `ANVIL_LLM_MODEL`. One trio = your local SGLang **or** cloud. Request **JSON-schema-constrained** output (Pydantic/`response_format`), validate locally, and **fall back to the deterministic baseline** on any failure (no key, no dep, bad JSON). Lazy-import the client; ship it as an extra (`pip install anvil-serving[llm]`) or hand-roll a `urllib` POST to stay stdlib-pure.
- **No off-the-shelf "card → serving settings via LLM" tool exists** — `analyze` fills a real gap.

## 4. Self-refinement layer — build it on the Claude Agent SDK (the part you asked about)
**Yes — this is the right tool for the OUTER loop.** The Claude Agent SDK gives you the agent loop + tool-use + reflection; point it at anvil-serving's deterministic commands as its tools. What today's Cowork scheduled task does by hand becomes a real, versioned agent.

**Critical split (from the research): the agent orchestrates; a deterministic optimizer does the numeric tuning.** Do NOT let the model "self-optimize" config values (small/local LLM-as-optimizer is proven weak; LLM-driven serving-config self-refinement is research-only). Use **Optuna (Bayesian) over a GuideLLM benchmark** for the actual search.

**The agent's job (Claude Agent SDK):**
1. Trigger (scheduler / new-model event) → `discover` + `analyze` (read card prose, classify family/quant, pull vLLM recipe).
2. **Seed the search space**: translate model+hardware into sane bounds for the optimizer (which knobs matter: `max-running-requests`, `mem-fraction-static` capped for the gaming rig, `context-length`, KV dtype) + SLA gates (P99 TTFT/E2E, no-OOM).
3. Call the deterministic inner loop as a tool:
   - **correctness preflight FIRST** (golden prompts; a fast-but-garbled config can never win) → then
   - **Optuna ↔ GuideLLM** benchmark-refine (~20-40 trials, multi-objective: max goodput / min P99, feasibility-filtered).
4. **Validate the winner on a held-out workload shape** (guard against benchmark-overfit), apply a conservatism margin.
5. Diagnose failures (read SGLang logs → tighten bounds → relaunch), write a versioned config + a human-readable note.
6. **Human-approval gate** before auto-promoting (the standard self-improving-agent guardrail).

**Two cadences (don't merge them):** a fast *task loop* (analyze/deploy a newly-found model) and a slow *improvement loop* (re-tune weekly / on driver change). The existing Cowork scheduled task is exactly the trigger substrate; it generalizes to cron/systemd.

**Guards against Goodhart/reward-hacking:** correctness-preflight-before-perf, held-out validation, multiple realistic traffic shapes (not one synthetic), hard SLA/no-OOM as *gates not objectives*, conservatism margin (never ship the OOM-edge mem-fraction — doubly important on a gaming machine), human approval.

## 5. Why not MCP / a daemon now
Single-user, local, deterministic → CLI wins (composable, cheap, agent-friendly). MCP adds a context tax + statefulness you don't need yet; add it only when a second agent/user must call anvil over a stateful boundary, *after* the core API stabilizes. A hand-rolled daemon buys nothing over the scheduler you already have until you hit overlap/precision/in-memory-state needs.

## 6. Build plan
- **P0 — factor `anvil_core`** (refactor the shelled-out scripts into importable, deterministic functions). Highest leverage; everything else is thin.
- **P1 — `analyze` subcommand**: deterministic baseline + vLLM-Recipes lookup + per-family thinking table + optional pluggable-LLM enrichment (schema-validated, graceful fallback).
- **P2 — inner tuning loop** (`anvil-serving tune`): correctness-preflight → Optuna×GuideLLM over the measured distribution → held-out validation → versioned config. Deterministic, no LLM.
- **P3 — Claude Agent SDK refiner**: the outer orchestrator wrapping P1+P2 as tools, two-cadence, human-gated, versioned. Replaces the hand-rolled scheduled task.
- **P4 (deferred)** — MCP frontend if/when a second consumer appears.

## Sources
- LLM-in-CLI / pluggable provider: simonw `llm` (extra-openai-models.yaml, schemas) https://llm.datasette.io/en/stable/other-models.html · OpenAI structured outputs https://developers.openai.com/api/docs/guides/structured-outputs · aider OpenAI-compat https://aider.chat/docs/llms/openai-compat.html
- Model→recipe: **vLLM Recipes** https://recipes.vllm.ai/ + JSON API https://recipes.vllm.ai/models.json · HF generation_config https://huggingface.co/docs/transformers/main_classes/text_generation · vLLM Unified-Parser RFC #32713 https://github.com/vllm-project/vllm/issues/32713
- Self-refine / eval loop: vLLM auto_tune https://github.com/vllm-project/vllm/blob/main/benchmarks/auto_tune/README.md · auto-tuning-vllm (vLLM+GuideLLM+Optuna) https://github.com/openshift-psap/auto-tuning-vllm · GuideLLM https://github.com/vllm-project/guidellm · ELMo-Tune-V2 https://arxiv.org/abs/2502.17606 · Optuna https://github.com/optuna/optuna
- Architecture: MCP vs CLI decision framework https://manveerc.substack.com/p/mcp-vs-cli-ai-agents · CLI-is-the-new-MCP https://oneuptime.com/blog/post/2026-02-03-cli-is-the-new-mcp/view · self-improving agents (two-loop) https://www.mindstudio.ai/blog/how-to-build-self-improving-ai-agents-scheduled-tasks · git→libgit2 https://www.edwardthomson.com/blog/libgit2-in-2024-the-past

### Changelog
- 2026-06-27 — Created from a 4-angle research pass. Verdict: deterministic core + CLI for discovery/analysis; Claude Agent SDK for the outer self-refinement loop; Optuna×GuideLLM for the deterministic inner tuning; no MCP/daemon yet.
