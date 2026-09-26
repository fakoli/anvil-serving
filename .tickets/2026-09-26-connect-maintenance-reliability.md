# Connect backup recovery and legacy deletion wire compatibility

Observed: a paired authentication/gateway backup restarted the gateway before
its identity provider served OIDC discovery. The gateway's first startup exited,
then its supervisor recovered after the manager's readiness deadline. Retained
backup receipts existed despite the operation reporting partial failure.

Wait for the declared HTTPS issuer's discovery document before stopping the
running gateway. Keep TLS verification, reject redirects/wrong issuers, and
bound retries. The stopped-authority backup still briefly interrupts gateway
requests; this change does not claim an online backup.

Separately, native Go JSON omits empty legacy usernames. Normalize this optional
field at the Python wire boundary, reject malformed types, and retain the
principal-to-IdP mapping fence before finalizing any existing deletion intent.
No new account deletion is authorized by this repair.

Regression gates: tests/connect/test_gateway_backup.py and
tests/connect/test_user_delete.py.

## Tunnel investigation

Pinned wstunnel 10.7.1 defers its reverse-TCP WebSocket 101 response until a TCP
connection arrives at the reverse listener (upstream server/server.rs and
server/handler_websocket.rs). The gateway's 40-second response-header timeout
can therefore emit tunnel_establishment_failed during idle demand-driven waits.
A zero-registration status or this event alone is not an application outage.
Keep bounded waits and authority checks; verify real traffic separately.
