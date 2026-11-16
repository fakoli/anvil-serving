# Private media profiles: worker capacity

<!-- benchmark-dossier/v2 -->

## Current status and review date

Reviewed 2026-11-16. Bounded `functional` and `capacity` checks support two
image workers and exclusive video on one RTX 5090. Decision: `no-promotion` of
models; the user authorized enabling a second slot after passing acceptance.
Exact deployed identities are private, so public reproduction is incomplete.

## Immutable identity

Model/variant identities, asset fingerprints and immutable native bindings are
retained only in private operator state. This page cannot qualify an arbitrary
checkpoint, runtime image or similarly named workflow.

## Tested hardware and topology

One RTX 5090,32,607 MiB, on Windows with Docker Linux. Independent image child
processes share the device beneath one managed supervisor. Other GPUs and
co-resident model-serving capacity were not measured.

## Engine, quantization, KV, context, and concurrency recipe

The [pool reconstruction](../../cli/media.md#isolated-image-workers) describes
the mechanism. Two 13 GiB native CUDA allocator budgets with dynamic VRAM
disabled; image profiles768×768/35 steps. Original 16 GiB free-commit floor
plus 15 GiB per admitted image;1,024 MiB device reserve. Video uses one full-budget
process after image children drain. Deployed quantization/weights are private;
text KV/context comparisons do not apply.

## Evidence by measurement class

[Dated acceptance](../../findings/2026-11-16-media-parallel.md): create/create,
create/edit and edit/edit passed paired full decode and actual native overlap.
Edit/edit peaked at 24,903 MiB GPU usage with 16.415 GiB free host commit.
Two real MCP clients passed disconnect/reconnect, idempotent retry, queued
cancellation and bounded waiting. Public resource projection is derivative;
native evidence and exact identities remain private.

## Decision and promotion state

Enable two image slots only on the tested deployment and bounds. Keep video
exclusive. Existing human-approved workflow profiles remain unchanged in
quality/duration. New hardware, profiles or resource budgets need new capacity
evidence; no new model promotion follows from these tests.

## Failures and gotchas

- **Headroom:** lower free commit serializes work; external pressure can still
  trigger the original memory supervisor.
- **Recovery:** uncertain native acceptance is retained without resubmission;
  child recycling preserves captured-history/output custody and the deadline.
- **Coverage:** no parallel-video, new-size, endurance or matched speed claim.
- **Publication:** public identity/native roles are missing for privacy, and
  the common public role ledger was assembled after execution.

## Dated run history

- 2026-11-16 — [Parallel image capacity and exclusive video acceptance](../../findings/2026-11-16-media-parallel.md).
