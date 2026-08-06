# Split-restore rollback serve `primary` references a vLLM nightly tag that no longer exists on Docker Hub

**Status:** Open — rollback path degraded on Fakoli Dark

## Problem

During a failed `serves mode enter` on 2026-08-06, the automatic split-stack
restore brought `omni` up but failed on `primary` (Agents-A1 FP8):

```text
Error response from daemon: failed to resolve reference
"docker.io/vllm/vllm-openai:nightly-f25953cc59f9b4ba9b04b16228d2b86dcfbcbdb1":
not found
```

The image is a vLLM **nightly** tag. Nightly tags are eventually evicted from
Docker Hub, and the image is no longer in the local Docker cache, so the
`llm-stack` restore group — the documented rollback for exclusive TP=2 mode —
can no longer start its primary serve. Exclusive-mode failure handling
currently assumes the restore group is startable; tonight it silently degraded
to omni-only.

## Required behavior / operator action

1. Operator: repin the `primary` service in the operator-home
   `docker-compose.yml` to an image reference that is either digest-pinned
   **and still resolvable**, or to a retained release tag qualified for
   Agents-A1 on sm_120. Until then, `llm-stack` is not a working rollback and
   `anvil-router.live.toml` describes a tier whose backing serve cannot start.
2. Product: `serves mode enter` should verify rollback-group images are locally
   present (or resolvable) as part of plan validation — a rollback that cannot
   pull is a false safety net and should fail the preview, not the recovery.
3. Product: consider a policy lint that flags `nightly-*` tags in any serve
   that participates in a rollback/restore group.

## Acceptance

- Preview/plan validation reports missing rollback images before mutation.
- Hermetic test covering an unpullable rollback image in the restore group.
- Operator evidence that `llm-stack` restores end-to-end again on Dark.
