# OS-supervised Anvil features require commands outside Anvil Serving

**Status:** Product implementation verified. Live adoption remains pending a
validated private operator workspace; this deployment ticket stays open.

## Observed behavior

On 2026-09-05, a macOS operator could inspect running Anvil voice model,
speech recognition, speech synthesis, controller, and event services only by
combining OS process/listener inspection, LaunchAgent metadata, and direct
endpoint reads. Starting, stopping, restarting, and changing automatic startup
required explaining `launchctl` to the operator.

The voice manifest declared STT, TTS, and the Realtime proxy as `external`.
The general serve loader explicitly rejects `runtime = "native"`. The voice
native implementation owns processes it starts itself; it does not own the
existing OS supervision registrations. A daemon can also be running with no
model loaded, so a listener or process alone does not establish model residency.

Verified against Anvil Serving 1.0.0 and product commit
`26fffff7fef3ee951b585c77448970317d5f81df`.

This record intentionally omits operator hostnames, service identifiers, home
paths, network identities, credentials, and raw process arguments. No live
service was changed while preparing the design.

## Required outcome

An operator or agent can discover, explicitly adopt, inspect, start, stop,
restart, read bounded logs, and configure automatic startup for an Anvil
feature through the CLI and equivalent typed MCP/controller operations.
OS supervision, engine behavior, and endpoint dialect are independent declared
contracts. The existing `serves`, recipe, and `voice` commands reuse those
contracts rather than introduce another lifecycle owner.

Read-only discovery never grants mutation authority. Model-serving lifecycle
retains topology, resource admission, exact identity, and confirmation gates,
including when invoked through a generic host-service command.

The operator's platform scope is Windows/Docker, macOS/MLX or Docker, and
Linux/Docker. Vast.ai/Runpod and AWS/Azure remain TBD. Existing macOS speech
services are explicitly approved for adoption as legacy supervision bindings;
engine migration is separate. Do not expand this ticket into native Windows
or Linux model supervisors, Podman, or cloud provisioning.

## Design and acceptance

See the [portable service lifecycle design](../docs/superpowers/specs/2026-09-05-portable-service-lifecycle-design.md)
for the command contract, migration, OS/engine support matrix, and verification.
This expands the implementation work required by
[ADR-0034](../docs/adr/0034-fleet-control-plane-and-node-runtime-classes.md),
including non-model Anvil services.

The ticket is not closed by adding a launchd wrapper alone. Closure requires
the shared lifecycle path, CLI/MCP/controller parity, honest OS/engine support
reporting, declared legacy-service adoption, and verified end-to-end operation
of the affected deployment through Anvil Serving tools.

## Implementation evidence

The shared supervisor/engine contracts, CLI and MCP operations, native serve and
recipe bindings, voice service bindings, and operator configuration closure are
implemented on the feature branch. An opt-in isolated macOS launchd fixture passed
install, up, status, logs, restart, disable, enable, down, and cleanup. Supervisor
fixtures exercise Docker behavior; no Windows or Linux live runtime was available
for this change. No real model or application service was restarted or adopted.
The full regression gate passed with 5,019 passed and 18 skipped tests; generated
CLI references and packaged scaffold checks passed. Independent review approved
the implementation after the identified ownership and engine-mismatch fixes.
