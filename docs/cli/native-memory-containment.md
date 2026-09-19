# Native memory containment canary

This experimental Mac command separates **unprivileged preparation** from a
separately approved **privileged test**. It does not load a model or authorize
one. First run the [small Metal accounting probe](native-memory-probe.md).

```bash
anvil-serving host native-memory-containment --prepare /private/canary --dry-run
anvil-serving host native-memory-containment --prepare /private/canary --confirm
```

Preparation requires a new directory. It compiles a fixed, reviewable helper
using the selected Xcode compiler and retains its source, binary, SHA-256
identities and OS-build identity. No elevation occurs. Review the code, exact
binary identity, protected-service baseline and proposed invocation before
approving the next command:

```bash
anvil-serving host native-memory-containment --execute /private/canary --expected-binary-sha256 REVIEWED_SHA256 --allow-privileged-probe --output /private/evidence/canary.json --confirm
```

The caller remains unprivileged. For each of three sequential modes, the command
invokes `sudo -n` on a fixed staging wrapper. The caller verifies the externally
approved binary SHA-256 before every cell and pipes the verified bytes to that
wrapper. Root creates a private temporary directory, verifies the digest again,
runs its root-owned copy, then removes that copy and directory. Root never opens
the caller-owned executable path. Cleanup must be reported before a cell can
advance. It never prompts for or
reads a password. Missing sudo authorization fails without an alternate path.
The helper accepts only `shared`, `private` or `mmap`; no process ID, memory limit,
executable, service or model argument is accepted.

Before any Metal allocation, each helper sets a **256 MiB fatal physical-footprint
limit on itself**, queries both active/inactive values and fatal attributes, then
permanently drops to the invoking user's UID/GID and removes supplementary groups.
It verifies root cannot be regained. The test uses macOS private memorystatus SPI,
whose interoperability declarations are pinned to Apple XNU
`f6217f891ac0bb64f3d375211650a4c1ff8ca1ea`; live OS support must be measured.

Each mode allocates in 16 MiB increments, stopping at 320 MiB of explicit buffers.
Shared and private Metal buffers complete GPU blits. The mmap mode uses temporary
files unlinked immediately after creation, mapped and wrapped with Metal's
bytes-no-copy API. This can temporarily dirty up to 320 MiB of scratch backing;
execution requires at least 1 GiB free on the temporary volume. No model cache or pre-existing file is opened. A 10-second
helper alarm and a 15-second parent timeout bound each child, with a two-second
TERM grace before forced termination. Normal completion and handled signals
clean the root-owned temporary stage. SIGKILL or a host failure can prevent
cleanup; a timeout therefore retains partial stage identity and reports cleanup
unverified. Resolve that exact stage before retrying. A successful fatal
limit is expected to terminate the helper before its allocation bound. Only the
helper's own process limit changes; it disappears when that process exits.

A signal is not proof of its cause. Results always retain
`hard_memory_containment_proven=false` and `target_model_trial_authorized=false`.
Even three observed SIGKILL exits return `needs_review`, requiring independent
kill-cause, accounting, protected-service and restoration checks. Swap is sampled
before, between and after cells; those samples do not prove continuous zero swap.
Any nonzero initial swap, observed growth, missing limit record or unexpected exit
stops subsequent cells. Protected voice health/PIDs and route identity must be
checked separately by the campaign operator.

This command installs no daemon, changes no launchd job, disables no swap,
restarts no service, deletes no cache and promotes no route. Do not grant it
persistent passwordless sudo or treat this canary as a production launcher.
