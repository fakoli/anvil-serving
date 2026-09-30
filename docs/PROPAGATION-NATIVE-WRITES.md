# Native propagation writes

The propagation foundation provides an in-process owner fence around direct
client catalog writes and Pi media withdrawal. It is not a deployed fleet writer.
Legacy callers remain unenrolled until the reviewed single-writer cutover.
An apply that supplies an owner contract or grant but omits the native fence
refuses before entering a legacy writer; previews remain read-only.
Fenced Pi/OpenClaw previews and repeat readback use the same native directory
custody as apply, with an exact grant and current owner authority. A preview
does not reserve a generation or authorize a write. Unfenced preview is legacy
behavior and is not propagation evidence. OpenClaw's optional service-env file
is a read-only path derived from the bound OpenClaw directory; it is never a
catalog-write effect.
The native journal uses the user's protected `.config/anvil-serving/propagation-fencing`
directory; the separate operator home may be a symlink and is not journal custody.

The owner supplies a canonical approved contract, generation, catalog digest,
resource identity and an immutable `TrustedNativeOwner.effect_bindings` mapping
from each native effect name to its approved effect kind and exact file path.
Installation resolves this mapping; request paths or effect names cannot create
it. An absent mapping refuses grants. Grants must match these paths and the
contract's permitted effect kinds, including at the actual fenced write boundary.
A private permanent index and
operation journal share one owner/resource lock across callers. Expired authority,
stale generations, conflicting identities and unresolved operations fail closed.
The installed `current_authority` callback checks admitted contract/generation/epoch
at lock entry and immediately before replacement. It is required for writes.
For the `client-catalog` resource, the admitted owner first calls
`activate_catalog_cutover` under that same lock. Existing direct CLI/MCP, scheduled
and manual catalog/media writes then refuse `stale_generation` at their shared
mutation entry. Installation must drain old binaries and disable their triggers
before activation; an old binary cannot be retroactively fenced. The cutover
marker retires legacy writes but does not itself approve a new effect.
An interrupted transaction retains its original effect records for recovery;
it does not automatically compensate or claim that an external effect stopped.

Each write retains the bytes used for rendering, binds a verified private backup
and manifest to those originals, checks for intervening edits, atomically replaces
the file, and reads it back. POSIX operations use held directory descriptors and
no-follow relative opens. Windows retains non-reparse directory handles and
validates native permissions. Unknown or unsafe custody is refused. A successful
file readback does not establish that an existing client session loaded the file.

`sync_client_catalog_batch` groups distinct Pi/OpenClaw installation paths in
one `client-catalog` generation. The owner derives native effect bindings from
each exact approved target identity and must check current authority for the
whole protected target set. Preview and repeat readback use held custody; apply
uses one union grant, namespaced journal effects and separate backups for each
installation. Overlapping file paths and a derived shared-home `.env` refuse.
The call returns target summaries only after every path set verifies. A later
target failure retains the original reservation for recovery and returns no
partial success rows. This source interface is not yet installed by fleet apply;
Hermes, loaded sessions and monitoring remain separate gates.

## Bounded controller jobs

The controller can bind a `PropagationService` at construction. The default
controller has no execution profile and declares propagation unavailable.
An opt-in `controller serve --propagation-profile PATH
--propagation-profile-sha256 SHA256` path constructs the service only from one
mode-0600, custody-checked JSON profile and a separately reviewed exact digest.
Both options and a valid scoped authorization policy are required. The profile
selects an exact `preview` or `effects` mode and binds one protected ledger,
pinned approved contract, active identity, fixed
apply and readback execution profiles, executor issuer and protected Workflows
control socket/token references. The ledger must already exist with mode 0600
inside an owner-only mode-0700 directory.
Executable and artifact paths need protected ancestry; their hashes are checked
at startup and before execution.

The pinned readback executable receives bounded JSON on stdin with schema
`anvil-serving.propagation-reader/v1` and one of `probe`, `preview`, `status`,
`verify`, `convergence`, `recovery` or `reconcile`. It returns one bounded JSON
object; failures, timeouts, excess output and concurrent readback fail closed.
The startup probe binds the installed profile and approved contract digest with
the complete mode list. It authorizes no effect. ai-infra still needs to supply
the reviewed fixed readback and fleet apply executables, and the private
installer must bind their exact artifacts and controller mounts.

The owner can resolve one canonical approved contract through
`PinnedApprovedContract`. Its expected SHA-256 must come from a separately
reviewed, protected installation profile; it cannot be computed from the
incoming request or the file at runtime. Every lookup rechecks the file pin,
ownership and path custody, so replacement or revocation fails closed. The
resolver is wired only through the protected opt-in profile; it does not itself
establish active model identity or authorize a fleet effect.
The contract's `authority_mode` is part of its canonical bytes but intentionally
outside the stable effect-set digest. A `preview` contract cannot pass admission,
and the owner refuses a contract whose authority mode differs from its pinned
profile mode. Enabling effects therefore requires distinct `effects` contract
bytes while retaining the reviewed effect-set digest.

