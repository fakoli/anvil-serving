# Troubleshooting

Symptom-first fixes for the router and the local serving tools. Each entry states what the
symptom means, what to check, and the fix. Use `127.0.0.1` in local URLs, never `localhost`
(see [the Windows stall entry](#everything-is-slow-on-windows-for-the-first-20-seconds)).

For a from-scratch setup walkthrough, start with [Getting started](GETTING-STARTED.md).

## The router returns HTTP 503

**What it means.** In the keyless (local-only) default, a 503 on a completion request is the
router's *designed* exhaustion signal, not a crash: no quality-gated tier passed for this request,
and rather than serve unverified output the router refuses cleanly so a downstream gateway can
hand the request to its own provider (ADR-0001). The response body says
`no quality-gated tier is available for this request`. The status code is configurable via
`[router].exhaustion_status` (default 503; any 100–599 integer —
`anvil_serving/router/config.py`).

There are two distinct exhaustion cases, logged differently on the server side
(`anvil_serving/router/serve.py`):

- **Unbound:** the gated candidate tiers have no working backend binding (endpoint or
  credentials not configured/reachable).
- **Exhausted:** the tiers *were* bound and reachable, but every candidate's response failed
  structural verification (or was guarded out by the budget or circuit breaker).

A third 503 is unrelated to routing: `server busy; try again later` means the concurrency cap
was hit (see [Request rejected with 413 or a size cap](#request-rejected-with-413-or-a-size-cap)).

**What to check.**

- `POST http://127.0.0.1:8000/v1/route` with the same payload — a no-generation routing probe
  whose response includes the routing `reason` (deny, metered-cloud gate, unknown tier, ...).
  Note that an *unbound* 503 is raised before the fallback walk runs, so `GET /v1/decisions`
  has no record of that request — the router's stderr log and `/v1/route` are the diagnostic
  surfaces.
- Profile deny rows for the work class: the quality gate fails closed, so an unmeasured *local*
  tier on an eval-proven-weak class (e.g. `planning`) is denied by design
  (`anvil_serving/router/policy.py`).
- Tier reachability: is the serve actually up? `anvil-serving eval preflight --base-url
  http://127.0.0.1:30001/v1 --model <served-name>`.
- Did the request pin a denied tier? A wire `model` field naming a concrete tier id is a
  *preference*, never a gate override: a denied pin is redirected to the work-class's gated pool,
  and if that pool is also all-denied you get a clean 503 (`policy.py`, the pin-redirect logic).

**Fix.** Bind/repair the missing tier, promote a reviewed profile that allows the class, or
configure an opt-in cloud tier for the class (see
[Cloud never gets used](#cloud-never-gets-used)).

**ADR-0005 caveat for OpenClaw operators.** Do not rely on OpenClaw's native failover
(`agents.defaults.model.fallbacks`) to escape this 503 on plugin-routed turns. Live validation
(ADR-0005, [`docs/adr/0005-anvil-503-native-failover-unreliable.md`](adr/0005-anvil-503-native-failover-unreliable.md))
showed the 503 *does* trip OpenClaw's failover, but the plugin's `providerOverride: "anvil"` pins
the provider for the run's entire attempt loop — the fallback models are re-resolved against
anvil and 503 again. Mitigations: move at-risk presets into the cloud-preferred set via
`ANVIL_CLOUD_CLASSES` (those turns never touch anvil), or — the durable fix — enable anvil's own
opt-in metered cloud tier so escalation happens *inside* anvil and no 503 is ever returned for
those classes.

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
  id or a router preset. A mismatch surfaces as an HTTP 404 / model-not-found error from the
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

## Responses come back empty / verification keeps failing

**What it means.** Thinking-by-default models spend a small `max_tokens` budget entirely on
hidden reasoning and return a *valid-looking* response with empty content. The router's
`NonEmptyContent` verifier (`anvil_serving/router/verify.py`) exists for exactly this: it fails a
response with empty/whitespace text *and* no tool calls, with the note
`empty content and no tool calls (thinking-budget starvation?)`. Each failure escalates to the
next candidate tier; if all candidates fail the same way you get the exhaustion 503.

**What to check.**

- Is the tier's model a thinking-by-default model (Qwen3.5, gpt-oss, GLM, ...)?
- Is the caller sending a small `max_tokens` (< 4096)?
- `GET /v1/decisions` — repeated `non_empty_content` failures against one tier confirm it.

**Fix.** Either disable thinking on the tier — in the tier's config:

```toml
extra_body = { chat_template_kwargs = { enable_thinking = false } }
```

— or give the model an adequate budget (>= 4096 tokens) so it finishes reasoning and still
answers. gpt-oss-style models ignore `enable_thinking` and need the budget approach. Full
per-model settings walkthrough: [Model settings](MODEL-SETTINGS-EXAMPLE.md).

## Cloud never gets used

**What it means.** This is the shipped default, and it is a billing decision, not a bug.
`[router].metered_cloud` is the explicit gate: a `privacy = "cloud"` tier is a routing candidate
*only* for work-classes listed there. When the list is absent or empty — as in
`configs/example.toml` — cloud is never a candidate, even if a cloud tier is defined and even for
custom presets (`anvil_serving/router/policy.py`, the metered-cloud gate; ADR-0001,
[`docs/adr/0001-cloud-cost-and-subscription-auth.md`](adr/0001-cloud-cost-and-subscription-auth.md)).

**What to check.** `POST /v1/route` with the same payload — the routing probe's `reason` shows
when the metered-cloud gate excluded a cloud tier (drop reasons are not part of the
`GET /v1/decisions` summary).

**Fix.** If you *want* metered cloud for specific classes, start from
`configs/example-with-cloud.toml` and list them explicitly:

```toml
[router]
metered_cloud = ["planning"]
```

Cloud credentials go in env vars only — never in config files.

## OpenClaw shows the wrong context window / requests get clamped

**What it means.** OpenClaw computes `max_completion_tokens = declared contextWindow − actual
prompt tokens`, clamped to a floor of 1 — it does not reject an oversized prompt. If a preset's
`contextWindow` in the OpenClaw provider config understates the real routed window, a growing
conversation eventually makes every turn's completion budget compute negative and floor to **1
token**. This caused a live incident (2026-07-02): turns "succeeded" with 1-token responses,
verification failed them, and the circuit breaker tripped on healthy tiers
(`docs/OPENCLAW-INTEGRATION-SPEC.md` §2, "contextWindow rule").

**What to check.** Each preset's `contextWindow` in `~/.openclaw/openclaw.json` must equal the
**largest** context window among the tiers that preset can actually route to — for the reference
config that is `heavy-local`'s `131072`, for *every* preset, because every preset either routes
to `heavy-local` directly or can escalate to it.

**Fix.** Set all presets' `contextWindow` to the largest routed tier window, or better, let the
product render it: `anvil-serving harness sync openclaw --config configs/example.toml`. (v0.7.1
also hardened the router side: a caller-capped `length` stop now passes the `NotTruncated`
verifier instead of 503ing — but correct `contextWindow` values remain the real fix.)

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

- **`GET http://127.0.0.1:8000/v1/decisions`** — per-request decision summary from the decision
  log (`?limit=1..500`, default 20): work class, requested tiers, per-tier attempts (including
  verify failures), served tier, tokens, and cost. Requests refused *before* the fallback walk
  (unbound or over-context) write no record — check the stderr log for those.
- **`POST http://127.0.0.1:8000/v1/route`** — a no-generation routing probe for one payload:
  returns the selected tier, confidence, and the routing `reason` (deny, metered-cloud gate,
  ...).
- **`anvil-serving router logs`** — docker logs for the deployed router container
  (`--tail`/`--since`/`--follow`). Exhaustion and over-context refusals are logged to stderr
  with the tier list and reason.
- **MCP tools** — `anvil-serving mcp tools` exposes `router_status` and
  `decision_summary` (plus `route_decision` for a no-serve routing probe against
  `POST /v1/route`), locally or via the split-host controller.
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
