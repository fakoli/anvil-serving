# Windows installed-Pi smoke cannot initialize or poll the native runner

Status: open; unrelated to the Media MCP changes.

The full Windows suite reaches the optional installed-Pi 0.85.1 smoke and
fails with exit 134. Its restricted fixture environment omits `SystemRoot`;
a bounded reproduction captures Node's `ncrypto::CSPRNG(nullptr, 0)` assertion.
Passing only that required OS variable removes the abort but the RPC response
still times out: `PiRpcClient._default_read_chunk` uses `select.select` on a
subprocess pipe, which Windows does not support, and swallows the resulting
`OSError` into an empty read. Both paths are unchanged from the base revision.

The temporary environment-only correction was removed because it does not
repair the complete native transport. No test skip or production workaround
was added. Hosts without the pinned installed Pi skip this optional smoke.

Acceptance: preserve necessary OS runtime variables in the isolated fixture;
implement bounded, nonblocking Windows pipe reads; prove startup, request,
history, fork, resume, and cleanup against the pinned real Pi fixture. Keep
credentials isolated and retain equivalent POSIX behavior.
