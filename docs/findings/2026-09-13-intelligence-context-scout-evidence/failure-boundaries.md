# Retained failure boundaries

This incomplete bundle preserves request-level failures in the sanitized native
artifacts. The 100-item quality scouts retain their exact-choice failures; no
quality score is recomputed here.

## Next strict120 canary gate

The native strict120 artifact records 120 received `stop` responses and 120
`request_canary_failed` exclusions. A separate direct-stream diagnostic found
two leading line-feed characters before each echoed canary. The strict checker
requires the canary at character zero, so these are a client/harness
formatting-contract failure, not a model crash, transport failure, or model
quality result. No performance claim is made from that run.

## GLM r7 route2 strict120 gate

The separate GLM r7 route2 strict120 artifact records 120 completed and
performance-eligible responses with zero failures. It has no matched
no-speculation control, so the retained timing fields do not establish a
speculative-decoding speedup.

## GLM r7 258K context stage

The retained r7 context stage completed nine `stop` responses at actual inputs
255,647–255,672 with a 65,536-token output reservation; seven passed. The
failed wrapper records only an oversized reasoning capture during result
serialization after the stage completed. Two model-result failures remain:
incorrect identifier retrieval at position 0.1 after the thinking guard and a
benign crosslink-retrieval refusal at position 0.1. Deterministic seed-17
fixture reconstruction matched both expected-answer digests before this public
record was made.

## Publication manifest collision

At 2026-09-14T01:31Z, the generated `artifact-manifest.json` unexpectedly
contained an `anvil-serving.benchmark-decision-summary/v1` document instead of
the artifact-set schema. The finalizer failed closed and did not overwrite it.
The observed corrupt payload SHA-256 was
`b35065f125a4ddec05ab468b5c8fe8401f0c321d2cf3fbee7bbebe3f9d3beb38`.
The generated path was replaced before its bytes could be copied to a diagnostic
file; this limitation is retained rather than reconstructed. No native evidence
was overwritten. The source ledger still targeted `artifact-manifest.json` and
was distinct from `summary.json`; regeneration twice from that ledger produced
the current matching artifact-set manifest. The overwrite origin remains
undetermined.

## GLM r7 matched MTP3/no-spec pair

Both strict120 C4 lanes completed 120/120 performance-eligible requests. The
reported shared-cache allocation differs (about 1.18 full 327K windows for
MTP3 and five for no-speculation), but neither allocation proves simultaneous
full-window correctness at C4. The no-spec context stage passed 9/9. Its completed fixed-100 quality scout retained 90/100 and its deep agentic suite retained 30/30; these do not substitute for a completed coding or restoration gate.


## GLM r7 no-spec SWE environment boundary

The first five-case no-spec SWE wrapper failed before model requests because its cached
Python environment inventory did not match: the running environment had
`anvil-serving` 1.2.1 while the recorded inventory required 1.1.0. The retained
derived diagnosis records that mismatch. The original frozen cache matches its
recorded inventory. The retained frozen retry completed 4/5 resolved, with five
graded submitted instances and zero grader errors; this initial wrapper remains
a harness failure rather than a model outcome.
