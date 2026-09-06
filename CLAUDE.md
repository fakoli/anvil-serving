# CLAUDE.md — anvil-serving product context

## Product

Anvil Serving is one umbrella product with six explicit families: Model
Serving, Capability Gateway, Evaluation & Evidence, Anvil Voice, Anvil Media,
and Control Plane & Fleet. The families share one package, CLI, topology,
safety/evidence contract, and release line. Anvil Voice and Anvil Media are
first-class branded domains inside the umbrella, not separate products.

The Capability Gateway family is an explicit capability meta-router,
implemented as a thin authenticated gateway. Repeatable local serving,
preflight and benchmark evidence, voice/media operations, and fleet control
remain separate authority domains. The gateway is not an intent-driven model
router.

Every routing surface is serving-engine agnostic. Chat, purpose-model, and
audio routes select declared aliases or model names, endpoints, dialects,
capabilities, readiness, and admission state. They never select on a runtime
brand, container image, quantization, weight format, or launch mechanism. Any
serving engine may back a route when its bounded adapter satisfies the declared
protocol and capability contract.

The gateway accepts Anthropic Messages, OpenAI Chat Completions, and the
supported stateless Responses subset. `[router.model_routes]` is the complete
chat vocabulary: one normalized caller alias maps to exactly one local tier.
Unknown or missing aliases return 404. A selected tier that cannot serve returns
an error; it is not replaced by another tier.

