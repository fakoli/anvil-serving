# Source-verified Flare served-name/SSE model-field mismatch

Observed 2026-10-22 in the pinned 47581cca runtime with common instrumentation.
`pilot-a-correctness-r2/http/000-response.json` retains the first real SSE event and
the original failed fixture check. Requested model was the configured served alias;
`/v1/models` advertised that alias, while SSE `model` was exact package source revision
`dec48abd33efa73c3bb7c95b74eee10cad34f9be`. This was a response-identity compatibility
failure before semantic output evaluation, not an allocator result.

Source chain: `flare/src/main.rs:192` sets `served_names` without updating `model_id`;
`helios/src/server/models.rs:30` chooses the served alias for the catalog;
`helios/src/server/http.rs:327` initializes the encoder with native `model_id`, and
line442 also uses that identity in raw observed-token chunks. The source behavior
explains the retained event. No engine patch belongs in this allocator comparison.

The bounded harness first requires exact served alias from `/v1/models` plus the
managed running-inventory container/registry/recipe proof. Only then it permits
SSE `model` to equal that alias or the exact proven loaded source revision. Missing
proof keeps the original strict alias check. Arbitrary alternate identities remain
errors. `check.py` reuses the retained real SSE metadata and checks unproven rejection,
proven revision compatibility, and corrupt-proof rejection. Original failed/cancelled
pilot receipts remain intact. Include this separate protocol issue in any proposed
upstream contribution discussion; no submission has occurred.
