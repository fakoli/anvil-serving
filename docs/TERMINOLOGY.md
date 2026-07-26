# Terminology

| Term | Definition |
| --- | --- |
| `anvil-serving` | Product, package, CLI, and documentation name. |
| Thin capability gateway | The token-authenticated, protocol-compatible service that maps a declared capability alias to one local endpoint. |
| Direct alias | A `model` value declared in `[router.model_routes]`, such as `llm.primary` or `llm.voice`. |
| Tier | A configured local backend endpoint selected by one direct alias. |
| Purpose model | A named embedding, rerank, STT, TTS, OCR, or vision endpoint exposed through its dedicated API surface. |
| Serve | A concrete model process managed and inspected with `anvil-serving serves`. |
| Preflight | Functional compatibility checks against one served model. |
| Benchmark | Repeatable performance or quality measurement recorded as evidence; it does not change routing automatically. |
| Transition | Quiesce, drain, or readmit an explicit local tier. |

Use **direct alias** for a caller-facing model name and **served model** for the backend's
advertised model id. Use **benchmark evidence** rather than profile or policy terminology.
