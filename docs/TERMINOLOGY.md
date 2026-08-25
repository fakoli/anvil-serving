# Terminology

| Term | Definition |
| --- | --- |
| `anvil-serving` | Product, package, CLI, and documentation name. |
| Capability meta-router | The product category: a stable capability contract mapped explicitly to one tier, while configured or inference-owned metadata describes the selected serve. It does not imply automatic model selection. |
| Thin capability gateway | The request-path implementation of the capability meta-router: token authentication, protocol translation, readiness, admission, streaming, and relay without an intent classifier or fallback chain. |
| Direct alias | A `model` value declared in `[router.model_routes]`, such as `llm.primary` or `llm.voice`. |
| Tier | A configured local backend endpoint selected by one direct alias. |
| Metadata authority | The owner of mutable served-model facts for a tier: router configuration in `configured` mode or the selected inference service in `upstream` mode. |
| Served configuration | The allowlisted model, context, engine, quantization, slot, modality, or build facts configured for or observed from one selected tier. |
| Purpose model | A named embedding, rerank, STT, TTS, OCR, or vision endpoint exposed through its dedicated API surface. |
| Serve | A concrete model process managed and inspected with `anvil-serving serves`. |
| Preflight | Functional compatibility checks against one served model. |
| Benchmark | Repeatable performance or quality measurement recorded as evidence; it does not change routing automatically. |
| Transition | Quiesce, drain, or readmit an explicit local tier. |

Use **direct alias** for a caller-facing model name and **served model** for the backend's
advertised model id. Use **benchmark evidence** rather than profile or policy terminology.
