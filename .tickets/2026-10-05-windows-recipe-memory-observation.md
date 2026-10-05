# Windows recipe cgroup observations are unavailable

Status: implemented; independent review pending
Observed: 2026-10-05

Managed recipe status and discovery retain Docker's declared limits and exit/OOM
state, but current/peak memory, effective cgroup limits, and memory.events are
null on Windows Docker Desktop WSL. The observer reads native /proc and
/sys/fs/cgroup, which are unavailable to the Windows process. This blocks measured
candidate memory qualification; null values must never be described as measured.

Add a narrow read-only local Docker Desktop WSL observation path. Validate the
selected local endpoint and exact running container identity; refuse unsupported
or remote endpoints. Preserve admission policy and existing limits. Stopped
containers retain Docker state with unavailable live counters. Bound probe time
and data, and return unavailable rather than break status when observation fails.

Diagnosis may use exact-ID read-only kernel cgroup reads because the managed
surface is the failing component. No container lifecycle or host mutation is
required. Cover endpoint selection, malformed identity/data, timeout/exit races,
valid cgroup counters and both status/discovery callers with independent tests.

## Evidence and implementation

The read-only diagnosis found Docker's State.Pid absent from the docker-desktop
/proc namespace. The host cgroupfs path /sys/fs/cgroup/docker/<full-container-id>
was accessible through the existing WSL helper. The implementation therefore
executes no command in the container. It validates the selected local pipe,
Desktop/WSL engine and matching VM memory identity, then reads five fixed kernel
files with a five-second timeout and 2 KiB per-file bound. Unknown layouts and
unavailable or malformed counters preserve Docker state with null live values.

Both exact-recipe status and label-owned discovery propagate their runner to the
observer. The shared endpoint/VM identity helpers retain the existing admission
checks, with all reserve/capability tests unchanged. Tests cover cgroupfs/systemd,
remote-context precedence, invalid IDs, stopped/racing containers, unlimited
limits, missing/duplicate/malformed counters, timeout, and caller integration.

Validation: 260 focused tests passed. A local read-only diagnosis returned current
memory, peak memory and zero OOM counters from an existing service; no model or
host lifecycle mutation was required. Actual model qualification remains a
separate operation and must retain observations while its cgroup is alive.
