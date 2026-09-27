# Native propagation writes

The propagation foundation provides an in-process owner fence around direct
client catalog writes and Pi media withdrawal. It is not a deployed fleet writer.
Legacy callers remain unenrolled until the reviewed single-writer cutover.

The owner supplies a canonical approved contract, generation, catalog digest,
resource identity and exact effect-to-file mapping. A private permanent index and
operation journal share one owner/resource lock across callers. Expired authority,
stale generations, conflicting identities and unresolved operations fail closed.
An interrupted transaction retains its original effect records for recovery;
it does not automatically compensate or claim that an external effect stopped.

Each write retains the bytes used for rendering, binds a verified private backup
and manifest to those originals, checks for intervening edits, atomically replaces
the file, and reads it back. POSIX operations use held directory descriptors and
no-follow relative opens. Windows retains non-reparse directory handles and
validates native permissions. Unknown or unsafe custody is refused. A successful
file readback does not establish that an existing client session loaded the file.

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
