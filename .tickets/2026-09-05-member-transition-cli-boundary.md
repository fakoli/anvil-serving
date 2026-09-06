# Keep member transitions intact across CLI and controller boundaries

Status: locally integrated source candidate; consolidated acceptance pending.

Scheduler T009 ground-truth inspection found that all four CLI command specs
forward through the existing router_transition controller tool. Its schema and
handler do not yet accept a member, so changing only the local parser/request
function would either reject remote use or risk losing its member scope.

The T009 PRD now includes that adapter, exact member validation, authenticated
non-mutating member previews, remote allowed-argument declarations, generated
command manifest and a real ephemeral HTTP/controller regression suite.
Omitted-member preview and tier-only command behavior stay compatible. Member
previews need the router's declared scope and therefore use the authenticated
dry-run boundary; they never probe readiness or echo endpoints/credentials.

This is product code only, not a route change, model promotion or deployment.

Candidate 64c47d82 passed 281 focused tests and Ruff after commit, recorded as
EV1FE1D210. Real ephemeral HTTP tests exercise both local CLI dispatch and
controller argument/handler forwarding for all four verbs. Invalid targets,
explicit null/empty members and stale tier-only previews cannot widen scope.
Member previews authenticate without readiness probes and exclude endpoint
and credential data; omitted-member offline previews remain compatible.
