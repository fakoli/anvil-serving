# Recovery terminal publication previously preceded its linked correction

Status: resolved in the bounded Observatory journal flow on 2026-09-22.

`Console._finish` published a successful recovery as terminal before correcting
the failed original intent. A concurrent operation reader could therefore see a
terminal recovery while the original still required manual recovery.

The shared completion path now records the verified linked recovery against the
original intent before publishing the recovery terminal state. Failed recovery
verification leaves the original intent unchanged. The focused regression reads
the original at the recovery terminal-publication boundary and retains its
failed execution outcome and evidence.
