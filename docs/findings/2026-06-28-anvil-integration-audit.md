---
title: "anvil-serving → anvil integration audit (T013 premise check)"
date: 2026-06-28
status: decision-input
verdict: premise-partial
method: 7-agent understanding workflow (6 readers + adversarial premise verifier → synthesis) over fakoli/anvil @ fix/windows-fcntl-portability
question: "Should we really connect anvil-serving to anvil, and what is the correct .anvil/config.yaml shape for routing-by-role?"
source_revision: "fakoli/anvil-serving@21f9a81f9be98dab3be15b07395ab34749d852b6"
notes_mirror_revision: "fakoli/anvil-serving-notes@7b46ceb6ae62252f8f808f6c065706a24e7970bb"
source_sha256: "32372e834d804db2fcbd2442c0f58189ca85774a07919223c8265c1c485b07de"
public_copy_date: 2026-07-22
---

> **Provenance.** This report is the output of a multi-agent audit of the public `fakoli/anvil`
> codebase at `fb2447eef44f6cf9489a414f927aa779c16fa1ab` on branch
> `fix/windows-fcntl-portability`, fetched 2026-06-28. This public copy removes only the original
> machine-local checkout path and adds publication metadata; the source digest above identifies the
> pre-sanitization narrative, which remains recoverable from the public source revision above and
> was also preserved in the notes-repository mirror.
> Six independent readers mapped one facet each (config system, LLM invocation, agent/skill
> model selection, role/tier routing, integration surface) plus one adversarial verifier tasked
> with *refuting* the T013 premise; a synthesis agent reconciled them. All load-bearing claims
> carry `path:line` citations re-verified by direct grep. Workflow stats: 7 agents, ~509k tokens,
> 77 tool calls, ~3.4 min wall-clock. Run ID `wf_53639227-28b`.

> **Historical scope.** The citations and config conclusions below describe that pinned Anvil
> revision. They were not rerun against current Anvil for this publication. Current operators must
> verify the installed Anvil version and its public configuration reference before relying on an
> exact key or line number.

# Should anvil-serving connect to anvil? And what is the correct `.anvil/config.yaml` shape for role routing?

## 1. Bottom line up front

The T013 plan is built on a **half-correct premise**. The concrete, falsifiable parts that are TRUE:

- anvil reads a per-project `.anvil/config.yaml` (`bin/src/anvil/config.py:316`, state dir `.anvil` at `bin/src/anvil/cli/_helpers.py:27`).
- It has a real `llm_provider` key that accepts `custom` (`bin/src/anvil/config.py:77`), plus `custom_base_url` / `custom_api_key_env` (`config.py:112-113`).
- A fully-wired `CustomEndpointProvider` builds an OpenAI client with `base_url=` against any `/v1` endpoint (`bin/src/anvil/planning/llm.py:955-973`). So `llm_provider: custom` + `custom_base_url: http://127.0.0.1:30000/v1` is a valid, supported config for **one** endpoint.

The parts that REFUTE the plan as written:

- **There is no two-tier / heavy-vs-fast endpoint routing.** `Config` has exactly one `custom_base_url` field per process (`config.py:112`). You cannot express "`:30000` heavy, `:30001` fast." `llm_tier` (opus/sonnet/haiku) maps to **model-name strings** sent to that single base_url, never to a second endpoint (`planning/llm.py:221-225`).
- **anvil does not ship a router**, and dynamic difficulty-based escalation is explicitly deferred behind a future `llm_router:` key (`docs/model-strategy.md:49,55`).
- **The custom endpoint backs ONLY anvil's optional planning augmentation** (`plan/score/expand --use-llm` + the MCP planning tools), which is the *only place anvil talks to an LLM* (`planning/llm.py:3-6`). It does **not** route the coding agent's traffic — that is owned by the surrounding harness (Claude Code), not anvil (`docs/model-strategy.md:82`).

So: you *can* point anvil's planning LLM at the HEAVY local server. You *cannot* "wire both serving tiers into anvil" so the fleet uses the local servers — that requires work in the agent runtime, not anvil's config.

## 2. What anvil actually is + its real config schema

