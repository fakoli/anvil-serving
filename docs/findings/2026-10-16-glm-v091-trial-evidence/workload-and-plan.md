# Workload and plan

R1 and R2 used the native six-check preflight, strict Oracle 18-case rounds,
the 18-check image suite (up to eight images per request), retrieval at 216307
actual prompt tokens, tool-context at 110760 actual prompt tokens, sampled GPU
reserve, and a pinned five-case SWE suite. Generation used default thinking
with max effort, temperature 0, and top-p 0.95.

The SWE control pins mini-swe-agent
`a83fcae82d2a08f0ee0c688f9d137b3566c097f8`, SWE-bench Verified dataset
`c104f840cc67f8b6eec6f759ebc8b2693d585d4a` with data hash
`a45b1fe4e2f0c8390b2b2938ac83e92ed5979000856808f3679c07812e9e6dcd`,
and independent SWE-bench grader `f7bbbb2ccdf479001d6467c9e34af59e44a840f9`.
It fixes five named cases, their image identities, one worker, 8 GiB memory and
swap limits, four CPUs, no task-container network, 16384 completion tokens,
60 steps, and a 21600-second job timeout.

R2 had to resolve at least four of five SWE cases before capacity, stability,
DFlash, or performance work could proceed. It resolved three. The fresh
matched incumbent control subsequently resolved the same three cases. Historical
4/5 evidence is not used as this campaign's baseline.

Source verification uses a short, clean temporary root with no Git ancestor and
a non-group-writable worktree root and parent. The final full suite passed from
that temporary root; focused harness checks passed after the permission
invariant was restored. The earlier temporary-root recovery is evidence custody,
not a reusable product fix.
