# Retain bounded private diagnostics for a failed native fleet reader

Status: open; functional permission repair tracked separately in AI-Infra PR173.

The owner reports `reader_unavailable` when a pinned fleet reader fails. Its
public response correctly omits private paths and subprocess output, but the
adapter deletes its temporary Ansible log on failure and the owner log retains
only the top-level error. There is no managed operation to retrieve the leaf
failure. This required bounded, read-only inspection of the owning container.

The observed case combined an unreadable root-owned non-secret wheel and an
aggregate readback timeout. A diagnostic-only longer bound produced a complete
thirteen-target observation; it was not accepted as a production preview. Keep
those failures separate and preserve the original limits until independently
qualified changes exist.

Acceptance: retain bounded, private, owner-scoped failure evidence through a
supported diagnostic operation, without returning credentials, changing the
selected model or replaying native effects. Keep full-fleet custody, deadline,
output and descendant-cleanup guarantees; do not expose raw logs in ordinary
caller errors or treat a diagnostic invocation as acceptance.
