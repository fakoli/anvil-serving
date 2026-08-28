---
name: anvil-media
description: Generate, inspect, or cancel images and videos through Anvil's bounded named-workflow MCP tools.
metadata:
  version: "1.0.0"
  hermes:
    tags: [media, image-generation, video-generation, mcp]
    category: media
---

# Anvil media

Use this skill automatically when the user asks Hermes to create, generate,
render, or revise an image or video through Anvil, or asks to inspect or cancel
an earlier Anvil media job. A slash-command invocation is optional.

## Procedure

1. Call `mcp__anvil_media__media_capabilities` and
   `mcp__anvil_media__media_workflow_list`. Select one exact returned workflow
   whose kind matches the request. If it is unavailable, report its stated
   reasons and stop.
2. Call `mcp__anvil_media__media_workflow_show` for that exact workflow and
   version. Treat its parameter schema, quality profiles, availability, and
   limits as authoritative.
3. Choose only a quality profile declared by the workflow. Use `draft` for an
   explicitly quick or rough request, `high` for an explicitly high-detail or
   largest-output request, and the returned `defaultQualityProfile` otherwise.
   These names select exact server-owned settings; they are not a promise of
   subjective visual quality. Never include fields named by
   `qualityProfileParameters` in `parameters`.
4. Build the remaining parameters from `profiledParameterSchema` when it is
   present, and otherwise from `schema`. Preserve the user's prompt faithfully.
   If a seed is required and the user did not provide one, choose a fresh valid
   integer without asking. Ask only when a genuinely required creative choice
   cannot be inferred.
5. Call `mcp__anvil_media__media_workflow_validate` for the selected workflow.
   If validation says it cannot run on the configured worker, report that
   result and stop.
6. Create one opaque idempotency key for the user's intent. Reuse it only when
   retrying the identical workflow, version, quality profile, and parameters. A
   changed request gets a new key.
7. Call `mcp__anvil_media__media_workflow_run` with the exact
   `quality_profile`. Preserve the returned job ID and state. Do not resubmit
   merely because generation is long-running.
8. Poll `mcp__anvil_media__media_job_status` at a bounded cadence until the job
   is terminal or the interaction's wait budget ends. Report
   `awaiting_approval`, `preparing`, `queued`, and `running` as real
   states; never invent completion.
9. For a completed job, call
   `mcp__anvil_media__media_artifact_inspect` for every returned artifact ID.
   For an image, present the native image content returned by that tool to the
   user; do not echo its encoded bytes as text. Also report the authenticated
   `resource`, media type, byte length, digest, expiry, selected quality
   profile, and the job's `latency` fields. Video remains resource-only.
10. Call `mcp__anvil_media__media_job_cancel` only when the user asks to cancel
    that job. Report the returned cancellation state exactly.

## Boundaries

- Use only the eight tool names in the procedure above.
- Treat workflow IDs, versions, quality profiles, parameters, availability,
  limits, job states, latency, and artifact resources returned by Anvil as
  authoritative.
- Do not construct or accept backend execution definitions, implementation
  identifiers, model filenames, host addresses, local paths, installation
  requests, or service-management operations.
- Do not retry through a different workflow, host, model, MCP server, or
  provider. Ask the user before changing their requested creative intent or
  quality profile.
- An approval requirement is a result to report, not permission to alter the
  worker. Never offer bypass instructions.
- Use the caller-owned job and artifact identities as returned. Never supply an
  `owner` argument unless the user is operating with an explicitly authorized
  cross-principal role.

## Failure handling

Surface Anvil's error code and safe explanation. For an unavailable workflow,
approval requirement, admission refusal, failed job, oversized inline image,
expired artifact, or authorization denial, preserve that disposition and stop.
A transport failure may be retried with the same idempotency key; it does not
authorize another submission path.
