# Close benchmark workload snapshot boundary regressions

Status: open; workload-visibility:T003.1 independent review.

A JSON timestamp containing a valid-looking prefix, embedded NUL and one million
suffix characters bypasses the SQL character-count guard: SQLite length(TEXT)
stops at NUL, so the Python scalar receives 1,000,021 characters. The source is
eventually partial, but that does not satisfy the bounded-scalar contract.
Guard the complete encoded value and pass only a genuinely bounded scalar;
retain fixed invalid-row quarantine and never send a full record to Python.

Review also checks the single deadline after a short query that does not reach
the progress callback, active-only exclusion of provably nonactive malformed
rows, canonical bounded selection and honest omitted counts for quarantined
rows. Add regressions before accepting this source implementation. Existing
green happy-path/concurrency tests are not sufficient proof of these boundaries.

Independent closed-writer WAL fixture inspection also reproduced SQLite-managed
`-wal`/`-shm` creation by `mode=ro`. The approved parent contract now explicitly
means logical read-only: no main-database/schema/content/lifecycle writes, with
SQLite coordination sidecars allowed for an existing database. Missing main
storage must still stay absent. Do not disable locking or assert immutability
on a live mutable database. Add byte/schema/row invariance coverage and retain
the coherent concurrent-writer regression. This was a contract ambiguity, not
permission to mutate owner state or manually remove WAL files.
