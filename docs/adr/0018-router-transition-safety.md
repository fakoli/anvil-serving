# ADR-0018: Router transition safety

- Status: Accepted
- Date: 2026-07-12

## Context

The reference deployment is one workstation with two independently resident GPU serves. Fast
runs on the RTX 5090; Heavy runs on the RTX PRO 6000 and can take minutes to replace. Swapping
containers on request is not useful, and a cluster scheduler would not shorten model load time.
The router instead needs to stop new Heavy dispatch, let existing Heavy generations finish, keep
policy-eligible Fast traffic moving, and prevent a healthy port serving the wrong model from
entering rotation.

## Decision

The router owns process-local per-tier admission state. Admission check and active-count increment
share one lock; an idempotent lease covers the complete upstream iterator and releases from one
`finally` path. Quiesce rejects later acquisition as `skipped-quiesced`, without retry-budget or
circuit-breaker mutation. A condition-backed drain waits for zero active leases and aborts on its
positive bounded timeout without operating containers.

Promotion plans declare `affected_tiers`. Each affected tier must map to the target and rollback
serve in their respective router configs and opt into `model_identity = true`. Readiness first
checks the configured health path, then makes a time- and size-bounded authenticated
`GET /v1/models`; an advertised id must exactly equal the existing tier `model`. Health and
identity share one cached result and one invalidation path.

The guarded transaction is:

1. validate forward and rollback router artifacts and affected-tier mappings;
2. quiesce every affected tier and drain it before the first lifecycle command;
3. replace only the manifest-managed Heavy serve;
4. require direct health, exact model identity, and every declared preflight gate;
5. promote router config/profile and accept the existing router restart;
6. require post-restart router health and model-aware readiness.

Rollback uses the same order. Resume reasserts quiescence and reruns every gate, but can reuse an
already healthy target. The authenticated router boundary is projected through the existing CLI,
MCP, and generic controller transport; it does not create another state authority. The complete
`serves promote` transaction is remotely dispatchable and retains its confirmation, dry-run, and
human-approval gates.

## Consequences

- Fast remains loaded and eligible during a Heavy transition; Heavy-only work returns 503 while
  Heavy is quiesced or unavailable.
- The final router restart may briefly interrupt Fast connections, but does not stop or recreate
  the Fast model container.
- Process-local quiescence is intentionally lost on restart. Post-restart health plus exact model
  identity is the fail-closed recovery guard.
- `/v1/models` proves only the advertised name. Revision, weights, image, quantization, engine
  flags, reasoning controls, and quality still require manifests, fingerprints, and independent
  preflight evidence.
- No K3s, distributed lock, daemon, sidecar, persistent queue, or automatic model swapping is
  introduced.
- Live Fakoli Dark transitions remain separately explicit and human-gated.
