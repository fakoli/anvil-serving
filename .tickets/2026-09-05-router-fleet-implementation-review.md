# Router and fleet implementation review corrections

Status: in progress
Priority: P1
Date: 2026-09-05
Scope: qualified-replica-sets, fleet-node-enrollment, workload-visibility

## Why this change exists

The autonomous router/fleet build requires independently verified contracts before
downstream components trust them. Initial implementations passed their focused
tests but independent adversarial probes exposed validation and privacy gaps.
The operator explicitly authorized ticketed fix-forward refactors and breaking
changes to these unshipped interfaces. No correction grants model promotion or
changes live routing by itself.

## Corrections and evidence

- Replica configuration, resolved in `0e370b6e2f2617ef2f27222f2e7a97e5f24feed8`:
  enforce a closed direct/replica endpoint union, immutable declared identity,
  independent duplicate checks, offline endpoint normalization, malformed URL
  rejection, and post-IDNA loopback-name rejection. Unicode category-C and
  whitespace input cannot survive normalization. Required parser tests: 49 passed;
  parser plus dynamic metadata regression tests: 66 passed; Ruff passed.
- Replica readiness, resolved in `19e3bae76f3b03fcfb59b031ab3f3b1e1119c495`:
  isolate member cache/locks and fence invalidation generations; require valid
  declared provenance before network I/O, and bound untrusted HTTP error codes.
  Twelve correction regressions failed before the fix; final availability tests:
  35 passed, dynamic metadata: 17 passed, Ruff passed. Independent review and
  strict task application passed. This does not yet wire member dispatch.
- Scoped authorization core, resolved in
  `7f4c71e6505ea7076efe1507d22bc8786158a4ef`: bounded secret loading and fixed safe
  errors, immutable public metadata without serializable credential material,
  constant-time candidate comparisons, no-follow local file validation,
  duplicate credential rejection, and strict argument validation. Required tests:
  32 passed; broader authorization/config/controller regression: 150 passed,
  1 skipped; Ruff passed. HTTP and CLI wiring remain separate open tasks.
- Workload schema, open: refactor owner/state/quality compatibility, unknown-state
  projection, freshness validation, source identity/timestamp integrity, truthful
  truncation, aggregate limits, safe typed failures, bounded query parsing, exact
  timestamp ordering, stale visibility, and bounded canonical round trips.
  The initial 17 passing tests did not establish acceptance. A second review
  caught seven additional contract gaps: opaque cross-node IDs, safe host IDs,
  exact wire fields, media lifecycle mapping, scalar state filtering, truthful
  partiality, bounded sequence failures, and duplicate-key JSON rejection.
  The corrective implementation now has 51 passing tests and Ruff passes;
  independent re-review and post-commit acceptance remain required. The original
  focused executor and stronger corrective author both missed wire-contract
  drift; passing either model's own tests is not independent acceptance.
- Replica admission, open: enforce bounded copied configuration/readiness,
  strict member eligibility, compound lease invariants, direct snapshot ordering,
  and event-controlled concurrency coverage. Review rejected silent counter
  divergence and unbounded external iterable materialization before downstream
  request dispatch can use this API.
- Controller authorization wiring, open: add pre-body wrong-scope rejection,
  unread-body connection closure, keepalive principal reset, store-access guards,
  and response/audit/persisted-result credential redaction. The 95 focused tests
  pass; independent review and post-commit acceptance remain required.

## Acceptance and closure

Each corrected task must have passing post-commit command proofs, independent
review, and strict Anvil State application. Integrate the original source commits
so proof ancestry remains checkable. Keep this ticket open until the workload
corrections are accepted. Runtime integration, full-product tests, deployment,
and real-client acceptance remain additional gates in the owning PRDs.

## Verification commands

```console
python scripts/run_tests.py tests/router/test_config.py tests/router/test_dynamic_upstream_metadata.py -x -q
python scripts/run_tests.py tests/test_control_plane_authorization.py -x -q
python scripts/run_tests.py tests/observability/test_workloads.py -x -q
python -m ruff check anvil_serving/router/config.py anvil_serving/control_plane/authorization.py anvil_serving/observability/workloads.py
```
