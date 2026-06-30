# anvil-serving — context for Claude Code

**What this is:** a network-facing, quality-gated router that fronts the Anthropic and OpenAI APIs
and routes coding-harness traffic across local and cloud model tiers — with per-request structural
verification and automatic fallback. Install, run `anvil-serving serve`, point your harness at
`http://127.0.0.1:8000`, and you get *local where it's been proven, cloud where it hasn't*.

The router is **shipped (v0.3.0)** — 18 tasks, milestones M0-M3, 378 tests green. The serving
substrate (`profile`, `models sync`, `deploy`, `preflight`, `benchmark`, `multiplexer`) also ships
and right-sizes the local tiers the router routes across.

Source of truth for product framing: **`README.md`** (accurate; rewritten for launch).

---

## Golden rule: call Claude through the Claude Agent SDK, never the raw Anthropic API

Any code that programmatically calls Claude/Anthropic models — product features (the
discovery / `analyze` / tuning loops) and any helper script — MUST use the **Claude Agent
SDK** (which runs on the user's Claude subscription), NOT the plain `anthropic` SDK or a
direct `api.anthropic.com` request authenticated with an `ANTHROPIC_API_KEY`. The raw API
bills usage separately; the Agent SDK uses the subscription. If a raw-API path appears
unavoidable, STOP and flag it for a human decision — do not add it silently. (Known existing
exception to migrate: the `CloudBackend` / `RelayBackend` transparent relay currently uses
raw `urllib` → the upstream; any NEW model-calling code must use the Agent SDK.)

---

## Architecture

```
anvil_serving/
  cli.py               dispatch: profile | models | deploy | serves | serve | preflight |
                                 benchmark | eval | multiplexer | cache-prune | score
  config.py            cross-platform auto-detect: Claude logs dir, HF cache roots, model dirs
  profile.py           usage percentiles + role split (-> _aggregate_usage.py, _role_split.py)
  models.py            scan HF caches, pull cards, extract serving facts, write INDEX.md (-> _sync.py)
  deploy.py            render tuned SGLang docker-compose for a given gpu + model
  preflight.py         correctness gate against any OpenAI-compatible endpoint
  benchmark.py         replay measured request distribution (TTFT, throughput, prefix-cache hit)
  multiplexer.py       single-resident model swap on one GPU (SGLang + vLLM backends)
  eval.py              unified shadow-eval harness (generalised planning-capability eval)
  score.py             quality scoring for eval outputs
  serves.py            model-serve lifecycle verb
  cache_prune.py       HF cache cleanup helper

  router/              THE MAIN PRODUCT — all shipped
    serve.py           `anvil-serving serve` entrypoint: config → backends → front door
    front_door.py      ThreadingHTTPServer accepting Anthropic Messages + OpenAI Chat Completions,
                       binding 127.0.0.1 (never localhost — see gotchas), SSE streaming
    intent.py          PRESETS enum (planning/quick-edit/review/chat/long-context) + resolve()
    classify.py        Tier-0 work-class classifier (infers intent from raw payload)
    policy.py          residency-aware routing: hard constraints → profile deny → cost order
    fallback.py        ordered tier walk: serve → verify → escalate; retry cap + circuit breaker
    verify.py          cheap inline structural verifiers (NonEmptyContent, ToolCallJSONValid,
                       CodeParses, DiffWellFormed, NotTruncated, RefusalMarker …)
    commit_window.py   streaming commit window: buffer + verify before first byte → harness
    profile_store.py   quality profile: (tier, work_class) → {score, decision, fingerprint, …}
    profile_bootstrap.py  bootstrap profile from shadow-eval / async calibration
    calibrate.py       async off-hot-path LLM-judge calibration loop
    fingerprint.py     serve fingerprint: model + quant + engine + flags (stale-row detection)
    decision_log.py    per-request DecisionRecord with per-tier token accounting
    metrics.py         traffic and routing metrics
    registry.py        backend/tier registry
    seams.py           typed extension seams (hook points for plugins/adapters)
    secrets.py         credential resolution + redaction (never log keys)
    discovery.py       /v1/models payload (advertises preset vocabulary)
    config.py          RouterConfig: tiers, presets, budget, circuit-breaker params
    internal.py        InternalRequest, Message, Backend protocol, NoAvailableTierError
    dialects/          anthropic.py + openai.py — wire-dialect parse + response rendering
    backends/          cloud.py (CloudBackend: urllib relay to Anthropic/OpenAI)
                       local.py (RelayBackend: urllib relay to local SGLang/vLLM)

templates/   configs/   docs/   examples/fakoli-dark/   plugins/   specs/
```

### Request path (one sentence per module)
1. **`front_door`** receives the request, parses it with the matching **`dialects/`** parser into
   an `InternalRequest`, and hands it to the injected backend.
2. **`serve.RoutingBackend`** calls `intent.resolve()` (preset from `model` field, or Tier-0
   `classify`) then `policy.route()` to get an ordered candidate list.
3. **`fallback`** walks candidates; for each, `verify` runs cheap structural checks on the
   assembled response; on failure it escalates to the next tier.
4. For streaming on fail-prone classes, **`commit_window`** buffers the local response and
   verifies before forwarding the first byte.
5. Every decision is written to **`decision_log`** and every fallback feeds **`profile_store`**
   as a calibration signal.

---

## Run / dev

```bash
pip install -e .               # stdlib-only; no required runtime deps
anvil-serving serve --config configs/example.toml   # start the router on 127.0.0.1:8000
anvil-serving --help           # all verbs

# Substrate (right-size + validate local tiers):
anvil-serving profile --out-dir .
anvil-serving models sync --out ./model-library
anvil-serving deploy --model /path/to/model --gpu 1 --context 131072 --served-name local
anvil-serving preflight --base-url http://127.0.0.1:30000/v1 --model local
anvil-serving benchmark --base-url http://127.0.0.1:30000/v1 --model local --burst 20
```

Point a harness at the router:
```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:8000"
export ANTHROPIC_MODEL="planning"   # an intent preset, sent verbatim in the model field
```
Cloud credentials go in env vars only — never in config files. The front door binds
`127.0.0.1` by default; see `SECURITY.md` before binding publicly.

---

## The hard-won gotchas (don't relearn these)

1. **`127.0.0.1`, never `localhost`.** On Windows, `localhost` triggers a ~21-second IPv6
   DNS stall before it falls through to the loopback address. Every URL in configs,
   tests, and examples uses `127.0.0.1` explicitly. This is baked into `front_door.py`'s
   default bind address.
2. **Stdlib-only.** The router and substrate are stdlib-only by design. No FastAPI, no
   aiohttp, no openai SDK in the hot path — `http.server.ThreadingHTTPServer` + `urllib`.
   Don't add a runtime dependency without explicit sign-off.
3. **WSL2 load OOM:** no `memory=` in `.wslconfig` → VM caps at ~50% host;
   `--weight-loader-disable-mmap` then loads the whole model into RAM → OOM-kill
   (`scheduler died, exit code -9`). Fix: raise WSL memory (64 GB on a 96 GB host).
4. **mmap over virtiofs** (Windows bind mount → Linux container) is pathologically slow;
   disable it, but then watch RAM (see above).
5. **GGUF != SGLang/vLLM.** GGUF is llama.cpp-only; SGLang and vLLM need safetensors.
   `models sync` flags this in INDEX.md's "SGLang-loadable" column.
6. **Thinking-by-default models** (Qwen3.5, gpt-oss, etc.) return *empty* content with a
   small `max_tokens` budget — they spend it reasoning. Disable per request with
   `chat_template_kwargs:{enable_thinking:false}` or give >= 4096 tokens. Preflight and
   benchmark must send the disable params or they time out.
7. **Blackwell sm_120 caveats:** some FP8 MoE paths hang post-load; AWQ/compressed-tensors
   via Marlin works. Run `preflight` before trusting a new model on sm_120.
8. **Never self-verify.** Agents that check their own output game the check. Every
   correctness gate (verify module, preflight, eval) must be independent of the model
   that produced the output.
9. **Thinking-budget starvation is a real failure mode.** `NonEmptyContent` in `verify.py`
   exists specifically because a local model on a small `max_tokens` budget produces valid
   JSON with an empty `content` array — looks successful, is wrong. The verifier catches this.

---

## Key design decisions (the "why")

- **The `model` field is the routing channel.** It's present in both Anthropic Messages and
  OpenAI Chat Completions, forwarded verbatim, and free-form. Named presets in the model
  field (`planning`, `quick-edit`, `review`, `chat`, `long-context`) is the right wire
  surface for harnesses that can be configured (Claude Code, Aider, Codex CLI).
- **Tier-0 classifier is the universal floor.** For harnesses that can't set the model
  field (or don't), `classify.py` infers work-class from the raw payload (token count,
  `thinking` flag, tool types, image content, system-prompt fingerprint).
- **Filter, then rank.** `policy.route()` runs hard constraints → profile deny → cost order.
  It never routes a `deny` work-class to local, regardless of availability.
- **The integration point is the harness, not anvil's state engine.** Anvil core is NOT an
  LLM gateway; it exposes one `custom_base_url` for optional planning augmentation. The
  router lives where agent traffic actually flows: in front of the harness.
- **OpenClaw is the beachhead, not the dependency.** The `before_model_resolve` hook unlocks
  per-request client-side intent; it ships as a thin adapter plugin in `plugins/`, not a
  core dependency. The front door is protocol-standard and works with any harness.

---

## Docs map

- `README.md` — product framing, quickstart, substrate commands, worked example
- `docs/QUALITY-GATED-ROUTER.md` — full design (intent presets, tier ladder, verify-fallback, profile)
- `docs/OPENCLAW-INTEGRATION-SPEC.md` — OpenClaw adapter plugin spec (verdict: go-with-caveats)
- `docs/findings/` — the research evidence (planning-capability eval, integration audit,
  harness intent routing, OpenClaw vs Hermes customization)
- `examples/fakoli-dark/` — real two-tier instance (heavy :30000 SGLang, fast :30001 vLLM)
- `specs/archive/` — pre-pivot design history (archived; marked historical)
