# Terminology

| Term | Definition |
| --- | --- |
| Anvil Serving | Umbrella product, package, CLI, documentation site, and release line for seven explicit local-AI product families. |
| Product family | One stable user outcome and authority boundary inside Anvil Serving. Every operational root command belongs to exactly one. |
| Model Serving | Family that owns artifacts, recipes, serve lifecycle, reservations, switching, and guarded promotion. |
| Capability meta-router | The Capability Gateway contract: a stable capability alias maps explicitly to one tier, while configured or inference-owned metadata describes the selected serve. It does not imply automatic model selection. |
| Capability Gateway | Family that implements the capability meta-router request path. |
| Evaluation & Evidence | Family that owns preflight, routed acceptance, benchmarks, and retained evidence without automatic promotion. |
| Anvil Voice | Family for STT, TTS, realtime proxy lifecycle, profiles, and voice qualification. |
| Anvil Media | Family for named image/video workflows, durable jobs, cancellation, qualification, and opaque artifacts. |
| Anvil Connect | Family that publishes local loopback applications to browsers through a self-hosted edge with your own OIDC provider. See [Anvil Connect](CONNECT-OVERVIEW.md). |
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
| OpenClaw | Optional agent harness that uses the gateway as an OpenAI-compatible provider by sending a capability alias. See the [integration contract](OPENCLAW-INTEGRATION-SPEC.md). |
| Hermes | Optional agent client whose profiles and media-only MCP catalog `anvil-serving harness sync` can reconcile. See [Configuration](CONFIGURATION.md). |
| NInfer | One compatible local serving engine that can back a tier; the router sees only the declared endpoint contract. See [Benchmarks](BENCHMARKS.md). |
| Pi runner | Optional isolated Pi agent runner built with `anvil-serving workbench build --runner pi`. See [Anvil Workbench portal](WORKBENCH-PORTAL.md). |
| Anvil Workbench | This repository's built-in dashboard, served with `anvil-serving dashboard serve` on loopback port 8768 (UI title "Anvil System View"). See [Host and setup CLI](cli/host.md). |
| Grafana Fleet | Optional, separate Grafana/Prometheus stack kept in the operator repository (Grafana on loopback port 3001), with dashboards titled "Fakoli · ...". Not part of this repository. |
| Operator repository | Private repository selected through `ANVIL_SERVING_HOME` that holds real topology, route assignments, and working evidence. See [Public product and private operator state](OPERATOR-PRIVACY.md). |

Use **capability alias** for a caller-facing model name and **served model** for the backend's
advertised model id. Use **benchmark evidence** rather than profile or policy terminology.
Use **Anvil Serving** for the umbrella and the exact family name when discussing
one authority domain.
