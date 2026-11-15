# Router recovery status rejects supported config artifact names

Status: fixed in source; live recovery and deployment remain separate gates.

## Observed

After a host reboot, the read-only `router recovery-status` diagnostic refused
an otherwise supported managed-router deployment because the host-side source
of the read-only `/etc/anvil/config.toml` bind did not have the literal basename
`router.toml`. Managed maintenance deployments may retain revisioned or
candidate configuration artifact names while mounting their exact bytes at the
canonical container destination. The reboot-stop implementation already binds
that destination, source bytes, configuration digest, Compose identity and
protected custody without imposing a host basename.

This refusal blocked the supported helper that emits the exact sidecar snapshot
digest needed to prepare reboot recovery. It did not authorize reading the
protected store through an ad hoc replacement.

## Resolution

Accept any absolute host config source without a `..` component after the
existing mount contract proves one read-only bind at the canonical container
destination. The existing bounded reader still rejects symlink components,
non-regular files, files that change during the read and oversized inputs.
Configuration parsing, managed-owner declaration, exact digest correlation,
mount/storage custody and final byte-for-byte stability checks remain intact.

Focused regression coverage uses a revision-style `candidate.toml` source and
passes through the complete read-only sidecar diagnostic. This source fix does
not grant recovery authority, stop or start a router, or acknowledge remote
memory uncertainty.
