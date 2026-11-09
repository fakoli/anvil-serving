# TensorFold recipe cannot use truthful serve manifest metadata

Status: fixed, pending verification and delivery.

During a managed configuration trial, an existing TensorFold recipe could not be represented in a serves manifest: the engine vocabulary rejected `tensorfold`. Omitting the field inferred `sglang`; `none` correctly required a non-inference service. This prevented truthful retained-container lifecycle and functional probes.

Accept explicit TensorFold metadata and dispatch its declared OpenAI-compatible text/image probes through the existing bounded adapter. Route selection remains unchanged. Regression: `tests/test_serves_tensorfold.py`.

The first exact retained-container preview also refused a matching TensorFold launch because served-model extraction did not recognize `--name`. The shared argv parser now supports that flag while preserving precedence for `--served-model-name`; exact command, recipe, image and container fences remain intact.
