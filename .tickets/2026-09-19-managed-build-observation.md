# Managed Compose build evidence is not exposed while building

`serves up` captures Compose output until completion and has no bounded build
progress or image-file provenance surface. The qualification campaign used
read-only BuildKit history logs for the exact managed build and copied only
`/opt/provenance` from its exited CPU-only helper. All lifecycle mutations
remained managed. The first helper was mistakenly modeled as a healthy serve,
causing an irrelevant port-zero readiness wait after a successful image build.
The corrected repeatable path is `serves up --compose` for the CPU-only helper;
the model itself has a separate pinned recipe and real readiness gate.

Add typed build progress and declared image provenance retrieval. Build limits
must describe the builder, not the later helper container: this build used four
compiler jobs inside the existing 20 GiB WSL VM ceiling. Compose service CPU/RAM
limits applied only to the exited `/bin/true` helper.
