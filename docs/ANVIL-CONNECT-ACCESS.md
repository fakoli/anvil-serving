# Manage Connect access in Observatory

Observatory's **Access** screen lets an explicitly configured administrator
manage existing Connect users and revoke individual browser or terminal
sessions. The Observatory name remains unchanged.

## Use the Access screen

Sign in through Connect and open **Access** in Observatory's navigation.

- **Users** lists existing Connect identities and their allowed browser resource IDs.
  Keep at least one declared browser resource (wildcards are unsupported).
  Save resources to change access, or disable access to revoke that identity's
  Connect authorization. Enable access to restore the declared grants.
- **Connect sessions** lists issued browser and terminal access with its session
  ID, resource and expiry. **Disconnect** revokes the selected session. Match a
  terminal session to the non-secret session ID printed by `anvil-connect login`.
- **Refresh access inventory** reads the current inventory. Results are paged;
  they describe issued authorization and do not establish that a device is online.

Changes require confirmation. If another administrator changed the record,
reload the inventory and review the new state before trying again. Do not
repeat an ambiguously delivered change automatically.

Disabling access retains the Connect identity as a disabled record. It does
not delete the identity provider's account or its passkeys. This screen manages
existing identities; creating a new sign-in account is a separate enrollment
workflow. Revoking a browser session also invalidates terminal sessions that
it approved. Revocation blocks subsequent admission and closes admitted
streams; it cannot stop work already executing inside an upstream model.

Disconnecting a session does not clear the identity provider's single sign-on
session. A user whose account remains enabled can sign in again.

## Enable administration

In the private managed deployment, set `gateway.gateway.browser_administration`
to the intended existing browser resource and exact opaque operator identities:

```json
{
  "browser_resource": "dashboard",
  "operators": ["human:OPAQUE_CONNECT_PRINCIPAL"]
}
```

The browser resource must permit both `GET` and `POST`. This declaration does
not grant access to the resource: each operator must also be an enabled Connect
human with a current session and the required resource grant. API resources
and other browser resources keep their own method and permission boundaries.

Set `connect_access: true` in the private Observatory configuration after
configuring its `authentication.mode` as `connect`. The advertised Access path
only enables navigation; the gateway independently authorizes every request.
Omitting either optional setting preserves the existing deployment behavior.

Use real opaque IDs only in private operator configuration. Do not put real
network identities, credentials or account state in public examples.

## Authority and recovery

The gateway handles the exact browser-resource-relative
`/_anvil-connect/access` endpoint. Observatory does not receive the local admin
socket, identity-provider database, signing keys or arbitrary file access to
perform these operations.

The gateway checks the acting administrator and updates a target within one
authority transaction. Changes use the target's current generation and a
request ID. The last enabled configured administrator with access to the
administration resource must remain available; this protection also applies
to local human updates.

An individual terminal revocation is limited to device-issued credentials.
It does not expose or revoke unrelated manually issued API keys. Inventory
responses contain identity and authorization metadata, never bearer tokens,
browser cookies, approval codes or passkey material.

The first version keeps administrator membership, resource definitions,
identity-provider enrollment and recovery policy in managed private deployment
configuration. These are distinct from day-to-day user resource grants and
session revocation.
