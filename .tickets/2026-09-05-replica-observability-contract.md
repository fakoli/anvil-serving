# Distinguish replica readiness, admission and declared provenance

Status: implementation awaiting independent review
Priority: P1
Date: 2026-09-05
Task: qualified-replica-sets:T005

## Reason for change

Existing model projections assume one endpoint per logical tier. Applying them
to the new closed replica shape would probe the empty logical URL sentinel,
show the tier as unavailable despite ready members, or mistake a single
endpoint's configured KV estimates for aggregate replica capacity.

## Implementation contract

- Keep one alias and logical tier; expose at most 16 ordered member IDs,
  qualification references, fixed readiness codes and exact-match served identity.
- Mark deployment provenance as declared and runtime deployment identity as
  unverified. A qualification reference does not prove evidence passed.
- Read aggregate/member in-flight counts from one atomic admission-owner snapshot.
  An absent or inconsistent owner is unavailable, not idle. The projection API
  accepts the owner explicitly; runtime wiring belongs to the later dispatch task.
- Preserve shared per-request limits. Do not fetch metrics using the logical URL
  sentinel, multiply KV estimates, or claim qualified aggregate throughput.
  Replica live metrics explicitly remain unavailable/not aggregated in this slice.
- Exclude endpoints, topology identities, auth material, unexpected observed models,
  free-text admission reasons, arbitrary readiness strings and raw exceptions.
- Include safe membership/provenance changes in the public config fingerprint,
  without hashing private endpoint values into that public projection.

## Verification so far

Focused metadata/capacity tests: 33 passed. Existing observability hardening:
5 passed. Ruff and diff checks passed. A negative control that restored an
unexpected upstream model to a member projection failed the identity/redaction
assertion as expected. Independent review, post-commit State proofs, publication
and runtime/deployment acceptance are not yet complete.
