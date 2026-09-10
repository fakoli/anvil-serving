# One browser identity for Connect and Observatory

Connect can hand a verified human identity to Observatory through the explicit
`signed-identity` browser resource mode. Existing `passthrough` applications and
API bearer delegation keep their original behavior. Observatory still owns its
resource/action permissions and CSRF policy.

## Configuration

Set the same browser rule's `native_auth` to `signed-identity` at the gateway
and connector. On the gateway resource only, declare `identity_key_env` and
`identity_key_id`. The deployment's `environment_files.gateway_identity` names
a separate private environment file loaded only by the gateway service.
Use a different 32-byte random signing key for each resource, encoded as
canonical unpadded base64url. Never put key values in declarations or bundles.

Opt in through Observatory's authentication configuration:

```json
{
  "authentication": {
    "mode": "connect",
    "connect": {
      "resource": "dashboard",
      "keys": [{"id": "dashboard-v1", "secret_env": "ANVIL_DASHBOARD_IDENTITY_KEY"}],
      "principals": {"human:OPAQUE_CONNECT_PRINCIPAL": "existing-operator"}
    }
  }
}
```

Replace the synthetic principal with the exact opaque ID returned by Connect's
human administration surface. The mapped user must already have explicit native
grants. There is no email matching, first-user administrator, or implicit API
permission. Real mappings belong in private operator configuration.

At verified session bootstrap, Observatory creates an idempotent profile for
the Connect principal. Unmapped profiles have no resource/action grants and
cannot read fleet data. Profiles do not provide independent tenant data
isolation. Explicitly mapped operators retain their configured permissions.

## Request and session contract

The gateway strips caller identity headers and signs after a fresh Connect
admission check. The connector accepts the assertion only over its authenticated
gateway mTLS path for the fixed resource, strips ordinary identity headers
again, and injects the retained assertion at the origin. Origin responses cannot
return the assertion to the browser.

`X-Anvil-Connect-Identity` carries
`acai1.BASE64URL_JSON.BASE64URL_HMAC_SHA256`. The MAC covers the literal `acai1.`
prefix and encoded JSON. The closed payload contains version, issuer, key ID,
opaque subject, Connect session ID/generation, principal generation, epoch,
resource, public host, method, SHA-256 of the exact request URI, issue/expiry
times, Connect session expiry, and a random assertion ID. It lasts at most
30 seconds and never outlives the Connect session. There are no OIDC tokens,
passwords or role/action grants. Go and Python tests share a fixed wire vector.

Observatory validates every protected request and binds its Secure/HttpOnly
cookie to the Connect subject/session/generations/epoch tuple. Same-origin and
CSRF enforcement remain; reused mutation assertions are rejected. Password
login is disabled only in Connect mode. Session bootstrap is bounded and
idempotent under concurrent requests and replaces stale browser bindings.

Logout submits a same-origin POST to Connect, invalidates Connect sessions and
active admissions, and presents an explicit sign-in link. Authelia's separate
session may still exist; subsequent sign-in can reuse it. A leftover native
cookie alone cannot authorize a request.

This deployment supports one administrative trust domain. If gateway and
connector share an OS account, logical key distribution does not isolate them
against compromise of a process under that account. Use separate identities
and protected origins before making stronger isolation claims.

## Passkeys

The optional managed `authelia.webauthn` declaration supports:

```json
{
  "enable_passkey_login": true,
  "experimental_enable_passkey_uv_two_factors": true,
  "discoverability": "required",
  "user_verification": "required"
}
```

Authelia 4.39.20 accepts this configuration with its existing `two_factor`
policy. The verified-passkey-as-two-factors option is experimental and
unsupported upstream; pin and validate upgrades before activation. See
[Authelia's WebAuthn reference](https://www.authelia.com/configuration/second-factor/webauthn/).
Synced passkeys are permitted and existing recovery factors remain. The user
performs enrollment and the device's biometric/PIN prompt.

1Password can store the site's passkey and offer it through its browser
extension. See [1Password's instructions](https://support.1password.com/save-use-passkeys/).

## Deployment and rotation

Provision the separate key without changing existing bootstrap receipts. Make
Observatory accept the candidate key ID alongside the current one, then
activate the gateway signer with the new ID. Verify session, direct-access
denial, wrong-resource, CSRF, logout and stream revocation paths. After the old
assertion lifetime plus clock skew, remove the old verifier entry. At most two
verifier keys are accepted.

Auth configuration participates in the native policy digest, so changes can
invalidate native sessions. Allow browser rebootstrap during rotation; do not
promise uninterrupted mutations. Roll back configuration and verified binaries
together while preserving current revocations, databases and profiles.
