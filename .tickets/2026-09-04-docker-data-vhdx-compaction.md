# Managed Docker Desktop data-VHDX compaction

Status: Completed 2026-09-05

## Gap

Docker Desktop's Windows data disk can remain much larger than the live Docker
objects after exact image and model-cache cleanup. Anvil Serving had no bounded
host operation for compacting that disk, which forced operators outside the
managed product surface for a repeatable host lifecycle action.

## Acceptance

- Accept one explicit absolute path only.
- Restrict the target to known Docker Desktop data-disk layouts:
  `.../Docker/wsl/disk/docker_data.vhdx` and the legacy
  `.../Docker/wsl/disk/docker/_data.vhdx`.
- Refuse symlinks, reparse points, directories, fixed disks, non-VHDX files,
  compressed files, encrypted files, and sparse files.
- Provide read-only preview plus an explicit `--confirm` gate.
- Stop Docker Desktop before mutation and prove the VHDX is detached.
- Re-resolve the file identity after shutdown and refuse identity drift.
- Prefer a read-only mount with Hyper-V `Optimize-VHD -Mode Full`, and detach it
  even when optimization fails. If Windows refuses the mount only because the
  current shell lacks the virtual-disk privilege, fall back to exact detached
  `Prezeroed` compaction without requesting elevation or opening UAC.
- Leave Docker Desktop stopped and report exact before, after, and reclaimed
  byte counts. Do not change the virtual capacity.
- Add focused tests, CLI documentation, and a live proof against the operator's
  exact data disk after conservative cleanup.

## Recovery

The operation modifies allocation in place without deleting Docker objects.
Restart Docker Desktop to remount the same data disk. Docker's documented VM
backup procedure remains the disaster-recovery path for the data VHDX.

## Completion evidence

Hermetic tests cover current and legacy layouts, relative and unrelated-path
refusal, preview behavior, already-stopped and stop-first paths, attached-disk
refusal, missing Hyper-V support, the stopped-CLI response, and the
non-elevated detached fallback. Second-inspection, optimization, and final-
inspection failures after shutdown preserve the stopped state and recovery
instruction in both library and CLI results. A confirmed live run preserved the file
identity and virtual capacity, left Docker Desktop stopped with the disk
detached, and reclaimed hundreds of gigabytes from the physical VHDX file.
