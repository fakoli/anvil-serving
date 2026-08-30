---
name: anvil-media
description: Generate, inspect, or cancel images and videos through Anvil's bounded named-workflow MCP tools.
metadata:
  version: "1.0.6"
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
   If the workflow declares no quality profiles, use the empty string as its
   exact `quality_profile` value and preserve it through any resume.
   These names select exact server-owned settings; they are not a promise of
   subjective visual quality. Never include fields named by
   `qualityProfileParameters` in `parameters`.
4. Build the remaining parameters from `profiledParameterSchema` when it is
   present, and otherwise from `schema`. Preserve the user's prompt faithfully.
   If a seed is required and the user did not provide one, choose a fresh valid
   integer without asking. Ask only when a genuinely required creative choice
   cannot be inferred.
5. Call `mcp__anvil_media__media_workflow_validate` for the selected workflow.
   If an otherwise available workflow returns `backend_unavailable`, treat it
   only as a cold-worker signal and continue to submission so the gateway can
   create the bounded lifecycle approval. Do not diagnose, name, or offer to
   inspect infrastructure. For any other validation failure, report the result
   and stop.
6. Create one opaque idempotency key for the user's intent using
   `hermes-media-` followed by exactly 32 lowercase hexadecimal characters.
   Never put an ellipsis in this key. Reuse it only when
   retrying the identical workflow, version, quality profile, and parameters. A
   changed request gets a new key. Preserve a resume bundle containing the exact
   workflow ID and version, quality profile, full parameters, and idempotency
   key; never reconstruct those fields from conversation or session history.
7. Call `mcp__anvil_media__media_workflow_run` with the exact
   `quality_profile`. Preserve the returned job ID and state. Do not resubmit
   merely because generation is long-running. If it returns
   `awaiting_approval`, report the exact bounded operator action and transaction,
   plus the complete resume bundle and returned job ID.
   Copy the server-returned `resumeBundle` object exactly into a literal,
   copyable `Resume bundle` mapping. It must contain `workflow_id`, `version`,
   `quality_profile`, complete `parameters`, the full `idempotency_key`,
   `job_id`, and `approval_transaction_id`. Do not hand-build, abbreviate,
   omit, reorder, or replace any value with prose or an ellipsis. The job ID
   must appear inside the mapping even when it also appears elsewhere. Before
   sending, compare all seven fields to the returned object and rewrite the
   reply if any field differs. Never say that the bundle is stored, remembered,
   or preserved unless every field is also present in the current reply. If the
   server omits `resumeBundle` or you cannot reproduce it exactly, cancel that
   job with `mcp__anvil_media__media_job_cancel`, report
   `resume_bundle_incomplete`, and do not request approval. Treat the emitted
   bundle as a user-facing result, not internal bookkeeping.
   Hermes has no worker-lifecycle authority; stop without changing the worker.
   Do not omit the caller-generated idempotency key.
8. Poll `mcp__anvil_media__media_job_status` at a bounded cadence until the job
   is terminal or the interaction's wait budget ends. Report
   `awaiting_approval`, `preparing`, `queued`, and `running` as real
   states; never invent completion. After an operator applies a reported cold
   lifecycle approval, retry from the complete resume bundle so the reserved job
   resumes exactly once. Pass exactly its five submission fields to
   `media_workflow_run`: `workflow_id`, `version`, `parameters`,
   `quality_profile`, and `idempotency_key`. Do not pass `job_id` or
   `approval_transaction_id` to that tool: use `job_id` only for the same-job
   equality check, and use `approval_transaction_id` only to correlate the
   reported operator approval. Require `created: false` and the same job ID
   returned by the original submission; any mismatch is a hard stop. Never use
   `session_search`, session history, or another job to reconstruct or resume a
   request. After a successful reattachment, `preparing`, `queued`, and `running`
   are tool-loop states: call job status again instead of ending the response
   with narration such as "now polling."
9. For a completed job, call
   `mcp__anvil_media__media_artifact_inspect` for every returned artifact ID.
   For an image, present the native image content returned by that tool to the
   user; do not echo its encoded bytes as text. Also report the authenticated
   `resource`, media type, byte length, digest, expiry, selected quality
   profile, and the job's `latency` fields. Video remains resource-only.
10. Except for the mandatory fail-closed cancellation in step 7 when the
    server-issued resume bundle is missing or cannot be copied exactly, call
    `mcp__anvil_media__media_job_cancel` only when the user asks to cancel that
    job. Report the returned cancellation state exactly.

## Envelope and content parsing (observed live 2026-08-30)

11. Read the full MCP result. Structured envelopes nest one level deeper than
    the procedure text: `media_workflow_run` returns the job object at
    `data.job` (job id is `data.job.id`, state is `data.job.state`, and the
    `created` flag sits beside it under `data`), not `data.job_id`.
    `media_job_status` returns `{"job": {...}}` with artifacts under
    `job.artifacts[]`, each carrying `id`, `mediaType`, `byteLength`,
    `sha256`, `expiresAt`, and `resource`. Parse these shapes defensively
    instead of assuming flat `job_id`/`artifacts` keys.
12. Every tool result carries both a `text` content block containing the JSON
    envelope and, for image artifacts, a second `image` content block with the
    bounded native image content. A client that reads only text blocks loses
    the image: enumerate the whole `content[]` array and render every image
    block. `media_artifact_inspect` returns metadata plus native image
    content for eligible bounded images; video and oversized images remain
    resource-only.
13. MCP `resources/read` is not implemented by this server even though
    artifact metadata advertises a `resource` path (it answers `Method not
    found`). Retrieve artifact bytes through the authenticated artifact
    origin instead of assuming resource support, and verify the downloaded
    bytes against the artifact's `sha256` before presenting them.
14. The results are transport-shaped, not client-specific: the same envelope
    nesting and the two-block content array appear whether the call arrives
    through Hermes' MCP client or a direct stdio JSON-RPC client, so this
    section applies to any client of the eight media tools.

## Completion invariant

For a generation request, do not finish the turn after discovery, inspection,
validation, submission, or a nonterminal status. Continue the procedure in the
same turn until the job is terminal, a returned blocking state requires the
user, or the interaction's wait budget ends. Intermediate narration such as
"I will inspect it next" or "now polling" is not a completed response. When a
wait budget ends, report the current state and the complete resume bundle rather
than implying that background polling will continue.
Determine the reply language only from the current user request, never from
tool output, prior sessions, or the system locale. An English request receives
an English response. Omit internal bookkeeping, planning notes, and promises
of actions that were not completed in the turn.

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
