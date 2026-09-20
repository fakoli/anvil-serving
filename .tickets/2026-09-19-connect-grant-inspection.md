# Connect CLI: current grants and incremental access edits

Status: Open. Found during the account CLI usability review.

The local native admin protocol supports `human-set`, `human-suspend` and
`human-revoke-sessions`, but has no read operation for current human grants.
`users access` replaces the complete list. Account file groups cannot establish
gateway authorization, and `users list/show` must not imply otherwise.

Current workaround: inspect grants in the service home's Manage access panel,
then provide the full desired list to `users access`. This is documented in help
and the onboarding guide.

Acceptance for a follow-up:

- Add bounded local native grant inspection through the existing same-UID admin
  authority, with explicit absent/disabled states and no credential fields.
- Resolve usernames through the pinned provider's exact OpenID identifier.
- Add grant/revoke operations only with generation-checked updates inside the
  authority transaction; do not implement a client-side read/replace race.
- Preserve last-operator protection, revocation, preview/confirm, role enforcement
  and independent tests for concurrent edits and interrupted delivery.
- Ship through the coordinated native release workflow; never read or edit the
  live authority database directly from the Python manager.