anvil is a **local-first project-state / agent-coordination engine**, exposed two equivalent ways: a CLI (`anvil`) and a FastMCP stdio server (`anvil-mcp`, 24 tools) (`README.md:24-28`, `AGENTS.md:13-20`). State lives in `.anvil/` SQLite resolved from `ANVIL_ROOT` else cwd. The **core coordination surface needs no API key** (`server.yaml:50-61`, `Dockerfile:40-42`). It is *not* purely a state server — it **optionally** consumes an LLM for planning augmentation, defaulting to the keyless Claude Agent SDK over the logged-in subscription (`docs/llm-providers.md:3-5`).

LLM-related keys in `.anvil/config.yaml` (all citations `bin/src/anvil/config.py`; build-site read at `:638-645`, template at `:985-1000`):

| Key | Type / values | Default | Meaning |
|---|---|---|---|
| `llm_provider` | `agent-sdk \| anthropic \| bedrock \| custom` | `agent-sdk` (when blank) | Which provider anvil's *own* augmentation calls use (`:77`) |
| `llm_fallback` | bool | `false` | If true, auto-detect provider from env when `llm_provider` blank (`:80`) |
| `llm_model` | str | none | Explicit model id; overrides tier (`:86-91`) |
| `llm_tier` | `opus \| sonnet \| haiku` | none → `sonnet` | Logical tier → model **name** via `MODEL_TIERS` (`:93-97`, `planning/llm.py:221-272`) |
| `bedrock_region` | str | none | Bedrock only (`:99`) |
| `bedrock_profile` | str | none | Bedrock only |
| `custom_base_url` | str | none | **The OpenAI-compatible `/v1` endpoint** — single value (`:112`) |
| `custom_api_key_env` | str | none | Name of env var holding the key (`:113`) |

There is **no** key literally named `base_url`, `provider`, `endpoint`, `api_base`, or `model`; the equivalents are `custom_base_url`, `llm_provider`, `llm_model`. Only `project_name` and `project_id` are required (`config.py:786-794`). A global layer merges underneath the project file (`ANVIL_GLOBAL_CONFIG` > `$XDG_CONFIG_HOME/anvil/config.yaml` > `~/.config/anvil/config.yaml`), project keys win (`config.py:357-457`).

Provider precedence (`planning/llm_planner.py:209-255`): explicit `llm_provider` wins; else default `agent-sdk`; only if `llm_fallback: true` does it consult env (`ANTHROPIC_API_KEY`→anthropic, `AWS_REGION`→bedrock, `CUSTOM_LLM_BASE_URL`→custom).

## 3. Does the T013 premise hold? (with citations)

**Partially — and as literally written, no.**

| T013 assumption | Verdict | Proof |
|---|---|---|
| anvil has `.anvil/config.yaml` | TRUE | `config.py:316`; state dir `cli/_helpers.py:27`; loaded `state_dir/"config.yaml"` |
| `llm_provider: custom` exists | TRUE | `config.py:77` |
| can point at a local `/v1` endpoint | TRUE (one) | `planning/llm.py:955-973`; `llm_planner.py:346-356`; `docs/llm-providers.md:143-179` |
| "both serving tiers" as two base_urls (`:30000` heavy, `:30001` fast) | **FALSE** | one `custom_base_url` field (`config.py:112`); tiers map to model names not endpoints (`planning/llm.py:221-225`) |
| wiring it routes the agent **fleet's** traffic to local servers | **FALSE** | custom endpoint backs only planning augmentation (`planning/llm.py:3-6`); host model is the harness's job (`docs/model-strategy.md:82`) |
| role-based routing is anvil's job | **FALSE (dynamic)** / static-only | no router shipped, deferred to future `llm_router:` (`docs/model-strategy.md:49,55`) |

No reader/verifier conflict here — all six facets agree, and the load-bearing claims (single `custom_base_url`, tier→model-name, "anvil does not ship its own router") were re-verified by direct grep of `config.py` and `model-strategy.md`. The most aggressive (adversarial) reading and the descriptive readings converge.

## 4. The correct place to wire local serving + concrete config

There are **two distinct "which model" axes**; do not conflate them.

### Axis A — anvil's own planning augmentation (the only thing `.anvil/config.yaml` controls)

If you want `anvil plan/score/expand --use-llm` and the MCP plan tool to run on your local HEAVY server instead of the Claude subscription, this is legitimate and supported — for **one** endpoint. Pick the HEAVY box (planning is bursty, low-concurrency, quality-sensitive; the 35s TTFT at 125k ctx is tolerable for a one-shot planning draft, far less so for interactive coding):

