# Managed router cannot recover an automatically restarted incarnation

Status: recovery implementation qualified in owned fixtures; live recovery is not activated.

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
without opening SQLite or claiming ledger correlation. The later ledger diagnostic adds native ledger/coverage
correlation and exclusion of a partial successor before a recovery operation. A host reboot or operator flag alone
does not supply that proof. Remote outcome uncertainty must remain visible.

The optional `--ledger` diagnostic now correlates a bounded native metadata
snapshot against the same protected sidecar set. It reports incomplete or
unanchored rows, domain/configuration differences and transfer inconsistencies.
Its isolated incumbent-image helper uses existing reader custody, query-only
SQL and fixed aggregate output. It does not write application state or grant
recovery; normal SQLite WAL/SHM housekeeping is possible. The managed `reboot-stop` phase now holds actual producer and writer exclusion
through exact previous-boot correlation, physical stop and staged proof commit.
Its v2 record preserves the immutable original live anchor, binds the real changed
stopped incarnation, and retains the acknowledged intent for safe retries after
interruption or expiry. Guarded startup consumes this proof and starts CLOSED;
readmission still needs separate authorization. Old runtimes refuse v2 records.

Qualification includes 43 deterministic cases plus an isolated Docker restart
loop and named volume. The Docker case synthesizes only historical boot metadata;
actual restart identity, locks, proof commit and CLOSED successor transfer are
native. This is not a host reboot qualification. The selected live model and
router state have not been changed. Exact-source packaging, CI, installation,
operator authorization, live recovery and readmission remain separate gates.