`ObservedActiveIdentity` is a separate read-only owner input. It compares the
authenticated router's current catalog digest with the installed expectation,
matches an exact previously approved container ID and pinned Docker start-input
digest (image, recipe labels, arguments, mounts, ports and GPU requests), checks
Docker's requested and observed loopback port binding, then asks that endpoint
for the exact served ID. Only a complete match returns the deterministic
activation fingerprint. The protected installation profile must supply and pin
the activation receipt and its runtime digest before production admission;
the default controller remains unavailable. Environment
values are omitted from the public fingerprint; the exact container ID pins
the immutable Docker environment from the approved activation.
The activation receipt must also prove the selected model artifact/revision and
its mount custody; matching container start inputs and a served alias alone do
not prove the loaded weight bytes. Missing artifact evidence refuses production
binding.
The observation is point-in-time evidence, not a mutation-boundary fence;
native effects still require a fresh current-authority check under their lock.
The observer refuses success after a 20-second aggregate acceptance deadline;
individual HTTP and Docker calls retain their own bounded timeouts and cleanup.
Its container must share the intended host network namespace with the model
loopback listener; an installed cross-namespace proof remains required.

The bound service declares `propagation.accept.v1`, `propagation.profile.v1`,
`propagation.preview.v1`, `propagation.status.v1`, `propagation.resume.v1`,
`propagation.cancel.v1`, `propagation.recovery.verify.v1`,
`propagation.dispatch.pending.v1`, `propagation.dispatch.record.v1`,
`fleet.propagation.preview.v1`, `fleet.propagation.submit.v1`, `fleet.propagation.current.v1`,
`fleet.propagation.status.v1`, `fleet.propagation.verify.v1`,
`fleet.propagation.convergence.v1`, `fleet.propagation.cancel.v1`.
An installed preview-mode owner advertises and accepts only profile, recovery
verification and whole-fleet preview reads. Every admission, dispatch,
workflow-control, job and current-authority operation fails closed even for a
broader credential. Enabling effects requires a separately pinned owner profile
and executable approved contract.
Admission, dispatch, activity and status credentials have separate scopes.
Requests contain opaque owner references; profiles, executable pins, transport
credentials and approval resolution come from the installed owner configuration.
The status-scoped `current` read checks the exact contract digest, generation,
declared target/resource and an executing job against the owner's current
approval and activation. A native client uses it inside its file fence's
current-authority callback; the read grants no file path or effect by itself.
Give each native host its own status-only credential for audit attribution.
It cannot invoke fleet preview, submit, verify, convergence or cancel operations.
Transport failure or a superseded, cancelled or completed job refuses a write.
The native client helper requires a closed success response and an observation
within five seconds, with at most two seconds of future clock skew. It refuses
responses that finish after a five-second acceptance window. The parent native
supervisor still owns the hard execution budget if a transport stalls.

Submit requires a complete current preview and retains one durable job identity.
An expired preview cannot authorize a new effect, but a repeated submission can
resolve the original job. Cancellation reports requested, confirmed or uncertain;
a lost process acknowledgement requires reconciliation. This release's native
supervisor is Linux-only; it does not install workers on fleet clients.

Verification and convergence perform separate fresh owner observations under
one stable pass identity. Generic HTTP idempotency caches cannot replay these
observations. Continuation cursors bind the authenticated caller, operation,
arguments and immutable snapshot. Consumers must read every page and retain
every declared target. Offline clients remain pending with their last observation.
An applied job alone does not establish verified client or monitoring acceptance.

The controlled integration check is
`python scripts/run_tests.py tests/test_propagation_jobs.py tests/test_propagation_supervisor.py -q`.
It uses synthetic native effects and scoped loopback HTTP, not live fleet state.

## Hermes capability boundary

The pinned executable reader supports native macOS directory descriptors and
extended-ACL inspection. macOS execution additionally requires root-owned code
and ancestry with no group/other write access; a mutable user installation is
refused. The helper retains and rechecks the opened objects through completion.
Provisioning must pin the interpreter, package and dependencies in that protected
release. This custody check alone does not prove descendant quiescence or loaded
session acceptance, and it does not install a Mac controller daemon.

Fenced Hermes profile catalog rendering runs CLI reads and writes in a disposable
home, then journals the exact proposed YAML bytes under the native file fence.
Synthetic tests prove byte custody and refusal after a concurrent edit; installed
profile behavior and loaded-session acceptance remain unverified. Hermes media
mutation remains `UnsupportedCapability` under the fence, including preview.
The orchestration layer must retain each declared profile as pending until its
installed identity and session state are verified; it cannot omit it or claim
convergence.

The existing unfenced Hermes path is legacy pre-cutover behavior. It is not
fenced propagation evidence. Release task T011 must provide installed
per-profile capability/readback evidence before Hermes integration or cutover
is accepted. The writer must preserve unrelated provider, credential, account
and session state. Loaded-session observation and idle-gated reload remain
separate requirements.

## Explicit recovery

`NativeMutationFence.resume` reopens only the deterministic original reservation.
The installed owner's `recovery_quiescent` callback must bind the reservation,
contract and generation to conclusive custody evidence for the original job and
process. Missing or unknown custody refuses recovery. The lock protects exact
grant/journal comparison, backup revalidation and target readback. Desired bytes
are reconciled without a write even after authority expires. Original before bytes
can retry only the same effect with a valid backup and current authority; a third
state fails closed. Never-started effects retain their approved identities and
need current authority. Every granted effect must be read back before completion.

The supervisor establishes quiescence from owned process/descendant custody,
including after a native crash; a profile's assertion is insufficient. Job
reconciliation retains the original child result and accepts only a matching
completed result with durable quiescence plus installed-owner readback of all
original effects. It never clears launch custody or creates a second job. Resource
custody is released only after this transition. Verification/convergence still
refuse a superseded intent. Public operator recovery commands and actual client
recovery adapters remain later release work.
