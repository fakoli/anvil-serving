---
title: "Harness-grounded feasibility of model-name-as-intent routing"
date: 2026-06-29
status: research-result
question: "Can coding harnesses carry routing intent to anvil-serving, and is 'named presets in the model field' the right API surface?"
method: "5 web-researched harness-capability finders (Claude Code, OpenAI-compat clients, OpenAI/Anthropic wire schemas, gateway precedent, intent granularity) → adversarial verification of each load-bearing claim → synthesis. Workflow wf_9cb7f2d7-16f, 11 agents, ~537k tokens, 136 tool calls."
verdict: "confirm-with-refinement"
source_revision: "fakoli/anvil-serving@b0a68c64482774a719da76a62a745e095effda1e"
notes_mirror_revision: "fakoli/anvil-serving-notes@7b46ceb6ae62252f8f808f6c065706a24e7970bb"
source_sha256: "311fb3daa743ce149cde05882c4f32d4742956400a7d07c9fdaeef3682e5cbe8"
public_copy_date: 2026-07-22
---

> **Provenance.** Output of a multi-agent research workflow that web-searched official docs and
> adversarially verified each load-bearing claim. Citations are inline (official docs preferred).
> Low-confidence / version-dependent facts are flagged explicitly. This is the evidence base for
> the intent-addressing design in [`../QUALITY-GATED-ROUTER.md`](../QUALITY-GATED-ROUTER.md).

> **Historical scope.** This is the sanitized public copy of the 2026-06-29 research snapshot, not
> a claim that every third-party harness still exposes the same controls. The source digest and
> public source revision identify the original narrative. Current OpenClaw behavior is governed by the
> [public integration contract](../OPENCLAW-INTEGRATION-SPEC.md) and its executable validator.

## Headline

**"Named presets in the model field" is the correct, precedented compatibility floor — but it only
carries SESSION-coarse intent; finer per-request intent must be inferred, and Cursor/Amp/Devin
can't carry it at all.**

## Per-harness reality

| Harness | base_url override? | arbitrary model string sent verbatim? | settable extra_body / headers (per-request)? | intent "slots" per session | best carrier tier today |
|---|---|---|---|---|---|
| **Claude Code** | YES — `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_API_KEY` | YES — `ANTHROPIC_CUSTOM_MODEL_OPTION` skips client validation; `ANTHROPIC_MODEL`/`--model`/`ANTHROPIC_DEFAULT_*_MODEL` passed "as-is, not transformed" | NO — only fixed `metadata.user_id` (harness-set, opaque, no PII); no user-settable per-request metadata/headers | ~3–4: main, background/haiku (`ANTHROPIC_DEFAULT_HAIKU_MODEL`), subagent (`CLAUDE_CODE_SUBAGENT_MODEL`), advisor; `opusplan` auto plan→exec | **Tier 1** (model-name-as-intent) |
| **OpenAI Codex CLI** | YES — `config.toml`: `openai_base_url` or `[model_providers.<id>].base_url` + `model_provider` | YES — `model` is a free-form string, no whitelist | YES (strongest) — per-provider `http_headers`, `env_http_headers`, `query_params` (config-level static map, not varied per call) | 1 global `model` + per-subagent model | **Tier 1** (+ Tier 2 side-channel) |
| **Aider** | YES — `OPENAI_API_BASE` / `--openai-api-base` (LiteLLM-routed) | YES — `openai/<token>`; unknown ids emit a cosmetic "not familiar with" warning, still work | Partial — `extra_headers`/`extra_body` in `.aider.model.settings.yml` (per-model-config, not per-request) | ~3: main, `--editor-model`, `--weak-model` | **Tier 1** (needs `openai/` prefix) |
| **Cline** | YES — "OpenAI Compatible" free-text Base URL | YES — free-text "Model ID", no whitelist | Uncertain (not documented — treat as no) | ~1–2 (Plan/Act can bind different models) | **Tier 1** |
| **Continue.dev** | YES — `apiBase` per model | YES — free string with `provider: openai` (examples use non-OpenAI names) | YES — `requestOptions.headers` (per-model-config) | role-based: chat/edit/apply/autocomplete/embed | **Tier 1** (+ Tier 2 via headers) |
| **Cursor** | PARTIAL — "Override OpenAI Base URL" exists but requests route through Cursor's own backend; agent/Composer effectively backend-locked | SEMI — custom model names exist but pass a Verify gate + backend mediation; free-form intent tokens fragile (NOT confirmed to be vendor-prefix-validated — TensorZero ran an arbitrary id) | NO — not exposed for custom OpenAI models | Auto-mode varies model server-side, uncontrollable | **Tier 0 / unusable** for self-hosted routing |
| **Amp / Devin / closed SaaS agents** | NO — backend-locked, cannot be repointed at a custom endpoint for agent execution | n/a | n/a | n/a | **none — cannot reach anvil-serving** |

## Verdict on the choice: CONFIRM, with refinement

**Correct as the compatibility floor.** Across every harness that can be repointed at a custom
endpoint, the `model` string is the *only* operator-controllable routing channel that is (a) always
present (required in both wire schemas), (b) forwarded verbatim, and (c) free-form — neither the
OpenAI nor the Anthropic schema validates it against a closed enum; only the *genuine upstream*
rejects unknown names (OpenAI `404 model_not_found`, Anthropic `404 not_found_error`). A router
behind the base_url is free to reinterpret an arbitrary `model`/intent string. This is exactly how
shipping gateways behave — Cloudflare AI Gateway's `dynamic/<route-name>` syntax, LiteLLM's
arbitrary `model_name` alias, OpenRouter's slug variants (`:nitro`/`:floor`/`:exacto`,
`openrouter/auto`). The model string is a single flat token, so it must stay a **closed enum of
preset names**; multi-axis intent (model+budget+latency+verifier) does *not* belong in the string.

