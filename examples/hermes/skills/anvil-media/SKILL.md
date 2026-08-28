---
name: anvil-media
description: Generate, inspect, or cancel images and videos through Anvil's bounded named-workflow MCP tools.
metadata:
  hermes:
    tags: [media, image-generation, video-generation, mcp]
    category: media
---

# Anvil media

Use this skill when the user asks Hermes to generate an image or video through
Anvil, inspect a prior Anvil media job, retrieve its artifact, or cancel it.

## Procedure

1. Call `mcp__anvil_media__media_capabilities` and
   `mcp__anvil_media__media_workflow_list`. Select one exact returned workflow
   whose kind matches the request. If it is unavailable, report its stated
   reasons and stop.
2. Call `mcp__anvil_media__media_workflow_show` for that exact workflow and
   version. Build `parameters` only from the returned public parameter schema;
   ask for a required value when the user's request does not determine it.
3. Call `mcp__anvil_media__media_workflow_validate` for the selected workflow.
   If validation says it cannot run on the configured worker, report that
   result and stop.
4. Create one opaque idempotency key for the user's intent. Reuse it only when
   retrying the identical workflow, version, and parameters. A changed request
   gets a new key.
5. Call `mcp__anvil_media__media_workflow_run`. Preserve the returned job ID
   and state. Do not resubmit merely because the request is long-running.
6. Poll `mcp__anvil_media__media_job_status` at a bounded cadence until the job
   is terminal or the interaction's wait budget ends. Report
   `awaiting_approval`, `preparing`, `queued`, and `running` as real states;
   never invent completion.
7. For a completed job, call
   `mcp__anvil_media__media_artifact_inspect` for each returned artifact ID.
   Give the user the authenticated `resource` returned by Anvil plus its media
   type, byte length, digest, and expiry. Do not place full video bytes in the
   response.
8. Call `mcp__anvil_media__media_job_cancel` only when the user asks to cancel
   that job. Report the returned cancellation state exactly.

## Boundaries

- Use only the eight tool names in the procedure above.
- Treat workflow IDs, versions, parameters, availability, limits, job states,
  and artifact resources returned by Anvil as authoritative.
- Do not construct or accept backend execution definitions, implementation
  identifiers, model filenames, host addresses, local paths, installation
  requests, or service-management operations.
- Do not retry through a different workflow, host, model, MCP server, or
  provider. Ask the user before changing their requested workflow parameters.
- An approval requirement is a result to report, not permission to alter the
  worker. Never offer bypass instructions.
- Use the caller-owned job and artifact identities as returned. Never supply an
  `owner` argument unless the user is operating with an explicitly authorized
  cross-principal role.

## Failure handling

Surface Anvil's error code and safe explanation. For an unavailable workflow,
approval requirement, admission refusal, failed job, expired artifact, or
authorization denial, preserve that disposition and stop. A transport failure
may be retried with the same idempotency key; it does not authorize another
submission path.
