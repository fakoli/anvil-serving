---
name: anvil-media-watch
description: Generate media through Anvil Media MCP and monitor long renders in Pi using background completion notifications. Use when a video should finish asynchronously and return to the same conversation without a manual check prompt.
---

Submit with the existing Anvil Media MCP. Read its current schemas:
`media_workflow_validate` takes only `workflow_id` and `version`; parameters,
quality profile and the stable idempotency key belong on `media_workflow_run`.

For each accepted nonterminal job, register a watcher immediately using Pi's
installed `bg_run`. Use `anvil-serving media job watch --job-id <ID>
--client-config <protected-client.json>` with `isAgent:false`,
`notifyOnCompletion:true`, `triggerOnCompletion:true`, and a background timeout
at least 30 seconds longer than the watcher's `--timeout-seconds`. The connection file contains the
existing stdio command, arguments and environment references, not credentials
in the shell command. Read local `TOOLS.md` if provided for installed paths.
Quote each path/ID for the platform shell; never interpolate prompts or tokens.

Keep the returned background task ID and output path in the current session.
Do not claim monitoring is active before `bg_run` returns its registration
receipt. Continue independent work or end the turn; do not sleep, repeatedly
check status, or require the user to say "check". The background notification
will wake a follow-up turn in this same session. Read its output once, then
present completed artifact links when returned by the gateway, or the actual
failure/approval-needed state. If only a relative resource is returned, report
that limit; never invent a URL.
For generation followed by upscale, submit the upscale with that completed
owned source, a new stable key, and register a new watcher for the upscale.

The watcher only reads status and artifact metadata over MCP. It never starts
workers, resubmits jobs, approves gates, cancels rendering or changes routes.
`awaiting_approval` requires the existing operator flow; replay only the exact
saved resume bundle after approval, then watch the same job. A watcher timeout
is not a failed/canceled render: reattach to the same job if still wanted.

The Pi session must remain open for automatic delivery. Session shutdown/reload
stops its background commands; durable media jobs persist. Reattach a watcher
to the saved job ID after resuming. Never regenerate to recover a notification.
