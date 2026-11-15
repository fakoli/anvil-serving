# Workload manifest

The campaign's quality decision used five fixed SWE-bench Verified tasks, a pinned agent configuration, and an independent official grader. A qualifying candidate needed all five submissions graded, at least four resolved tasks, and resolved Django and Pylint sentinels. Partial runs retain their fixed denominator and are not re-ranked by graded-only percentages.

Baseline capacity cells used controlled output and request-canary validation. The retained cells are 32K/C1 (four requests), 32K/C4 (eight requests), 128K/C1 (three requests), and 256K/C1 (three requests). They measure only their native model/runtime/configuration and actual prompt-token populations. No sample is large enough for a stable p99 claim.
