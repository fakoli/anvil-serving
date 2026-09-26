# Detached SWE containers lack managed cancellation cleanup

**Status:** Open product gap.

## Observed boundary

The pinned mini-swe-agent revision
`a83fcae82d2a08f0ee0c688f9d137b3566c097f8` starts a Docker environment with a
random `minisweagent-*` name, `--rm`, and a two-hour `sleep` command. `--rm`
removes that container only after it exits.

Managed benchmark cancellation retains native outputs, then verifies and stops
the owned local POSIX worker process group. A Docker-daemon-owned container is
outside that process group. The pinned adapter's cleanup is best-effort: it is
triggered from object destruction, starts a background stop/remove command, and
does not wait for or verify removal. Its SWE batch path has no explicit signal
or exit cleanup. A worker terminated during cancellation can therefore leave a
detached container running until its sleep timeout.

The existing SWE cleanup operation removes the owned work directory only. It
does not have a run-scoped container identity and cannot identify, stop, or
verify a detached Mini-SWE container.

Detailed investigation evidence remains in the private campaign records.

## Safety policy

Do not describe or invoke benchmark cancellation as container-clean. The
current qualification campaign must finish under its frozen bounded budget
unless a separate verified cleanup path exists. Do not delete benchmark work as
a substitute for container cleanup.

## Required closure

- Record a managed, run-scoped container identity without publishing operator
  or container details.
- On cancellation, stop only that identity and verify its absence before
  reporting container cleanup complete.
- Preserve partial native outputs and keep work when container cleanup cannot
  be proven.
- Add a no-live-GPU regression for a detached container surviving worker
  cancellation and for cleanup verification failure.
