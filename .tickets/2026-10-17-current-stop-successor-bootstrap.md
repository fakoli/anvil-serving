# Receipt-bound startup after an acknowledged current-owner stop

Status: implemented; isolated validation recorded with the change.

A completed `current-instrumented-stop` could authorize a successor configuration
at a new read-only bind source, but ordinary `router up` compared that source to
the stopped predecessor. It held before startup despite the finalized native
death proof. Repeating ordinary death custody against the successor profile was
not a valid solution.

`router up --current-stop-receipt PATH` now binds the protected completed receipt
to its exact stopped incarnation, original acknowledgement time, retained native
marker, store, closure and death proof. It verifies the declared successor image,
Compose/config hashes, unchanged runtime and writable storage, and only permits
source changes for read-only mounts of the router configuration. The original
profile performs the existing fenced offline readback. The new owner starts
closed through existing native transfer logic; readmission stays separate.

Regression coverage includes changed configuration bytes and bind source,
read-only preview, expired completed approval, drift/refusal cases, and the
opt-in isolated Docker maintenance roundtrip. Generic mount guards are unchanged.
