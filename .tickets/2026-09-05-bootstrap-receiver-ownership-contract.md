# Close receiver ownership and framed protocol before target staging

Status: pure request framing closed as T004.1; remaining receiver implementation pending.

The PRD defines a pinned, preprovisioned receiver and fixed framed operations,
but the current code contains only plan, manifest, bundle and receipt values.
There is no control_plane/bootstrap_shim.py yet. A receiver that imports the
candidate package before installation could make absent-controller bootstrap
depend on the package it is meant to install; duplicating bundle validation in
a standalone shim would instead create two security contracts.

Before the T004 executor starts, specify the receiver's preprovisioned runtime
and trusted local configuration ownership, its relationship to the shipped
bundle shim, the exact frame schema/operation field matrix, and the durable
operation-binding read/write seam. Keep target configuration out of caller
frames and bundles. Reuse canonical bundle/path validation and fixed errors;
do not assume the remote host has this caller's package or filesystem.

Separate pure frame parsing from staging/activation if necessary to retain
bounded implementation tasks. Tests must cover first enrollment without the
candidate installed, exact byte framing and trailing-data rejection, repeated
UUID binding, trusted target identity/policy, and restart-safe staged state.
No receiver preprovisioning or live installation is implied by this ticket.

T004.1 now owns exact immutable operation-specific frames, canonical bounded
length-prefixed JSON, exact stage length and SHA-256, and no side effects. All
non-identity requests also bind target_config_sha256: an opaque plan hash alone
cannot prove the receiver is still using the previewed paths and policy.
Controller staging and SSH recovery must compare that digest before upload and
again at mutation boundaries. The pure codec is independently buildable while
the remaining packaging, permission and handle-safe staging details are closed.

The receiver will be preprovisioned as a self-contained deterministic zipapp
embedding the sole existing bundle validator. It must not import a not-yet-
installed candidate or duplicate archive validation. Its trusted sibling
configuration derives from the exact private plan domain; credentials remain
separately provisioned and are absent from that document and every frame.
