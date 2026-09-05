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
