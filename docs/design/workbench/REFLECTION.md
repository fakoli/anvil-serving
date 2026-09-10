# Workbench implementation reflection

This reflection records implementation changes and open operating limits. It is
not a claim that release, merge, installation, or live browser acceptance has
completed.

1. The dashboard moved from a concept-only shell to authenticated workspace
   views backed by declared owner projections. This keeps connector, controller,
   Connect and Anvil State authority in their existing systems.
2. Playground now retains user-scoped conversations and effective request
   parameters while resolving protected credentials only on the server. Exact
   connector/model policy prevents browser-selected endpoints or substitutions.
3. Project work now freezes a task packet, validates Anvil lease ownership and
   keeps Pi in a separate full clone. Evidence capture includes committed and
   untracked changes, records actual file modes, and compares type/mode/content
   before recovery or transfer.
4. Pi sessions use the official RPC agent and durable intent/cursor records.
   A pinned unprivileged runner, per-session reverse gateway and fixed storage
   pool separate native agent state from server journals and provider secrets.
5. Storage setup is a preview-first supported command. It proves an existing
   loop-backed ext4 pool rather than reformatting it, rejects nested mounts and
   journal overlap, and installs a reviewed systemd mount only after validation.

Remaining limits are intentional operational boundaries. A private full
Workbench policy must still supply exact projects, resource grants, image digest,
provider/model endpoint, protected credential file and controller availability.
The current UI cannot configure arbitrary providers, filesystem paths, Docker
commands, host shells, native service execution, model promotion, or task
acceptance. Provider gateway setup requires a reachable declared provider and
engine policy; no networkless fixture proves that external route. Pi task work
depends on a healthy Anvil State project and a valid exclusive lease. A complete
delivery record still needs the configured installation, authenticated rendered
journeys, broad checks, independent review and the maintainer's release/merge
decision.
