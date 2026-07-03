# ADR-0010 — Specialized-Engine Tier: run any model on any engine (config-first, RelayBackend-served)

- **Status:** Accepted
- **Date:** 2026-07-03
- **Relates to:** ADR-0003 (genericity / out-of-box router correctness), ADR-0004 (containerized token-authed service), ADR-0008 (heavy-tier speculative decoding), ADR-0009 (measured write-back loop — the fingerprint axis this adds is measured by that loop); `anvil_serving/router/{backends/relay.py, config.py, fingerprint.py, serve.py}`; CLAUDE.md gotchas #10/#16/#17/#18 (sm_120 NVFP4), #6/#9 (thinking-budget starvation)

## Context

**Motivation (maximum model-access flexibility).** SGLang is excellent for our agent workload
(the heavy tier: hybrid GDN+MoE, spec-decode, radix cache) but it — like vLLM — gates *which
models we can run*: it needs safetensors and day-one architecture support, so brand-new models and
exotic quant formats are often unservable on it. The goal is that a new or better model is **never
blocked because our agent engine doesn't support it** — run it on whatever engine does (llama.cpp
for fresh GGUF architectures, ktransformers for MoE-NVFP4, DS4/DwarfStar for DeepSeek V4 Flash,
ExLlamaV3 for EXL3), fronted by the same router, while SGLang stays the agent workhorse. This ADR
is that flexibility feature. ktransformers/MoE-NVFP4 on sm_120 is the concrete first case below;
the design is engine-general.