```yaml
# .anvil/config.yaml
project_name: my-project
project_id: 0199...            # required

llm_provider: custom
custom_base_url: http://127.0.0.1:30000/v1   # HEAVY: qwen3-coder-30b (SGLang)
llm_model: qwen3-coder-30b                    # model name the server advertises
custom_api_key_env: ANVIL_LOCAL_KEY           # name of an env var; value can be "EMPTY"
```

`custom_api_key_env` names an env var; local servers ignore auth so any non-empty value works (provider falls back to `"EMPTY"` sentinel, `planning/llm.py:955-973`). You **cannot** add the FAST `:30001` endpoint here — there is no second slot. At most you could set `llm_model` to a second model the *same* server hosts, but your two tiers are on two different servers/ports, so that does not help.

### Axis B — the coding-agent fleet's traffic (NOT anvil's config)

This is the bulk of the tokens and the actual point of standing up HEAVY+FAST. anvil never sees this traffic. The integration point is the **surrounding runtime**:

- **Claude Code**: redirect via `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` to an OpenAI-→Anthropic shim, or set `CLAUDE_CODE_SUBAGENT_MODEL` (`docs/model-strategy.md:32-39`). anvil's shipped agent `model:` frontmatter (planner=opus, critic=opus, …) only governs Claude Code subagent **tier**, not endpoint, and has no `provider`/`base_url` field (`agents/planner.md:1-17`).
- **A proxy is the clean answer for two tiers**: run LiteLLM (or a community router like `musistudio/claude-code-router`, noted at `docs/model-strategy.md:49`) in front of both servers, expose one base_url to the harness, and let the proxy map model-name → `:30000` vs `:30001`. That gives you the heavy/fast split that anvil structurally cannot.

## 5. Routing-by-role: is it even anvil's job?

**Not for serving endpoints.** anvil supports *static role→tier* mapping as a first-class concept (`docs/model-strategy.md:19-28`; per-agent frontmatter), but "tier" is an **Anthropic-namespace model-name** primitive (`opus/sonnet/haiku` → `claude-*` ids, `planning/llm.py:221-272`), not an endpoint selector. There is also a separate "fast-lane" that routes **work-packet shape** (lightweight vs full) by complexity/blast-radius score (`context/packets.py:132`, `packet_metrics.py:40`, config `fast_lane_*` at `config.py:284-285`) — but that routes *content*, never *which model/endpoint runs*.

A **dynamic heavy-vs-fast model router does not exist** and is explicitly deferred behind a future `llm_router:` key (`docs/model-strategy.md:49-55`). So role routing across your two local servers must live in the **agent runtime / proxy layer**, mapping role or task-difficulty → endpoint. anvil can *inform* that decision (it knows task complexity scores), but it does not and cannot *perform* the endpoint routing today.

## 6. Recommendation

**Connect — but narrowly and honestly, and do not pretend anvil is the router.**

1. **Optionally** point anvil's planning augmentation at the HEAVY server via the Axis A config above. Low risk, real value (keeps planning local), single endpoint — this is exactly what the custom provider is for. Gate behind `--use-llm`; the default keyless agent-sdk path is unaffected.
2. **Do the fleet routing in the runtime/proxy**, not in anvil. Stand up a LiteLLM/router proxy in front of `:30000` (heavy) and `:30001` (fast, gpt-oss-20b vLLM) with the Qwen3-14B as failover, expose one OpenAI-compatible base_url, and point Claude Code (or your custom client) at it. Map role/difficulty → tier in the proxy.
3. **Rewrite the T013 task** to drop "wire both tiers into anvil's config." Replace with: (a) one-line anvil planning opt-in to HEAVY; (b) a proxy-based two-tier router owned by the agent runtime; (c) a shadow-eval/failover policy (route a slice of real traffic to FAST, compare against HEAVY/cloud on a held-out set before promoting) — this matches anvil-serving's existing `preflight` correctness-gate + `benchmark` philosophy and keeps the risky cutover behind a measured gate rather than a config edit.

The original plan was based on a wrong assumption that anvil is an LLM gateway with per-role endpoint routing. It is a state/coordination engine that *happens* to make a few optional planning LLM calls through a single configurable endpoint. Treat that single hook as a nice-to-have, and build the real heavy/fast role routing where the traffic actually flows: the surrounding agent runtime.
