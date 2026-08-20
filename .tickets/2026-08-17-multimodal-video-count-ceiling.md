# Multimodal benchmark needs an explicit multi-video ceiling

## Observed

On 2026-08-17, a managed Qwen3.8 qualification recipe declared an engine-side
limit of two videos per request. The hash-pinned multimodal corpus runner still
rejected a two-video case because its one-video ceiling was hard-coded and had
no recorded command-line override.

## Impact

The managed qualification surface could not independently prove the recipe's
declared video-count boundary. An ad hoc request would not retain the corpus
hash, exact runtime identity, deterministic assertions, or fail-closed evidence
contract required for publication.

## Resolution

Add `--max-videos-per-request`, defaulting to one and bounded at sixteen. Apply
the ceiling while loading the corpus and retain it in both dry-run plans and
completed evidence artifacts. This changes only benchmark admission; it does
not alter serving or router policy.

## Acceptance

- The default remains one video per request.
- A two-video corpus is admitted only when the operator explicitly selects two.
- Values outside one through sixteen fail before any endpoint request.
- The selected ceiling is present in dry-run and completed evidence.
- Unit tests cover the default, explicit ceiling, and hard bound.
