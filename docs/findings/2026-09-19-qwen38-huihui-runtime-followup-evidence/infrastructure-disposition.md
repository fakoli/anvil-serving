# Deployment and observability disposition

No live model assignment, router alias, Workbench card, Grafana mapping or ai-infra deployment source was changed. The candidate is operational as an isolated managed recipe, but it misses the requested footprint improvement and is not promoted.

The earlier campaign inspected the ai-infra benchmark importer, inventory-driven model-switch workflow, and allowlisted Anvil configuration capture. See the [retained infrastructure inspection](../2026-09-19-qwen38-huihui-ninfer-evidence/infrastructure-disposition.md). That inspection is not a deployment validation for this corrected runtime.

The new recipe verifies model bytes, source revision and the exact cached executable SHA at startup. Managed MTP8K and32K reloads succeeded with that SHA. Apt resolution and linked libraries are not an immutable runtime image, so clean recreation remains unproven. No claim is made that these source-build recipes satisfy production repeatability.

Before any separate speed-focused promotion: bake and pin the complete runtime, prove clean managed recreation/rollback, add a truthful NInfer telemetry adapter, update private inventory through ai-infra, validate Workbench and Grafana identity, and test real routed clients. None is silently replaced with a different engine label. Media restoration is verified independently in restoration.json.
