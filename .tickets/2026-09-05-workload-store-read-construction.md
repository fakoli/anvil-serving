# Read workload stores without initializing writable owners

Status: T012.1 and T012.2 implemented candidates; consolidated acceptance pending.

Node aggregation must read existing benchmark and media state even when it
does not own a live in-process store. BenchmarkJobStore construction creates
a run directory; MediaJobStore construction initializes SQLite schema and
WAL state. Reusing those factories in a workload query would violate the
read-only contract before the existing read-only list_workloads method ran.

Extract each projection into its owning module's standalone read function.
Keep instance methods as delegators with their actual locks and clocks.
Do not add another SQL owner, an uninitialized writable object, immutable
SQLite mode, new lifecycle behavior, or filesystem creation on visibility.
Prove missing-path non-creation and live-WAL output parity with literal tests.

Source composition, router credentials, node deadlines and aggregate caps
are separate node-collector design work, not part of these two refactors.

Packet preparation caught the installed State parser truncating wrapped
acceptance bullets. Before execution, the unused claim was released and the
two new tasks' acceptance bullets were made single-line for full extraction.
This is PRD compatibility formatting, not a change to the acceptance contract.

T012.1 candidate 7e9546ff289c69c594f0c75291d32b12a772253d passed71 tests
and Ruff after commit, recorded as EVB6FCC86B. T012.2 candidate
51a7bafe867408f3be2733917068f762cf7261e1 passed36 tests and Ruff after
commit, recorded as EV1C2891C3. Missing-path non-creation and live-WAL reads
are covered. No lifecycle, deployment or final acceptance is claimed.
