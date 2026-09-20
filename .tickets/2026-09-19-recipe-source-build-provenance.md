# Source-built recipe binary provenance is absent from managed status

The NInfer challenger recipe builds inside a digest-pinned CUDA base image.
Managed recipe status identifies the base image and model revision, but cannot
report the resulting executable digest. A revision text marker in a mutable
build volume does not prove a reused executable's identity.

For the bounded investigation, read only the exact executable SHA256 through
Docker exec after a successful cold build. No raw Docker lifecycle mutation is
authorized by this exception. Preserve the digest in the next exact candidate
recipe and return to managed lifecycle commands. This is an intake integrity
check; it does not replace a baked pinned runtime or clean recreation evidence.

Durable product gap: support a declared, bounded runtime provenance probe in
recipe status, with output validation and no arbitrary operator command
execution. Acceptance should detect a changed binary even when the source
revision marker is unchanged. Promotion remains blocked until clean runtime
recreation is proved.
