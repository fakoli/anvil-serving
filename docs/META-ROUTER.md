# Capability meta-router

The Capability Gateway in Anvil Serving is a **capability meta-router** for
local inference. It gives callers stable capability names while allowing the
concrete serve behind one configured tier to change without teaching every
client about model versions, quantization labels, engine names, or context
settings.

The term describes an authority boundary, not an automatic model chooser.
Every accepted chat request still resolves through an exact, closed route:

```text
caller alias -> configured tier -> that tier's configured endpoint
```

The selected endpoint may supply bounded metadata about what it currently
serves. It cannot make the router select a different endpoint, retry another
model, or reinterpret the caller's intent.

## The three identities

The Capability Gateway keeps three identities separate:

| Identity | Example | Stability | Owner |
| --- | --- | --- | --- |
| Capability alias | `llm.secondary` | Stable caller contract | Router configuration |
| Tier | `secondary-local` | Stable route and policy boundary | Operator configuration |
| Served configuration | model id, context, engine, quantization, slots, modalities | May change when the serve changes | Router configuration or the selected inference service |

This separation is the reason a caller can keep using `llm.secondary` after an
operator replaces a Qwen build or changes its context on the same endpoint.
The route does not become dynamic; the description of the selected serve stays
truthful.

## Authority model

Each plane has one job:

| Plane | Owns | Does not own |
| --- | --- | --- |
| Caller | The requested capability alias and request payload | Tier selection, serve lifecycle, or metadata authority |
| Operator configuration | Alias-to-tier mapping, endpoint, auth reference, dialect, capability policy, safety ceilings, readiness contract | Inference-engine runtime facts in upstream-owned mode |
| Inference service | Its active single-model identity and allowlisted runtime configuration when `metadata_source = "upstream"` | Public alias, tier mapping, router policy, promotion, or fallback |
| Router | Authentication, alias closure, metadata validation, admission, protocol translation, streaming relay, and metadata-only decisions | Intent classification, candidate ranking, lifecycle, or quality claims |
| Evaluation and promotion | Evidence that a concrete serve satisfies a capability and the guarded transaction that changes exposure | Per-request routing or silent config mutation |

An authority may report facts only within its boundary. For example, an
inference service may report `n_ctx = 262144`; it may not claim that tools are
allowed if the router policy disables them, and it may not redirect
`llm.secondary` to a different tier.

## Request path

For a chat request, the Capability Gateway performs these steps:

1. Authenticate the caller when front-door authentication is configured.
2. Normalize the supplied `model` value and require an exact entry in
   `[router.model_routes]`.
3. Resolve that alias to its one configured tier.
4. Resolve the tier's effective served configuration from router config or
   bounded metadata from that tier's one inference service.
5. Fail closed if required metadata is missing, ambiguous, malformed, or
   internally inconsistent.
6. Enforce router-owned context, tool, media, output, readiness, and
   concurrency rules.
7. Relay the request to the already-selected endpoint and return its ordinary
   or SSE response.

The route is decided at step 3. Metadata resolution at step 4 cannot revisit
that decision.

## Two metadata-authority modes

### Configured

`metadata_source = "configured"` is the default. The tier declares its served
model and context in router configuration. This is appropriate when the router
config and inference service are released as one coordinated artifact.

```toml
[[router.tiers]]
id = "primary-local"
base_url = "http://127.0.0.1:30000/v1"
model = "served-model-name"
context_limit = 131072
dialect = "openai"
metadata_source = "configured"
privacy = "local"
tool_support = true
auth_env = "ANVIL_PRIMARY_LOCAL_KEY"

[router.model_routes]
llm.primary = "primary-local"
```

### Upstream

`metadata_source = "upstream"` delegates mutable served-model facts to the
single OpenAI-compatible inference service already selected by the tier. The
router reads exactly one `/v1/models` entry and, for supported llama.cpp
services, a bounded `/props` response. It caches the validated result only for
the configured readiness interval.

```toml
[[router.tiers]]
id = "secondary-local"
base_url = "http://100.64.0.10:39038/v1"
dialect = "openai"
metadata_source = "upstream"
privacy = "local"
tool_support = true
auth_env = "ANVIL_SECONDARY_LOCAL_KEY"
health_path = "/health"
max_concurrency = 1

[router.model_routes]
llm.secondary = "secondary-local"
```

The tier intentionally omits duplicated model, context, engine,
quantization, identity, and fingerprint values. Replacing the serve at that
endpoint can update effective metadata after the cache interval without a
router edit or restart. The caller alias, tier, endpoint, and safety policy do
not change.

## Invariants

The capability meta-router keeps these invariants:

- The chat alias vocabulary is closed and explicit.
- One alias maps to exactly one configured tier.
- One accepted request has exactly one selected tier.
- Upstream metadata comes only from the already-selected tier.
- Missing or conflicting required metadata makes that tier unavailable.
- A failed request is never replayed against another model.
- Router-owned safety policy is never inferred from a model card.
- Readiness is not qualification, and configuration is not promotion.
- Purpose-model and audio routes stay on their separate deterministic
  surfaces.

Named media workflows are another deterministic capability family, not chat
aliases. MCP and A2A callers choose an explicit workflow whose operator-owned
mapping names exactly one media service and resource owner. Durable job
handling is downstream of that choice and cannot infer or retry a different
workflow, model, host, or provider.

## What meta-router does not mean

The Capability Gateway's request path does not:

- classify a prompt or infer a work class;
- rank models, providers, or endpoints;
- choose a model from runtime capacity or benchmark scores;
- fall back, escalate to cloud, or retry on another tier;
- verify a response and substitute a second answer;
- start, stop, or promote a serve from the request path; or
- let an inference service rewrite a public capability contract.

It also does not accept arbitrary ComfyUI graphs or give MCP/A2A protocol
handlers placement, lifecycle, or GPU-reservation authority. Those boundaries
are defined in [ADR-0040](adr/0040-media-gateway-and-controller-authority.md).

Those omissions are deliberate. They make request behavior inspectable and
keep model promotion in a human-gated, evidence-backed operator transaction.

## Thin gateway and meta-router are complementary

**Capability meta-router** is the Capability Gateway contract: the stable
authority model across callers, operator configuration, inference services,
and evidence.

**Thin capability gateway** is the implementation style: a small stdlib-only
request path that authenticates, translates, validates, admits, streams, and
relays without taking on model-selection or lifecycle authority.

ADR-0028 established the thin direct gateway after removing the former
intent-driven router. ADR-0038 added inference-owned metadata behind a stable
alias. [ADR-0039](adr/0039-capability-meta-router.md) names the resulting
gateway contract and makes its invariants explicit.

## Operational consequence

Changing a model at an upstream-owned endpoint no longer requires a router
metadata edit, but it still requires the normal operational discipline:

1. manage the candidate through a recorded serve recipe or manifest;
2. qualify the concrete endpoint with preflight and benchmark evidence;
3. make any alias-to-tier change through the guarded promotion/config path;
4. verify the router's effective metadata and real clients; and
5. keep package publication separate from live deployment.

See [Configuration](CONFIGURATION.md),
[Architecture](ARCHITECTURE.md), and the
[meta-router request path](THIN-CAPABILITY-GATEWAY.md).
