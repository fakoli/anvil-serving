# Connect broker construction fails during closed managed startup

Enabling the native Connect key broker on an instrumented managed router reaches
`ConnectKeys.migrate` before its new KeyStore handle is bound to the assembled
owner. External writer custody rejects the deliberately closed successor. A
subsequent restart encounters the resulting unanchored run and remains held.

Fix broker assembly using the existing storage-only completion permit. Preserve
closed request admission and verify that no request can inherit that permit.

The live failed start has no request rows, but that alone is insufficient death
proof. Recovery must use the native owner, retain all committed authority and
accounting, preserve the predecessor transfer history, and obtain independent
physical owner evidence. Do not fabricate a live anchor, change the usage domain,
disable accounting, or restore an older snapshot over newer state.

Status: startup regression reproduced and a focused fix is under validation;
recovery of the already incomplete startup is still required.
