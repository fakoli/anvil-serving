# Recovery terminal publication previously preceded its linked correction

Status: resolved in the bounded Observatory journal flow on 2026-09-22.

`Console._finish` published a successful recovery as terminal before correcting
the failed original intent. A concurrent operation reader could therefore see a
terminal recovery while the original still required manual recovery.

The shared completion path now publishes a verified linked recovery and its
terminal state in one recovery-specific SQLite transaction. It validates both
stored operation identities, their host/resource pair, and the exact original
intent link before either row changes; a write error rolls both rows back.
Failed recovery verification leaves the original intent unchanged. Focused
regressions retain the failed original execution outcome and evidence, inject a
fault at the terminal row write, reopen the journal, and reject missing or
mismatched linked rows without changing their counterpart.

Retention pruning runs only after publication and cannot recast an atomically
committed recovery as an unknown outcome when cleanup fails.