A tier is either direct or an explicit set of 2–16 qualified equivalent
replicas on one host. Replica tiers require configured metadata. Round-robin
or capacity-aware selection chooses one ready, admitted member inside that
tier; it does not retry, substitute a model, or schedule across hosts. See
[replica configuration](docs/CONFIGURATION.md#qualified-same-host-replicas)
and [lifecycle limits](docs/cli/serves.md#replica-lifecycle-limits).

"Meta-router" names the authority split, not a model-selection algorithm. The
caller owns the requested capability alias; operator configuration owns the
single alias-to-tier mapping, endpoint or closed member set, and policy; and an opted-in inference
service owns only its mutable served-model metadata. The router validates and
projects those facts without changing the selected tier.

The gateway retains token authentication, protocol/tool translation, true
upstream SSE relay, readiness, admission control, and metadata-only
`DecisionLog` records. It also hosts deterministic purpose-model and audio
routes. It has no classifier, presets, policy/profile routing, residency
selection, fallback chain, cloud tier, response verifier, commit window, or
routing calibration loop. Named media workflows may separately expose explicit
caller-selected quality profiles whose exact parameters remain locked by that
one workflow; those profiles never choose a route or substitute a model.

## Reference topology

- **Fakoli Dark / two equivalent RTX PRO 6000 Max-Q GPUs:** split mode admits
  compatible workloads independently on UUID-backed `dark-compute-a` and
  `dark-compute-b`. `dual-gpu-exclusive` mode admits exactly one declared TP=2
  serve on both cards and blocks every other GPU inference start.
- **Capability placement:** `llm.primary`, `llm.voice`, and `vision.*` remain
  capability aliases, not physical GPU classes. An unavailable backing serve
  never falls back to the exclusive model or another tier.
- **Fakoli Mini:** OpenClaw Gateway and voice Realtime/proxy only. It is
  model-free in the reference topology. Mini loopback audio proxy ports forward
  to Dark; they do not host models.

`127.0.0.1` is host-relative. Never substitute `localhost` in URLs, examples,
or tests. Use private/tailnet addresses for cross-host traffic.

## Module map

```text
anvil_serving/
  cli.py                  CLI dispatch
  product_families.py     canonical product boundary and ordered journeys
  product.py              read-only product discovery CLI projection
  commands/               modular command families and deterministic registry
    registry.py           explicit decorated family assembly
    spec.py               command, option, policy, and manifest contracts
  serves.py               Docker/native-bound model lifecycle and GPU reservations
  service_runtime/        owner-checked launchd/Docker lifecycle and engine state
  models.py               model cache/source/recipe management
  preflight.py            endpoint functional qualification
  eval.py                 benchmark and evaluation commands
  benchmark.py            public benchmark compatibility facade
  benchmarking/           artifacts, specs, requests, evaluation, and runners
  controller.py           public authenticated-controller compatibility facade
  mcp.py                  public MCP compatibility and composition facade
  control_plane/
    authorization.py      explicit scoped node/fleet read authority
    controller/           persistence, security, HTTP, server, and CLI internals
    mcp/                  validation, security, protocol, runtime, and catalog
      tools/              explicit ordered tool-family implementations
  router/
    config.py             direct/replica topology, aliases, purpose/audio routes
    serve.py              deterministic chat relay backend
    front_door.py         auth, protocol endpoints, streaming
    availability.py       readiness cache/probes
    admission.py          concurrency controls
    replica_scheduler.py  bounded member scores; admission owns atomic reservation
    workloads.py          metadata-only in-flight request snapshots
    decision_log.py       metadata-only audit records
    discovery.py          configured alias advertisement
    dialects/             Anthropic/OpenAI translation
  observability/          bounded workload projections, fleet reads, dashboard
  voice/                  owned STT/TTS, bridge, Realtime operations
  media/                  named workflows, durable jobs, qualification, artifacts
```

The three compatibility facades remain supported import and entrypoint
surfaces. Their internal packages point toward shared foundations and domain
modules; internal modules do not import a facade to discover implementations.
MCP tool registration is an explicit ordered composition with direct
dictionary dispatch, not filesystem scanning or plugin discovery.

## Routing contract

```toml
[router.model_routes]
llm.primary = "primary-local"
llm.voice = "omni-local"
vision.ocr = "omni-local"
vision.general = "omni-local"
vision.video = "primary-local"
```

Aliases are lowercase after trimming; compatibility prefixes are not accepted.
They map only to local tiers. The tier's `model` is the concrete
upstream served model name and must not be confused with the public alias.

`engine`, `quantization`, runtime image, and engine-specific parameters are
deployment or observability metadata, not route-selection inputs. Engine-specific
launch flags, lifecycle operations, health/metrics parsing, and kernel tuning
belong in manifests, recipes, or bounded adapters behind the tier contract.
They may enrich readiness and evidence, but must not introduce implicit engine
preference, fallback, or model substitution. This applies equally when a tier
is backed by llama.cpp, NInfer, SGLang, vLLM, TensorRT-LLM, or another compatible
local engine. It also includes running Unsloth or an Unsloth-provided serving
stack: whether Unsloth supplies quantized artifacts, launch tooling, or the
upstream server, the router sees only the declared endpoint contract.

Embeddings and reranking route by exact configured model on their dedicated
endpoints. STT/TTS routes are operator-selected under `/v1/audio/*`. ComfyUI is
lifecycle-managed rather than a chat alias.

## Evidence boundaries

Readiness only establishes that a configured endpoint can receive traffic.
`eval preflight` and benchmark artifacts establish whether the endpoint is a
qualified capability. A configuration mapping never promotes a model. Publish
user-relevant benchmark results as dated findings with raw-artifact links and
the engine, hardware, quantization, context, concurrency, metrics, failures,
and caveats.

Every tracked file is public by default. Real topology, active/promoted route
assignments, GPU UUIDs, personal paths, and unsanitized working evidence belong
in the private operator repository selected through `ANVIL_SERVING_HOME`.
Credentials remain outside Git through environment or file-backed references.
Public examples and the packaged scaffold contain generic placeholders only.

## Working rules

1. Use only the Python standard library in `anvil_serving/` unless explicitly
   approved. The Mini-side remote MCP bridge is the approved exception: its
   source and lockfile live in `mcp_bridge/`, the official TypeScript MCP SDK is
   bundled into package data, and it requires Node.js 20+ only when remote MCP
   proxy mode is launched.
2. Secrets are environment-variable references only; never commit a literal.
   Treat the private operator repository the same way: it tracks configuration,
   not credential values.
3. Return dictionaries from library code; CLI wrappers print.
4. Durable lifecycle, host, routing, voice, and benchmark operations belong in
   the `anvil-serving` CLI and, where useful, MCP/controller surfaces.
5. New model-calling code uses the Claude Agent SDK, never the raw Anthropic SDK
   or a direct `api.anthropic.com` request.
6. Do not claim a local qualification without recorded test evidence.
7. Do not modify `specs/archive/`.

## Agent model strategy

Use model depth at decision boundaries, then reduce it for bounded execution:

- Use **GPT-5.6 Sol with high reasoning** for behavior-first PRDs, product or
  architecture boundaries, cross-cutting breaking changes, and final removal
  review.
- Use **GPT-5.6 Terra with medium reasoning** for focused implementation tasks
  whose acceptance criteria and verification commands are explicit. Raise Terra
  to high for parser, router, migration, or edge-case-heavy work.
- Use Terra low or medium for mechanical renames, fixtures, inventories,
  documentation synchronization, and straightforward tests.
- Prefer PR-sized tasks and the lowest reasoning effort that reliably passes an
  independent verification gate. Do not use stronger reasoning as a substitute
  for recorded tests or human promotion approval.

## Documentation

- `README.md` — product framing and quick start
- `docs/PRODUCT-FAMILIES.md` — umbrella boundary and all six user journeys
- `docs/META-ROUTER.md` — product category, authority model, and invariants
- `docs/ARCHITECTURE.md` — current request path and topology
- `docs/CONFIGURATION.md` — capability and metadata-authority configuration
- `docs/HOST-SERVICES.md` — supervisor/engine matrix, adoption, and portable lifecycle
- `docs/OPERATOR-PRIVACY.md` — public/private repository and evidence boundary
- `docs/THIN-CAPABILITY-GATEWAY.md` — meta-router request path and omissions
- `docs/OPENCLAW-INTEGRATION-SPEC.md` — harness ownership and capability aliases
- `docs/adr/0028-serving-benchmarks-and-thin-capability-gateway.md` — rationale
- `docs/adr/0039-capability-meta-router.md` — meta-router product decision
- `docs/adr/0042-anvil-serving-product-family-boundary.md` — umbrella/family decision
