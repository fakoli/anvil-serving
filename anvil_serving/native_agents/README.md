# Personal and invited-user Agents

A broker deployment with multiple principals must declare `personal_principal`
in its protected JSON configuration. Only that exact issuer/subject principal may
own workspaces marked `personal`. All other principals must use `sandboxed`
workspaces with distinct owner endpoints and credentials. Existing single-user
configurations remain valid without the new field.

This admission guard does not create or qualify a sandbox. An operator must bind
each invited user's entire agent process tree to a separately verified runtime,
private persistent storage and scoped model and memory credentials before adding
its workspace. A label, extra profile, Unix directory picker, or terminal-only
sandbox is insufficient. Never grant invited users the personal Hermes UI or Pi
owner endpoint as a substitute for an isolated workspace.

Keep personal providers, memories and sessions intact. The main user's personal
agent retains its existing Unix permissions; this does not create a root grant.

Before guest exposure, verify cross-user file, memory, credential and session
denials, finite resource limits, live revocation, and restart persistence. Account
creation and unit tests alone do not establish this runtime acceptance.
