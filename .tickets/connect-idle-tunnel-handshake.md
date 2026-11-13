# Connect reverse-tunnel idle handshake repair

The pinned wstunnel reverse TCP backend waits for application demand before
returning HTTP 101. The gate's 40-second response-header timeout and 45-second
socket read timeout interrupted legitimate pending demand, causing repeated
establishment failures and retries even when the origin could subsequently serve.

The gate now waits under its existing authenticated request lifetime, renewable
lease watcher, cancellation and admission slots. Dial, TLS, local admission and
write limits remain; validated upgrades arm the existing read idle limit.
Other relay callers retain their previous behavior. No warning is suppressed.

The managed event reader also projects only required journal fields and caps
observations at the native 64-event ring size. Full journal metadata previously
overflowed the unchanged 64 KiB subprocess bound and hid existing events.
Invocation checks and closed event decoding remain; this is bounded observation,
not complete history. Generic log availability is independent.

Verification: native gate/relay race tests cover pending capacity, revocation,
expiry, cancellation and the read-deadline transition. The explicit pinned-binary
lab `TestManagedTunnelGateIdlePendingHandshake` waits 46 seconds before demand,
then verifies streaming and revocation. Its predecessor reproduces the original
40-second failure. Manager tests cover oversized journal metadata projection.

These are source and isolated transport checks. Installed deployment and
authenticated browser acceptance require separate verification.