**Why "with refinement":**
1. **Granularity is SESSION-coarse, not per-request.** An unmodified harness pins the model across a
   small fixed set of slots per session (Claude Code ~3–4; Codex 1+subagent). It does **not** vary
   the model by work-class *within* the main loop. Finer per-request intent (plan vs edit vs review
   inside the main loop) is **not declarable and must be inferred**.
2. **Presets-only must be paired with a classifier (Tier 0) as the default path**, because most
   requests arrive on a single session model string with no declared intent. Named presets are the
   *declarative ceiling*, not the *operating mode* for most traffic.
3. **An optional side-channel (Tier 2) should be specified but not required**, exactly as
   OpenRouter/Portkey do — for harnesses that can carry it (Codex `http_headers`/`query_params`,
   Continue `requestOptions.headers`).

## The graceful-degradation tier model

| Tier | Mechanism | What it unlocks | Available on |
|---|---|---|---|
| **0 — Infer** | Router classifies work-class from raw payload (token count, `thinking` flag, tool types, image content, system-prompt fingerprint) | Per-request intent **with no caller cooperation** — the universal floor and default operating mode | every harness that reaches the endpoint |
| **1 — Model-name-as-intent (named presets)** | Caller/config puts a preset token in the `model` field; router maps preset → tier | Caller-declared **coarse** intent per session slot; first-class UI via `ANTHROPIC_CUSTOM_MODEL_OPTION` or `/v1/models` discovery | Claude Code, Codex, Aider (`openai/` prefix), Cline, Continue — **not** Cursor/Amp/Devin |
| **2 — extra_body / header dimensions** | Optional structured hints (budget, latency, verifier policy) in headers/query/body | Multi-axis intent beyond the flat string — but config-level, not per-request | Codex, Continue; Aider (config yaml). Not Claude Code, not Cursor |
| **3 — Native intent field** | A first-class request field for intent | True per-request multi-axis intent | **no harness, no schema today** — needs a standard/harness change |

claude-code-router is the production existence proof for Tier 0+1: it routes by inferring work-class
from request properties **and** honors an explicit `/model provider,model` override, both through an
unmodified Claude Code.

## What is BLOCKED by lack of harness support

1. **Per-request intent within a session's main loop** — not declarable on ANY unmodified harness
   (model pinned per session across ~1–4 fixed slots). Must be inferred (Tier 0). Intrinsic to
   harness design. `opusplan` plan→exec is a partial, harness-driven exception, not a declarable channel.
2. **A reliable per-request extra_body/metadata channel** — Claude Code exposes none (only a fixed
   opaque `metadata.user_id`); Codex/Continue are config-level only. Tier 2 is partial, never per-request.
3. **Cursor** for self-hosted agent execution (backend-mediated + Verify gate); **Amp/Devin/closed
   SaaS agents** entirely (no base_url override) — out of scope.
4. **Tier 3 (native intent field)** — absent from both wire schemas and every harness.
5. **MCP / plugins** — add tools/resources inside a session but the harness still calls the LLM with
   its own model string; they cannot carry routing intent on the harness→LLM call.

## Streaming tension (architectural)

Both APIs deliver via SSE (OpenAI data-only `data:`/`[DONE]` chunks; Anthropic named
`message_*`/`content_block_*` events). Verify-before-deliver and low-latency streaming are in direct
tension — buffering raises TTFT; optimistic streaming forfeits clean mid-stream fallback. The tier
model does not remove this; it remains the central data-plane risk.

## Flagged low-confidence / version-dependent facts

- **Cursor's exact gating** is backend-mediated + a Verify step — **not** confirmed vendor-prefix
  validation (TensorZero ran an arbitrary non-prefixed id through Ask/Agent/Cmd+K).
- **Cline custom-header support** undocumented — assume none.
- **Codex `wire_api`** `chat` vs `responses` is version-dependent; third-party gateways commonly use `chat`.
- **OpenRouter suffix set** changes over time (at least `:nitro`/`:floor`/`:exacto` + `:online`/`:free` + `openrouter/auto`).
- **Claude Code caveats:** enterprise `availableModels` allowlist must include preset tokens if set;
  a trailing `[1m]` suffix is stripped and `modelOverrides` remaps built-in picker IDs (a plain
  intent token is unaffected).

## Key citations

- Claude Code: `code.claude.com/docs/en/llm-gateway`, `.../model-config`, `.../env-vars`
  ("skips validation for the model ID set in `ANTHROPIC_CUSTOM_MODEL_OPTION`… use any string";
  "passed to the provider as-is and are not transformed"; gateway `/v1/models` discovery via
  `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1`).
- OpenAI-compat clients: `aider.chat/docs/llms/openai-compat.html`,
  `docs.cline.bot/provider-config/openai-compatible`,
  `docs.continue.dev/customize/model-providers/top-level/openai`,
  `developers.openai.com/codex/config-reference`.
- Schemas: OpenAI Chat Completions + Anthropic Messages references (`model` free-form string;
  `extra_body`/`metadata`/`user` survive the SDK, not necessarily the harness).
- Precedent: OpenRouter slugs, LiteLLM aliases, Cloudflare AI Gateway `dynamic/<route>`, Portkey configs.
- Cursor: `tensorzero.com/blog/reverse-engineering-cursors-llm-client/` (backend mediation; arbitrary id worked).
