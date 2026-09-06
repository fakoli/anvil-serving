# Add bounded managed controller recovery

Status: open; deployment prerequisite, design preflight in progress.

Managed diagnostics prove the selected controller is internally healthy but
has no observed published port. The command catalog has foreground serve,
HTTP status and bounded inspect/log reads, but no selected-controller image
build or container recovery workflow. Model `serves` operations do not own this
service. Router and workbench lifecycle commands target different resources.

A durable fix must be an Anvil Serving CLI operation, not a one-off Docker
script. Resolve the actual existing Compose project/service and configuration
first with narrow read-only ownership metadata; do not assume that a public
example or a tracked private snapshot is the deployed configuration. This
specific missing ownership/recovery inspection justifies the bounded read-only
Docker fallback during design, not raw Docker lifecycle mutation.

Required contract: explicit candidate and recovery image identity, bounded
local-daemon execution, immutable selected configuration, manifest-derived
read-only mount closure, plan freshness and selected-service ownership,
loopback publication independent of unrelated listeners, and identity-checked
postconditions. Any image build needs explicit CPU/memory limits. Apply changes
only to the selected controller; retain the active model and router assignments.
Use a reviewed recovery path if postconditions fail. Remote exposure remains a
separate managed edge step after local endpoint identity succeeds.

Do not stop the unrelated Windows listener, broaden credentials, mutate model
serves, reclaim GPUs, or treat an image label as runtime version proof. Keep
operator configuration and raw observations private; public tests use synthetic
paths, IDs, ports and frozen command results. Source and live acceptance are
separate gates.

## Read-only ownership preflight

Fixed Compose ownership labels identify the selected service as `controller`
in its controller-only project, launched from an older public worktree. Its
referenced Compose file still exists, has one service and thirteen mounts,
and defaults to image version 0.21.1. The selected private operator home does
not contain a controller Compose file. These are not interchangeable sources.

A bounded in-container package version check independently reports 0.21.1;
the internal managed status command reports an OK controller with sixteen
legacy tools but no node/build identity fields. This is stronger evidence than
the matching image label, and still does not prove an externally reachable
endpoint or current capability parity. The managed recovery must therefore
include configuration migration and an actual version upgrade, preserving
durable state and a known selected-service recovery path. No container,
configuration, listener, model or route was changed in this preflight.
