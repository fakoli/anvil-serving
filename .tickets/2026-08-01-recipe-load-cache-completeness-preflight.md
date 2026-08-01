# Preflight recipe cache completeness before GPU startup

**Observed:** 2026-08-01

## Problem

`models recipes load` starts the serving engine even when the pinned repository
revision is only partially cached. vLLM then downloads missing shards inside the
GPU-owning serve container. The Nemotron Super TP2 attempt spent more than ten
minutes loading, exhausted the cache volume, and only exposed the missing-space
warnings in the container log after the managed readiness timeout.

The recipe already records an exact repository and revision, and `models pull`
already has independent snapshot, incomplete-file, broken-link, and free-space
checks. The load path does not reuse those checks or force an offline serve.

## Impact

- A bounded serve operation can turn into an unbounded network transfer.
- Both GPUs remain exclusively occupied while a cache-space failure is inevitable.
- Cold-start timing mixes model download with engine initialization and is not
  publishable benchmark evidence.
- A successful engine start would not prove the artifact was complete before the
  serve began.

## Proposed resolution

Add an opt-in strict artifact preflight for pinned recipes. Before `docker run`,
verify the exact snapshot exists, has no incomplete files or broken links, and has
the expected revision. Start the serve container with Hugging Face offline mode so
missing content fails immediately. Point failures to the managed `models pull`
command for the same repo and revision.

Separately, make the recipe health timeout configurable because large complete
checkpoints can legitimately need more than 600 seconds to initialize.

## Acceptance

- A pinned strict recipe fails before GPU allocation when its snapshot is absent,
  incomplete, or contains broken links.
- The error prints an exact managed pull command and does not reveal credentials.
- Strict recipe containers cannot download model files during startup.
- A hermetic test distinguishes artifact-preflight failure from engine-readiness
  timeout.
- Recipe-specific readiness timeout remains bounded and validated as positive.
