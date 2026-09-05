# Close receiver results and rollback failure binding

Status: pure protocol follow-ups specified; implementation pending.

The existing request codec binds operation, node, UUID, plan and trusted target
configuration, but rollback cannot yet carry the failure that initiated it.
That prevents durable attribution of post-restart controller acceptance failure.
T004.4 adds one rollback-only fixed error code; it is evidence, not authority
to revert an arbitrary successful operation. Other operation wire bytes stay
unchanged. This unpublished v1 candidate has no installed migration claim.

T004.3 adds exact bounded result variants and request/result matching. Stage
retries report the current matching operation even after it has advanced;
status can report historical errors without incompatible per-operation error
allowlists. Receiver results never claim controller acceptance. A malformed
request produces a fixed null-identity protocol error rather than guessed data.

The PRD records exact field, phase, permission, canonical-byte and matching
matrices, plus literal-fixture regression gates. File permissions, handle-safe
staging, zipapp packaging and activation remain separate implementation gates.
