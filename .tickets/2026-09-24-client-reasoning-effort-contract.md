# Preserve declared reasoning effort compatibility in client catalogs

Status: implemented and independently reviewed; source changes uncommitted

A promoted secondary model received a real Pi request with `reasoning_effort=high`
and returned HTTP 400. Its pinned upstream template supports `low`, `medium`,
and `xhigh`; the request reached the intended engine. Context and output limits
were correct. Pi supports a per-model `thinkingLevelMap`, but the secondary
catalog row omitted it. The catalog renderer also discarded the router's
declared supported-effort compatibility metadata.

Declare the actual supported efforts in the tier's public compatibility
contract. Preserve validated metadata when reconciling OpenClaw, and derive
Pi's explicit per-model map from that contract. Keep exact supported choices,
translate Pi `high` to `xhigh` for this declaration, and preserve user defaults
and unrelated models. Do not select behavior by engine or model name.

Acceptance: focused renderer validation and idempotence tests, independent
review, managed reconciliation on each installed harness, and a real Pi
conversation at `high` with a tool call and successful result continuation.
Retain the original rejection and the corrected request evidence separately.

Verified: 34 focused tests and Ruff pass. The exact installed Pi 0.85.1 SDK
includes both `xhigh` and `max`; each requires an explicit map to be selectable.
The live secondary mapping sends `high`, `xhigh`, and `max` to `xhigh`, keeps
`low` and `medium`, and maps `minimal` to `low`. It hides `off`, because the
OpenAI-format adapter otherwise omits the effort and selects the model's
default thinking mode. Normal Pi tool/result conversations passed on both
router and model hosts. Final catalogs and companion scheduled reconciliation
converged; existing interactive sessions can refresh their registry with
`/reload` without resetting the conversation.
