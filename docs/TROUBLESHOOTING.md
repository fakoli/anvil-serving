# Troubleshooting

Symptom-first fixes for the router and the local serving tools. Each entry states what the
symptom means, what to check, and the fix. Use `127.0.0.1` in local URLs, never `localhost`
(see [the Windows stall entry](#everything-is-slow-on-windows-for-the-first-20-seconds)).

For a from-scratch setup walkthrough, start with [Getting started](GETTING-STARTED.md).

## The router returns HTTP 503

**What it means.** The direct alias named by the request has no ready local serve, is
quiesced, or has reached its admission limit. The gateway does not choose another model.

A third 503 is unrelated to routing: `server busy; try again later` means the concurrency cap
was hit (see [Request rejected with 413 or a size cap](#request-rejected-with-413-or-a-size-cap)).

**What to check.**

- `GET http://127.0.0.1:8000/v1/decisions` to identify the requested alias and its
  readiness or admission result.
- Confirm the configured serve is up, then run `anvil-serving eval preflight --base-url
  http://127.0.0.1:<port>/v1 --model <served-name>`.
- Inspect the alias-to-tier binding in `[router.model_routes]`; unknown aliases are 404,
  while a known alias with no admissible local tier is 503.

**Fix.** Start or repair the configured local serve, then readmit it after any transition.
If the requested capability should use another model, change its explicit alias binding and
restart or reload the router.

## Preflight fails

**What it means.** `anvil-serving eval preflight` runs four correctness tests against an
OpenAI-compatible endpoint (`anvil_serving/preflight.py`): a short coding smoke, structured JSON,
long-context needle retrieval (default ~128k tokens), and a shared-prefix tool-calling batch
(default 20 concurrent — this one catches sm_120 garbage output and spec-decode tool corruption).
Exit code 0 means all passed; 1 means at least one failed.

**What to check.**

- **Serve not up / wrong port:** every test reports `error: <URLError>` — connection refused.
  Confirm the serve is listening and the `--base-url` port matches (heavy `:30000`, fast `:30001`
  in the examples).
- **Wrong `--model` name:** the value must be the serve's `--served-model-name`, not the HF repo
  id or a gateway alias. A mismatch surfaces as an HTTP 404 / model-not-found error from the
  serve.
- **Thinking-budget timeout or false-fail:** inspect the reported `finish_reason`, visible length,
  reasoning-channel length, and reasoning-token usage. For a functional gate on Qwen-style
  models, use `--thinking-mode disabled` (or `--no-thinking`) with the default 256 visible-token
  allocation. For a quality gate, use `--thinking-mode enabled` with benchmark-calibrated
  `--reasoning-headroom-tokens`; this headroom is added to `--visible-answer-tokens` as the API
  completion cap. GPT-OSS-style models ignore Qwen's chat-template control; use their supported
  `--reasoning-effort` semantics and an explicit budget instead.
- **Tool-batch failures on new hardware:** garbage signatures (`<<tool`, `<|`, `function=`)
  in the batch test are the known sm_120 failure mode — see CLAUDE.md gotcha 7 and
  `docs/findings/blackwell-sm120-lab-notebook.md`.

**Fix.** Address the specific failing test; do not trust throughput numbers from a serve that
has not passed preflight.

## Responses come back empty

**What it means.** Thinking-by-default models can spend a small `max_tokens` budget entirely on
hidden reasoning and return an empty visible response. The thin gateway preserves that upstream
result; it does not retry another model.

**What to check.**

- Is the tier's model a thinking-by-default model (Qwen3.5, gpt-oss, GLM, ...)?
- Is the caller sending a small `max_tokens` (< 4096)?
- Inspect the upstream serve response and its generation settings.

**Fix.** Either disable thinking on the tier — in the tier's config:

```toml
extra_body = { chat_template_kwargs = { enable_thinking = false } }
```

— or give the model an adequate budget (>= 4096 tokens) so it finishes reasoning and still
answers. gpt-oss-style models ignore `enable_thinking` and need the budget approach. Full
per-model settings walkthrough: [Model settings](MODEL-SETTINGS-EXAMPLE.md).

## OpenClaw shows the wrong context window / requests get clamped

**What it means.** OpenClaw computes `max_completion_tokens = declared contextWindow − actual
prompt tokens`, clamped to a floor of 1 — it does not reject an oversized prompt. If an alias's
`contextWindow` in the OpenClaw provider config understates its direct tier's window, a growing
conversation eventually makes every turn's completion budget compute negative and floor to **1
token**.

**What to check.** Each alias's `contextWindow` in
`~/.openclaw/openclaw.json` must equal its one configured tier's context
window. In the reference config, `llm.primary` uses `heavy-local`'s `131072`
window and `llm.voice` uses `fast-local`'s `32768` window.

**Fix.** Let the product render the exact per-alias values:
`anvil-serving harness sync openclaw --config configs/example.toml`.

## Port already in use

**What it means.** Something else is bound to the port the router (default `8000`) or a model
serve (commonly `:30000` heavy, `:30001` fast in the examples) wants.

**Fix.** Start the router on a free port and use it in every URL and harness base-URL:

```bash
anvil-serving router run --config configs/example.toml --port 8010
```

For serve ports, change the port mapping in the serve's compose file and update the matching
tier's `base_url` in the router config — they must stay in lockstep.

## Everything is slow on Windows for the first ~20 seconds

**What it means.** You used `localhost` somewhere. On Windows, `localhost` triggers a ~21-second
IPv6 DNS stall before falling through to the loopback address. This is the project's hard rule:
every URL in configs, tests, and examples uses `127.0.0.1` explicitly, and the front door binds
`127.0.0.1` by default (`anvil_serving/router/front_door.py`).

**Fix.** Replace `localhost` with `127.0.0.1` in the offending base URL, config, or env var.

## Windows starves for RAM during repeated big model loads (WSL page cache)

**What it means.** Every 60–90 GB model-weight stream (bakeoffs, repeated serve restarts) passes
through the WSL2 VM's Linux page cache, which grows until it fills most of the VM — 50–54 GB of a
64 GB VM was observed during the 2026-07-10/11 Blackwell bakeoff. The VM holds that memory, and
Windows itself starts starving. `autoMemoryReclaim=gradual` in `.wslconfig` does return it, but
lags load bursts by minutes.

**Fix.** For repeated Anvil-owned downloads and managed model loads, enable the persistent
machine policy once in `~/.anvil-serving/host.toml` (or
`$ANVIL_SERVING_HOME/host.toml`):

```toml
schema_version = 1

[cache_reclaim]
enabled = true
distro = "docker-desktop"
threshold_gb = 16
```

Covered command dry runs disclose the policy. Their existing `--confirm` authorizes a
best-effort postcondition after the download or the model's readiness gate. It reclaims only when
the operation grew cache by at least 1 GiB, total cache meets the threshold, and growth has
settled. A readiness timeout, active-I/O refusal, unreadable sample, or drop failure warns without
turning a successful model operation into a failure.

For diagnosis or an operation outside that lifecycle, inspect and manually drop clean page cache
(data-safe, but the next load re-reads weights from disk):

```bash
anvil-serving host memory                    # host RAM / WSL used + page cache / GPU VRAM
anvil-serving host reclaim --confirm         # sync && echo 1 > /proc/sys/vm/drop_caches (as root)
```

`reclaim` refuses while a load is actively streaming (the cache is growing fast — dropping it
mid-load would evict pages the loader is about to reuse); wait or `--force`. For a bakeoff
session, run the watchdog in a spare terminal instead of remediating by hand:

```bash
anvil-serving host reclaim --watch --threshold-gb 40 --interval 30 --confirm
```

This is a symptom-relief valve, not the sizing fix — if the VM cap itself is wrong, size it with
`host doctor` / `host wsl-config` ([CLI reference → repair the host](cli/host.md#repair-the-host)).
The automatic policy deliberately excludes ad-hoc Compose, voice, request-time ComfyUI loading,
and the request-triggered multiplexer; it never forces a reclaim while cache is still growing.
Use `host status` to see the resolved source, distro, threshold, validity, and host applicability.

## 401/403 from the router

**What it means.** Front-door auth is on and the request carried no valid token. Auth is
configured by env-var *name*, never by a literal secret in the config:

```toml
[server]
auth_env = "ANVIL_ROUTER_TOKEN"
```

The token is resolved from that env var once at server start. When `auth_env` is unset, auth is
off entirely. The router itself answers failed auth with **401** (`invalid or missing API key`)
on every route — a 403 usually comes from a proxy or the upstream serve, not from anvil
(`anvil_serving/router/front_door.py`).

**What to check.**

- Send the token as either `Authorization: Bearer <token>` or `x-api-key: <token>`; both are
  accepted (constant-time compare).
- `GET /healthz` is the *only* unauthenticated route (container healthchecks) — note the `/health`
  alias is **not** exempt, only the literal `/healthz` path.
- Is the env var named by `auth_env` actually set in the router process's environment? A missing
  or restarted-without-env process is the common cause after a redeploy.
- Unauthenticated callers get a uniform 401 whether or not the path exists — do not read a 401
  as "wrong URL".

## Request rejected with 413 or a size cap

**What it means.** The front door enforces resource caps before doing any work
(`anvil_serving/router/front_door.py`):

- **413 `request body too large`** — the body exceeds the size cap. Default 32 MiB; override
  with the `ANVIL_MAX_BODY_BYTES` env var (bytes).
- **413 `request exceeds the context window of every available tier`** — an over-context
  request: no configured tier's `context_limit` can physically hold the prompt. This is a caller
  problem, refused up front instead of forwarded to a tier that would 400 at the model. Shrink
  the request, or add/route a larger-window tier.
- **503 `server busy; try again later`** — the concurrency cap was hit. Default 64 in-flight
  requests; override with `ANVIL_MAX_CONCURRENCY`.
- **411** — chunked request bodies (`Transfer-Encoding`) are unsupported; send `Content-Length`.

## pip install anvil-serving is missing commands

**What it means.** Published packages lag `main`. The source tree is versioned v0.13.2 while
tags and PyPI releases can trail it, so a command documented here may not exist in the installed
release.

**Fix.** Install editable from a clone:

```bash
git clone <this repo> && cd anvil-serving
pip install -e .
anvil-serving --help
```

The install is stdlib-only — no required runtime dependencies.

## Where to look when diagnosing

- **`GET http://127.0.0.1:8000/v1/decisions`** — per-request metadata from the decision
  log (`?limit=1..500`, default 20): requested alias, selected tier, status, tokens, and cost.
- **`anvil-serving router logs`** — docker logs for the deployed router container
  (`--tail`/`--since`/`--follow`). Exhaustion and over-context refusals are logged to stderr
  with the tier list and reason.
- **MCP tools** — `anvil-serving mcp tools` exposes `router_status` and
  `decision_summary`, locally or via the split-host controller.
- **Playbooks** — step-by-step operator workflows for status, preflight, benchmark, and OpenClaw
  sync live in [Operator playbooks](OPERATOR-PLAYBOOKS.md).
## A promotion stopped before container mutation

Run `anvil-serving router transition-status --tier heavy-local --router-url
http://127.0.0.1:8000`. A drain timeout means an admitted generation is still active; the workflow
does not stop any serve. If timeout recovery could revalidate the old health and exact model name,
it safely readmits the tier. Otherwise the tier stays fail-closed: correct the endpoint, confirm its
`/health` and `/v1/models` identity, then use `serves promote ... --resume`.

`--resume` never trusts old artifacts. It reasserts quiescence, drains again, reruns health,
identity, and every direct preflight gate, and only skips recreation when the intended target is
already running. A failed automatic rollback reports the same fail-closed state; inspect both serve
and router status before retrying.

## A healthy serve is reported as `identity_mismatch`

The health port is live but `/v1/models` did not advertise the tier's exact configured `model`.
Correct either the serve's `--served-model-name` or the reviewed router config; do not readmit it by
bypassing the guard. The identity check does not prove weights, revision, quantization, or engine
flags—use promotion fingerprints and preflight evidence for those properties.

The reference two-GPU transition leaves Fast resident on the RTX 5090 while Heavy changes on the
RTX PRO 6000. The accepted final router restart can briefly interrupt Fast connections, but it must
not stop or recreate the Fast model container. Any live Fakoli Dark promotion remains a separate
explicit human-gated operation.
