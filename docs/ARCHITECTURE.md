# Architecture

anvil-serving is a serving and benchmark substrate with a thin capability
gateway. It owns repeatable lifecycle and evidence around local model serves,
not semantic model selection.

```mermaid
flowchart LR
    C["client or harness"] --> A["authenticated front door"]
    A --> D["dialect translation"]
    D --> R{"explicit model alias"}
    R -->|"llm.primary"| H["RTX PRO 6000 / heavy-local"]
    R -->|"llm.voice"| F["RTX 5090 / fast-local"]
    R -->|"unknown"| X["404"]
    H --> S["SSE / normalized response"]
    F --> S
    S --> L["metadata-only DecisionLog"]
```

## Request boundary

The router accepts Anthropic Messages, OpenAI Chat Completions, and the
supported stateless Responses subset. It authenticates the caller, translates
the request and tools for the selected upstream dialect, enforces the selected
tier's context/tool/readiness/admission constraints, then relays ordinary or
SSE responses. There is exactly one selected tier per accepted chat request.

The front door also has deterministic purpose-model endpoints for embeddings
and reranking, plus normalized audio endpoints. They have separate
operator-configured route tables and never join the chat route vocabulary.

## Capability topology

The reference split assigns primary LLM candidates to the RTX PRO 6000 and
low-latency voice LLM, STT/TTS, embeddings, reranking, and optional ComfyUI to
the RTX 5090. Fakoli Mini remains model-free in the reference voice topology;
its loopback audio proxies forward to Dark rather than hosting a model.

`serves` manages compose-backed model lifecycle and GPU reservations.
`eval preflight` and benchmark commands qualify a concrete endpoint. The
gateway can only expose a configured capability. It neither proves model
quality nor promotes a new recipe.

## Deliberate non-components

The gateway has no workload classifier, intent presets, quality profile,
policy engine, residency selector, verification chain, circuit-breaker
fallback, cloud route, or routing calibration loop. A selected tier that cannot
serve returns an error rather than changing the request's capability.

## Evidence and observability

`DecisionLog` records routing metadata without request/response content:
normalized alias, selected tier, timing, token counters where available, and
terminal outcome. It provides audit evidence, not a feedback signal for future
routing. Benchmark results and preflight artifacts remain the source of truth
for changes to the configured mapping.
