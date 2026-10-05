# Bound managed SWE task and official grading containers

Co-resident coding qualification exposed a product gap: mini-SWE task containers
disabled network but had no memory/CPU/process ceiling, and the pinned official
grader supplied neither network isolation nor resource limits to Docker create.

The managed mini-SWE overlay now requests 8 GiB memory and total memory-plus-swap,
four CPUs, 512 processes and no network. A small compatibility launcher verifies
the pinned official grader revision and source hashes, guards its Docker SDK
create seam, rejects conflicting or unsupported container options, and checks
the daemon's returned limits before the unchanged grader can start a container.
The adapter digest, source hashes and limits are retained in native plans/results.
The Docker SDK remains solely in the existing isolated grader environment.

The same review found that a prepared dataset revision was not passed to the
running harnesses. Both now load the exact same local data directory, verify
its pinned Parquet digest before each stage, and reject unexpected data files.
New asset checkouts disable automatic CRLF conversion to preserve source hashes.
The grader uses prebuilt official images; automatic image builds are refused.

Validation: focused adapter tests cover conflicting options, changed source,
failed daemon enforcement, cleanup, and official-grader argument passthrough.
Live generated-code execution still requires an independent review and a
contained smoke. Co-resident results must retain shared-host performance and
isolation caveats; synthetic agentic fixtures are not executed coding evidence.
