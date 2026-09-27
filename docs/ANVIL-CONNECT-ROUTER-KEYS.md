# Router API keys from Connect Home

Open **Home → Router API keys → Request router access**. An explicitly configured
Connect operator approves the account's models, endpoints, requests per minute,
and maximum key lifetime. Browser service access alone never grants router access.

After approval, choose **Create API key**, name a device or app, select its models
and lifetime, and choose **Create key**. Endpoint permissions and a lower request
rate are available under **Limit permissions and request rate**. Copy the key immediately into protected credential
storage; it cannot be retrieved later. Home lists your keys, expiry, grants and
recent usage (up to 100 keys, active keys first). **Revoke key** stops subsequent
requests. If a create response is lost, refresh the list and revoke the unused key before creating another.

The resulting `ask_` key authenticates the router directly, through an already
provisioned network path. It does not establish a tunnel, replace the local key
used by `anvil-connect login`, or authorize the Connect transport itself. Ask the
operator for the reachable router base URL. Existing terminal login continues to
use its own credentials and permissions.

## Operator setup

This feature is opt-in and requires compatible Connect and router builds. In the
private Connect deployment, configure `gateway.gateway.portal_host`, the existing
`browser_administration` operator list, and this additional declaration:

```json
"router_keys": {
  "url": "http://127.0.0.1:8000",
  "secret_env": "ANVIL_CONNECT_ROUTER_SIGNING_KEY",
  "check_env": "ANVIL_CONNECT_ROUTER_CHECK_KEY"
}
```

`url` is the router authority, without a path. HTTP requires explicit
`127.0.0.1`; remote endpoints require verified HTTPS. Loopback is relative to the
Connect process. Use the managed deployment's protected gateway EnvironmentFile
for the references, then apply the deployment through `anvil-serving connect`'s
normal managed preview/apply workflow.

In the router's private `router.toml`, alongside its existing `auth_env` and
initialized `api_keys_path`, configure:

```toml
[server]
auth_env = "ANVIL_ROUTER_API_KEY"
api_keys_path = "/var/lib/anvil-serving/router-keys/keys.sqlite3"
connect_keys_env = "ANVIL_CONNECT_ROUTER_SIGNING_KEY"
connect_check_env = "ANVIL_CONNECT_ROUTER_CHECK_KEY"
connect_home_url = "https://home.example.test"
```

Provision the **same two references** separately into both service environments.
The signing value is 32 random bytes encoded as unpadded base64url. The account
check value is a different random bearer of 32–256 printable ASCII characters.
Neither may reuse the router master credential, OIDC secret, or another resource's
signing key. Credentials never belong in deployment JSON, TOML, Git or browser
configuration. The router must reach the declared Home URL with verified TLS. This URL must
match its canonical hostname, without an explicit port or path.

Home uses its signed account session to call the fixed router broker endpoint.
Only explicitly listed, enabled operators with the administration resource grant
can approve or remove access. **Manage router access** shows pending requests
first, with current usernames resolved by Connect and the account ID available
for verification. Names are not copied into the router database. Changed or
deleted accounts cannot be approved until a fresh request is made. Every approval edit invalidates that account's existing
router keys; **Save limits and revoke keys** requires confirmation. Stale policy
revisions fail rather than overwrite another edit.
Under **Record options**, **Remove inactive record** frees a denied or deleted account record when the
256-record capacity is reached. Deny enabled accounts first. Retained request
metadata is preserved, and removed records cannot reuse an old policy revision.

## Limits and revocation

- At most 10 active keys per account, 256 account records and 1,024 router key
  records. Operator-approved key lifetime is at most 90 days. The broker supports
  up to 256 catalog models and 8 KiB of serialized model/endpoint grants per account.
- Each key has a token bucket and all keys share the approved account bucket.
  Creating more keys cannot multiply the account's request budget. Catalog reads
  also count. `429` includes `Retry-After`.
- Account disable, deletion, generation changes and authority recovery deny new
  requests. A non-reusable account incarnation prevents deleted and recreated
  accounts from reviving old keys or reading their usage. Changed accounts must
  request approval again. Signing out alone does not revoke device keys.
- Approval removal and key revocation stop subsequent admission. Already admitted
  streams may finish; this is the router key contract, separate from Connect's
  continuously checked transport-session contract.
- Connect-owned requests perform a bounded, uncached account check. If Connect is
  unavailable, they fail closed. A separate concurrency lane prevents slow checks
  from occupying ordinary device-key authentication capacity.

Usage shows request counts, HTTP errors, rate-limit responses, last use and average
elapsed time from the retained metadata log, capped at 10,000 requests across the
router. It is not a billing ledger, token accounting or proof of stream completion.
Prompts, response content and credentials are excluded. Metadata recording is best
effort under store contention, so counts can be incomplete.

## State and rollback

Enabling the broker transactionally upgrades the existing key database from schema
1 to schema 2, preserving manually issued keys. Take a protected database backup
with the router stopped before deployment. Previous router binaries reject schema
2; do not change the schema marker or remove ownership records to force a rollback.

To disable this feature while retaining the upgraded build, remove its optional
configuration from both services. Ordinary keys keep working; Connect-owned keys
fail closed without the account checker. A rollback to an older binary requires
restoring the complete pre-upgrade database. That also restores its credential
revocation state: review/reapply any intervening revocations before admitting traffic.
Restoring Connect authority state must use the existing recovery procedure that
rotates its authority epoch. No live deployment is implied by source validation.
