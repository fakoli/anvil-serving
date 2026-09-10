# Workbench implementation reflection

This reflection records the design decisions, improvements found during review,
and operating limits of the Workbench. Deployment receipts and current PR checks
remain the authority for the installed revision and release status.

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

## Improvements made during acceptance

Testing the real controller transport found a contract error that shaped replies
had hidden: transport redaction removes raw executable arguments. The diagnostic
review now uses the exact declared command, owner, container and bounded limits;
private policy and candidate digests still pin the actual execution. An
authenticated HTTP regression proves preview, apply and rejection after a policy
change without weakening global command or credential protections.

Pi polling initially replaced controls while the operator was typing. The chat
now retains separate drafts, native event order and focused inputs. An actual
browser check caught a second issue between focus loss and clicking an extension
response. Keeping already-rendered unresolved controls in place fixed that race
while allowing new prompts and completed responses to render. The final model
and thinking selectors share a compact row and collapse on smaller screens.

Cross-platform CI exposed assumptions about POSIX file descriptors, pipe polling,
absolute paths and file encoding. The private store and bounded CLI adapter now
use the supported platform boundaries. Linux-only runner provisioning fails
explicitly on unsupported systems; it no longer prevents importing the product
or using its portable commands.

Connecting the historical project also exposed missing State registration and
legacy replay defects. Those repairs belong in Anvil State and use its normal
event and backup mechanisms. The Workbench does not synthesize a competing
project or repair its database directly.

The installed service exposed a storage-proof assumption that host-level tests
missed. Systemd isolation can show identical stacked bind mounts for one bounded
filesystem. Validation now compares the whole filesystem root, device identity,
source, separate mount and superblock flags, and the configured loop image;
foreign overlays and nested mounts remain rejected. The task's frozen Python
verification command also exposed a missing runner toolchain. Python and pinned
pytest are now built into the runner, and the actual command passed in the
networkless verifier without installing dependencies at task runtime.

Remaining limits are intentional operational boundaries. A private Workbench
policy supplies exact projects, resource grants, image digest,
provider/model endpoint, protected credential file and controller availability.
The current UI cannot configure arbitrary providers, filesystem paths, Docker
commands, host shells, native service execution, model promotion, or task
acceptance. Provider gateway setup requires a reachable declared provider and
engine policy; no networkless fixture proves that external route. Pi task work
depends on a healthy Anvil State project and a valid exclusive lease. The chat
uses official Pi RPC with a Pi Web-style thread layout; it does not embed or fork
Pi Web. Arbitrary project extensions remain disabled in the installed runner.
The supported extension-dialog renderer does not grant permission to install or
execute an extension. Benchmark operations remain deterministic owner workflows,
separate from Pi conversations and independent quality acceptance.
