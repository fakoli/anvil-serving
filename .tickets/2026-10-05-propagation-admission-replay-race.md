# Identical concurrent admission can be reported as a conflict

## Evidence and cause

The existing concurrent-admission test failed in Windows Python 3.11 CI with
`intent_conflict`. Admission performed an optimistic replay lookup and then a
separate request-existence read. An identical request committed by another
connection between those reads was mistaken for a conflicting contract.
The fixed test clock and contract are unrelated to this interleaving.

## Correction

Remove the redundant existence read. The existing `BEGIN IMMEDIATE`
duplicate-binding transaction checks the persisted request digest, returning
the original admission for an identical replay and refusing different bytes.
New-admission approval, active identity, generation, capacity and resource
gates are unchanged. No schema migration or live owner change is required.

## Verification

A deterministic regression commits through a second store immediately after
the first optimistic read. The identical case fails before the fix and passes
after it; the different-digest case remains refused. Both retain exactly one
intent, outbox row and request tombstone, with the original canonical bytes.
The existing threaded concurrency and lost-acknowledgement tests remain gates.