The hardware is Blackwell sm_120 (RTX PRO 6000 96 GB + RTX 5090). On sm_120 the batch
engines choke on some model/quant combos we want to serve: MoE-NVFP4 grouped-GEMM produces
garbage / crashes on vLLM and SGLang (CUTLASS #3096, vLLM #31085/#33416), and block-scaled
FP8 is a dead-end (gotcha #16). ktransformers is flagged as the one system with a working
MoE-NVFP4 path on sm_120 (kt-kernel MXFP4 operator, sidestepping the grouped-GEMM crash
cluster) — a scoped trial candidate, **not yet verified on this box**. We therefore want a
first-class way to declare a router tier backed by a **specialized inference engine** for
model/quant combos the batch engines cannot serve well, toggleable per tier (opt-in).

Hard realities that bound the option space:

- **RelayBackend already exists** (`relay.py:21-78`): a `CloudBackend` subclass that relays to
  ANY OpenAI-compatible local endpoint over `urllib`, auth-optional (`_require_key=False`),
  inheriting the `/v1/chat/completions` path, real `stream:true` SSE relay, tool
  forwarding/translation, and usage/finish_reason/tool_calls extraction. `build_backend_for_tier`
  (`serve.py:194-198`) selects it for any `privacy != "cloud"` tier. So **an OpenAI-compatible
  ktransformers serve is routable today with zero new backend code** — a tier block suffices.
- The Backend Protocol (`internal.py:170-180`) is a single `generate()` method; supported
  dialects are exactly `{openai, anthropic}` (`cloud.py:93`).
- Three genuine gaps, all config/identity plumbing (not a new backend class):
  1. `Tier` (`config.py:61-86`) is a frozen dataclass exposing **no** `engine`/`quantization`/
     `params` field. `fingerprint.IDENTITY_FIELDS` (`fingerprint.py:46-55`) ALREADY has
     `quantization` and `params` slots, but they resolve to `None` on a real `Tier`, so a kt
     serve is fingerprint-indistinguishable from a vLLM/SGLang serve at the same
     base_url/model — it could inherit another engine's measured verdict.
  2. `relay_timeout` is a single **global** `RouterConfig` field (default 20 s, `config.py:123`)
     applied to every local tier; a slow NVFP4 large-prefill could be spuriously fail-fasted.
  3. There is **no per-tier concurrency cap** — only a process-global `BoundedSemaphore`
     (`front_door.py`) — so a low-throughput specialized engine cannot be given a smaller bound.
- Golden rules in force: stdlib-only router hot path (`http.server` + `urllib`); no new anvil
  runtime dependency without sign-off; **no self-verification** for any quality gate;
  **preflight before trusting any new engine on sm_120**; data-driven re-measure (no tier gates
  traffic without a measured-profile A/B); ADR discipline for any contract/architecture change;
  and the seams-taste rule (engines/plugins are thin config/adapters, not new core classes).

## Considered options

1. **Config-first minimal (CHOSEN).** Declare the specialized tier as an ordinary
   `privacy="local"` / `dialect="openai"` tier pointed at the external kt serve, served by the
   existing RelayBackend. Make it first-class with additive optional `Tier` fields
   (`engine`, `quantization`, `params`, `timeout`, later `max_concurrency`), add `engine` to the
   fingerprint identity, and gate the tier behind preflight + a measured-profile A/B. No new
   backend class; substrate untouched in the shipping path.
2. **EngineDescriptor registry.** A first-party in-process registry (launch + request-shaping +
   capabilities + concurrency) consumed by BOTH router and substrate, extensible via a typed
   `engine` seam, plus an optional `backend`-seam hook for a future non-HTTP engine. **Rejected:**
   its own Phase 1 is byte-identical to option 1's zero-code path, so the registry buys nothing
   for the only engine that exists — a framework for N=1. The `backend`-seam hook designs for a
   case the design itself concedes "would be a new ADR." Speculative generality; more surface to
   build, review, and get wrong for the same shipped capability.
3. **New `SpecializedBackend` class (± registry-driven backend selection).** **Rejected:**
   justified only by a non-HTTP transport or a dialect outside `{openai, anthropic}` — ktransformers
   is neither. This is exactly the "config would have sufficed" over-build; it violates the
   seams-taste rule and the no-over-build-past-RelayBackend constraint.

## Decision

Adopt option 1. A **specialized-engine tier is an ordinary `privacy="local"` / `dialect="openai"`
tier** whose `base_url` points at an external, OpenAI-compatible specialized-engine serve
(ktransformers first), routed by the **existing RelayBackend with no new backend code**.
Engine request-body knobs ride the existing `extra_body`; launch-time engine flags are the
serve's concern.

"First-class" is delivered by **additive, default-unset** changes, phased by proven need:

- **Phase 1 (zero anvil code):** hand-author `docker-compose.ktransformers.yml` + a `[[serve]]`
  entry (`serves.py` runs an arbitrary `up` argv and probes a configurable `health` path); declare
  the tier; run `preflight` (which also confirms kt speaks exactly `/v1/chat/completions`).
- **Phase 2 (additive contract):** add optional `Tier.engine`, `Tier.quantization`, `Tier.params`,
  `Tier.timeout`; add `("engine", ("engine",))` to `fingerprint.IDENTITY_FIELDS`; thread per-tier
  `timeout` through `build_backends` as an override of the global `relay_timeout`.
- **Phase 3 (additive capacity):** optional `Tier.max_concurrency`, enforced by a per-tier stdlib
  `BoundedSemaphore`, sized from `benchmark`.
- **Phase 4 (deferred):** substrate first-class (multiplexer/deploy engine branches) and
  registry-driven backend selection — only if a genuine second engine earns it; own ADR.

**Explicitly rejected:** a `SpecializedBackend` class and registry-driven backend selection for
ktransformers. **Gating:** no specialized tier joins a live preset pool until `preflight` passes
and its quality enters the measured profile (keyed by its engine-distinct fingerprint) via an
independent A/B — never by self-verification, and never as the sole tier of a preset before A/B.

## Consequences

**Keep:** the stdlib-only, urllib-only router hot path (kt is an external serve with its own
container/deps — no dependency crosses into the router); the RelayBackend serving path unchanged;
`build_backend_for_tier`'s privacy-based selection unchanged. No new anvil runtime dependency.

**Change (additive only):** five optional `Tier` fields and one `IDENTITY_FIELDS` entry. Because
`identity()` omits `None` fields, every existing engine-less tier's fingerprint stays
byte-identical — **no profile churn, no `FINGERPRINT_SCHEMA` bump**. A specialized tier now keys a
DISTINCT measured-profile fingerprint (cannot inherit a vLLM/SGLang verdict), and an in-place
vLLM->kt swap at the same base_url marks old rows stale. A slow NVFP4 prefill gets a per-tier
timeout; a low-throughput engine gets a per-tier concurrency bound.

**Give up / follow-up:** no swap-managed or `deploy`-rendered kt until Phase 4 (the substrate
multiplexer/deploy surfaces stay hardcoded to sglang+vllm); a kt tier is brought up via a
hand-authored compose + `[[serve]]` entry meanwhile. The `backend`-seam / registry path is left
unbuilt until a second, non-OpenAI-compatible engine justifies it.

**Risks / open items requiring a local trial (fail-safe by design — a broken/absent kt serve
raises `CloudBackendError` and fallback escalates):** the relay hardcodes `/v1/chat/completions`
with no per-tier path/header override (`cloud.py:622-633`), so a non-standard kt path or header is
a real gap that must surface in preflight; kt's tool-call JSON, SSE chunk shape, `usage` block, and
`GET /v1/models` support must be verified before the tier is trusted; and the core premise — kt
serving MoE-NVFP4 on sm_120 — is unverified on this box. Preflight is the first proof; no
production preset lists the tier until preflight + A/B pass.