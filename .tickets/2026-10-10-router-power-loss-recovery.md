# Managed router cannot recover an automatically restarted incarnation

Status: diagnosis in progress; no recovery authorization or activation.

After a reported host power loss, the router repeatedly exited before listening
with `KeyStoreError: router container custody unavailable` at
`container_owner.ready_transfers` phase validation. Docker retained the same
container/image but advanced its start identity through automatic retries.
The selected model remained running. Managed router logs could not retrieve the
startup failure; a bounded read-only Docker log projection supplied the stack.

Existing managed cold startup accepts an exactly anchored stopped incarnation.
It deliberately rejects changed incarnations, unanchored successors and unknown
native ownership. The crash-looping target also cannot supply the live drain
required by ordinary restart. Do not weaken these gates, edit the ledger or
custody records, fabricate an old stopped identity, or restore stale accounting.

The first change supplies a read-only `router recovery-status` diagnostic with
fixed aggregate output. It inspects protected sidecars and actual mount metadata,
without opening SQLite or claiming ledger correlation. Native ledger/coverage
correlation and exclusion of a partial successor are still required before any
new recovery path is designed or authorized. A host reboot or operator flag alone
does not supply that proof. Remote outcome uncertainty must remain visible.
