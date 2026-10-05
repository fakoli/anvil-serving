# Media job reads can combine different committed states

The blocking A2A task test failed while polling nonterminal jobs during backend
submission. `MediaJob` correctly rejected a row whose state did not match its
last event. `MediaJobStore.get` read the job, events and artifacts using three
autocommit queries, allowing an independent transition to commit between them.
The writer's atomic transaction does not give separate reader queries a shared
snapshot.

Start one deferred read transaction before the existing three queries. The
connection context commits or rolls it back at exit; the WAL store still
permits concurrent writers. Principal checks, transition validation, schema,
error behavior and lifecycle authority remain unchanged.

A deterministic regression commits a transition through a second store after
the reader obtains the job row and before its event query. It reproduces the
original exact contract error before the fix. Afterward, the first read returns
the complete accepted snapshot and the next read returns the complete queued
snapshot, including matching timestamps and ordered history. Existing media and
A2A tests remain the integration gates.
