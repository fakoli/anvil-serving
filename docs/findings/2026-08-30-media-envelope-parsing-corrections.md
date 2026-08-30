# Media envelope parsing corrections from a live Hermes run

**Date:** 2026-08-30

**Scope:** packaged Hermes media skill documentation (`1.0.5` → `1.0.6`),
CHANGELOG; no source, gateway, workflow, or deployment change

**Deployment state:** `not-deployed`; no route, model assignment, promotion,
container, controller, or fleet state changed

## What happened

A production media run on the fleet produced one real image job end to end:
discovery through the packaged skill, submission with a caller-generated
idempotency key, queued → completed, artifact retrieval with a verified
SHA-256 digest, and an independent visual review that confirmed prompt
adherence (a fish suspended mid-air against a clouded sky). The run exercised
the standard 768×768 four-step profile of the qualified image workflow, and
the gateway routed the job to the managed worker without any approval gate.

While driving the same gateway with a direct stdio JSON-RPC client alongside
the packaged skill, three response-contract facts surfaced that the skill did
not yet document. None is a server defect: the server behaved per its tests.
They are client-parsing traps that cost one lost job id and one lost image
before being diagnosed.

## Corrections folded into the skill

| Observation | Correction (skill 1.0.6, new steps 11–14) |
|---|---|
| `media_workflow_run` and `media_job_status` envelopes nest the job object under `data.job` / `{"job": ...}`; artifacts live at `job.artifacts[]` | Parse the nested shapes; do not assume flat `job_id`/`artifacts` keys |
| Native image content arrives as a second MCP content block (`content[1]`, type `image`) beside the JSON text block; a client reading only text blocks silently drops the generated image | Enumerate the whole `content[]` array and render every image block |
| MCP `resources/read` answers `Method not found` even though artifact metadata advertises a `resource` path | Retrieve bytes through the authenticated artifact origin and verify `sha256` before presenting |

The corrections are transport-shaped, not client-specific: the same nesting
and two-block content array appear through Hermes' MCP client and a raw
stdio JSON-RPC client alike, because both meet the same gateway protocol
layer.

## Measured run

| Item | Value |
|---|---|
| Workflow | qualified image workflow, `standard` profile (768×768, four steps) |
| Job latency | 10.53 s end to end; 0.115 s generation; 10.296 s queue |
| Artifact | 666,126-byte PNG; digest verified byte-for-byte against metadata |
| Visual review | prompt-adherent; independent vision pass confirmed subject and scene |
| Skill versions | production ran `1.0.5`; corrections recorded in `1.0.6` |

## Caveats

- One image job is not a quality corpus; the existing bounded perceptual
  review evidence for the image workflow stands unchanged.
- The findings above describe the current protocol contract as implemented
  and tested; if the envelope shape changes in a later release, this section
  and the skill steps must move with it (the skill test suite pins the skill
  version and the synchronized copies).
