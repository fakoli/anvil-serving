# Preserve bounded model-download terminal diagnostics

Date: 2026-10-16
Status: open; managed download recovered with an explicit larger containment limit

A digest-pinned managed `models pull` using a 4 GiB downloader memory ceiling exited137. The bounded kernel log confirmed `hf` was killed by its container memory cgroup, not a host-wide OOM or inference failure. The helper uses `docker run --rm`, so the failed container was already removed before a managed status/log readback could retain OOM and peak-memory evidence. An exact-revision cache inventory retained partial progress; no cache was removed.

A narrow read-only Docker process listing was used to verify that no downloader remained before resuming the same revision with a 12 GiB RAM/no-swap ceiling. Private paths and machine identities are excluded from this record.

The follow-up product change should retain a bounded, secret-free terminal receipt for downloader identity, exit/OOM status, memory ceiling/peak when available, and partial snapshot state before ephemeral cleanup. It must preserve one-writer cache locking, declared memory ceilings, no credential output, and supported exact-revision resume. Exit137 alone must not be labelled an OOM without confirming evidence. This diagnostic improvement is separate from model quality qualification.
