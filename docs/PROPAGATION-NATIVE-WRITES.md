# Native propagation writes

The propagation foundation provides an in-process owner fence around direct
client catalog writes and Pi media withdrawal. It is not a deployed fleet writer.
Legacy callers remain unenrolled until the reviewed single-writer cutover.
An apply that supplies an owner contract or grant but omits the native fence
refuses before entering a legacy writer; previews remain read-only.
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

## Bounded controller jobs

The controller can bind a `PropagationService` at construction. The default
controller has no execution profile and declares propagation unavailable.
Installation and the operator start/status commands are separate release work;
these internal interfaces do not establish deployment readiness.

The owner can resolve one canonical approved contract through
`PinnedApprovedContract`. Its expected SHA-256 must come from a separately
reviewed, protected installation profile; it cannot be computed from the
incoming request or the file at runtime. Every lookup rechecks the file pin,
ownership and path custody, so replacement or revocation fails closed. This
resolver is not yet wired into `controller serve` and does not itself establish
active model identity or authorization to apply a fleet effect.

`ObservedActiveIdentity` is a separate read-only owner input. It compares the
authenticated router's current catalog digest with the installed expectation,
matches an exact previously approved container ID and pinned Docker start-input
digest (image, recipe labels, arguments, mounts, ports and GPU requests), checks
Docker's requested and observed loopback port binding, then asks that endpoint
for the exact served ID. Only a complete match returns the deterministic
activation fingerprint. The protected installation profile must supply and pin
the activation receipt and its runtime digest before this input can be bound to
`controller serve`; the default controller remains unavailable. Environment
values are omitted from the public fingerprint; the exact container ID pins
the immutable Docker environment from the approved activation.
The activation receipt must also prove the selected model artifact/revision and
its mount custody; matching container start inputs and a served alias alone do
not prove the loaded weight bytes. Missing artifact evidence refuses production
binding.
The observation is point-in-time evidence, not a mutation-boundary fence;
native effects still require a fresh current-authority check under their lock.
Production binding also needs one aggregate observation deadline; the HTTP
socket timeouts here are per exchange, not a whole-call deadline.
Its container must share the intended host network namespace with the model
loopback listener; an installed cross-namespace proof remains required.

The bound service exposes `propagation.accept.v1`,
`propagation.dispatch.pending.v1`, `propagation.dispatch.record.v1`, and
`fleet.propagation.preview.v1`, `fleet.propagation.submit.v1`,
`fleet.propagation.status.v1`, `fleet.propagation.verify.v1`,
`fleet.propagation.convergence.v1`, `fleet.propagation.cancel.v1`.
Admission, dispatch, activity and status credentials have separate scopes.
Requests contain opaque owner references; profiles, executable pins, transport
credentials and approval resolution come from the installed owner configuration.

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

Direct catalog rendering of a declared Hermes YAML file uses the bounded file
writer. Hermes CLI-backed profile/media mutation is unavailable under the fence:
it returns `UnsupportedCapability` before creating a lock, reservation, journal,
backup, runner or restart. This refusal also applies to fenced preview calls.
The orchestration layer must retain each declared profile as unsupported or
pending, with its pinned identity; it cannot omit it or claim convergence.

The existing unfenced Hermes path is legacy pre-cutover behavior. It is not
fenced propagation evidence. Release task T011 must deliver an owner-resolved,
bounded per-profile writer and its capability/readback evidence before Hermes
integration or cutover is accepted. That writer must preserve unrelated provider,
credential, account and session state and use the same effect/backup custody.
Loaded-session observation and idle-gated reload remain separate requirements.

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
