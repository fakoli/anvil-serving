# Preserve SQLite locks during private-file verification

The private key-store verifier opened and closed regular-file descriptors while
another connection in the same process could hold a SQLite transaction. POSIX
record locks are process-owned: closing any descriptor for the same inode can
release that connection's locks. A synthetic child writer reproduced the loss
without writing to the live store. Native client readback also opened a second
authentication connection inside its existing writer transaction.

Retain a bounded set of verifier descriptors until the last local connection
closes, while checking the current path, object and permissions on every access.
Fail closed on inherited live connections or uncertain descriptor cleanup.
Authenticate native readback against its existing transaction. On Windows, reuse
held handles within one open while repeating DACL and named-object checks; retain
the existing one-second writer deadline.

Checks cover another instance/thread, nested connections, WAL sidecars, fork,
capacity and failure cleanup. An independent child remains blocked until the
final connection closes. Focused author tests passed 219 cases; independent
verifier/readback tests passed 41. Windows, installed-runtime and live concurrent
staging qualification remain separate delivery gates. The original fatal native
signal is not claimed to have been reconstructed from its missing stack trace.
