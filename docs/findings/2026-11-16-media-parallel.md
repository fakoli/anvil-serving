# Parallel image workers with exclusive video execution

<!-- benchmark-result-card/v1 -->

Two independent image workers passed bounded local capacity acceptance on one
RTX 5090. The authorized deployment enables two image execution slots; video
retains exclusive access. This changes worker capacity, with no model promotion
or new quality-profile approval.

| Result | Measured condition |
|---|---|
| 3/3 final paired cases decoded | Create/create, create/edit and edit/edit; two 768×768 outputs each, 35 steps |
| 24,903 MiB peak GPU usage | Final edit/edit pair; sampled device-wide usage |
| 16.415 GiB minimum free host commit | Final edit/edit pair; original 16 GiB guard floor retained |
| 100.234 seconds native overlap | Two separate authenticated MCP clients;32 seconds also had both CUDA allocators above 2 GiB |
| 0.078 /0.063 seconds submission return | First/second MCP render calls, after client initialization and selection |
| 4,668 tests passed | Managed Linux suite;128 skips and 20 passing subtests |

**Setup:** Windows with Docker Linux, one RTX 5090 exposing 32,607 MiB;
ComfyUI-compatible pool beneath the original memory supervisor. Image children
use separate processes,13 GiB native CUDA allocator budgets, disabled dynamic
VRAM and low-memory execution. Direct pool checks preceded authenticated MCP
acceptance. Cold startup, idle-process recycling and warm requests occurred;
these timings do not form a matched speed comparison. The
[CLI reconstruction](../cli/media.md#isolated-image-workers) describes the
public mechanism. Exact deployed identities and native bindings are private.

The [public evidence index](2026-11-16-media-parallel-evidence/README.md),
[role ledger](2026-11-16-media-parallel-evidence/artifact-manifest.json) and
[resource projection](2026-11-16-media-parallel-evidence/measurements.json)
preserve the bounded account and its gaps. Public native identity/raw evidence
roles are explicitly missing, so this is not an independently reproducible
model qualification. Original records, outputs and rollback bindings remain
private. The common public ledger was assembled after execution.

## Resource and lifecycle checks

The original host-memory policy,64 GiB container RAM limit,72 GiB combined
RAM/swap limit,16 CPU limit and 1,024 MiB physical GPU reserve remained intact.
Both children inherited one 7,200-second supervisor lifetime,2,700-second job
reservation and 30-second margin. Process recycling and video transitions did
not renew that deadline. Kernel container OOM/OOM-kill counters stayed zero.

Each admitted image reserves 15 GiB of host commit above the original 16 GiB
floor: two admissions require 46 GiB currently free commit. This is an admission
reservation, not a measured maximum per-job allocation. Lower headroom can
serialize requests despite a free execution slot. Continued external pressure
can still stop work through the original supervisor.

Final direct native queue overlap was 94.312 seconds for create/create,
110.125 seconds for create/edit and124.984 seconds for edit/edit. All pairs
also showed both CUDA allocators holding over 2 GiB simultaneously. This proves
two independently progressing renders; it does not measure simultaneous GPU
kernel execution or a twofold speedup.

One previously approved short segmented-video profile also passed: two
segments,608×352, 24 fps, 20 steps, 226 decoded frames. Only one full-budget
native video process ran while the image request waited. The pool returned to
image mode, and video capture after the native child stopped succeeded.
Previously completed image custody also survived recycling. Sampled video GPU
usage peaked at 31,580 MiB, leaving 1,027 MiB; no second video lane is qualified.

## Second-caller contract

Each MCP submission returns its own durable job ID and queue state. With a
qualified free lane and enough headroom, both images progress concurrently.
Otherwise the second request stays FIFO-ordered until admission is safe.
An exclusive video waits for image work to drain and keeps later image work
queued until video execution releases the GPU.

The test closed the second client's process after submission; its job still
completed and another client fetched the output. Retrying its idempotency key
returned the same job. A third request queued and canceled without interrupting
either active render. A fresh client also verified a one-second blocking
`media_job_status` wait. The advertised bounded wait is at most 20 seconds;
longer observation uses repeated status calls or `media job watch`, which the
harness may run as a background task. No unsolicited MCP completion callback
or native MCP task extension is claimed. Existing clients with cached older
tool schemas can continue polling without the optional wait argument.

## Failures and boundaries

Earlier low-headroom trials completed serially and are excluded from parallel
passes. An observer 503 was recovered by observing existing native IDs rather
than resubmitting. Idle recycling required a TIME_WAIT socket fix; Linux
parent-death, bounded orphan cleanup and port-reuse checks passed. The
[friction log](2026-11-16-media-parallel-evidence/friction-log.md) retains the
inferences and superseded fixture/environment failures.

The deployed worker/gateway image bindings and 15 mount dependencies were
read back through bounded product diagnostics. All existing available workflow
approvals remain registered. Temporary diagnostic execution was removed after
the queue drained; exact rollback configuration and tested build contexts were
retained privately. No new image sizes, longer videos, parallel videos, model
quality, endurance or shared inference/media capacity were established.
