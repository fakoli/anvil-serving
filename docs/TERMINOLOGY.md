# Terminology

| Term | Definition |
| --- | --- |
| Anvil Serving | Umbrella product, package, CLI, documentation site, and release line for six explicit local-AI product families. |
| Product family | One stable user outcome and authority boundary inside Anvil Serving. Every operational root command belongs to exactly one. |
| Model Serving | Family that owns artifacts, recipes, serve lifecycle, reservations, switching, and guarded promotion. |
| Capability meta-router | The Capability Gateway contract: a stable capability alias maps explicitly to one tier, while configured or inference-owned metadata describes the selected serve. It does not imply automatic model selection. |
| Capability Gateway | Family that implements the capability meta-router request path. |
| Evaluation & Evidence | Family that owns preflight, routed acceptance, benchmarks, and retained evidence without automatic promotion. |
| Anvil Voice | Family for STT, TTS, realtime proxy lifecycle, profiles, and voice qualification. |
| Anvil Media | Family for named image/video workflows, durable jobs, cancellation, qualification, and opaque artifacts. |
| Control Plane & Fleet | Family for topology, typed remote dispatch, host utilities, integrations, observability, and fleet state. |
| Thin capability gateway | The request-path implementation of the capability meta-router: token authentication, protocol translation, readiness, admission, streaming, and relay without an intent classifier or fallback chain. |
| Capability alias | A caller-facing `model` value declared in `[router.model_routes]`, such as `llm.primary` or `llm.voice`. |
| Tier | A configured local backend endpoint selected by one capability alias. |
| Metadata authority | The owner of mutable served-model facts for a tier: router configuration in `configured` mode or the selected inference service in `upstream` mode. |
| Served configuration | The allowlisted model, context, engine, quantization, slot, modality, or build facts configured for or observed from one selected tier. |
| Purpose model | A named embedding or rerank model exposed through its dedicated API surface. Image, OCR, and video remain explicitly mapped chat capabilities; STT and TTS use audio routes. |
| Serve | A concrete model process managed and inspected with `anvil-serving serves`. |
| Preflight | Functional compatibility checks against one served model. |
| Benchmark | Repeatable performance or quality measurement recorded as evidence; it does not change routing automatically. |
| Transition | Quiesce, drain, or readmit an explicit local tier. |

Use **capability alias** for a caller-facing model name and **served model** for the backend's
advertised model id. Use **benchmark evidence** rather than profile or policy terminology.
Use **Anvil Serving** for the umbrella and the exact family name when discussing
one authority domain.
