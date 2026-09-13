# Invite a developer to Anvil Connect

This small-scale flow uses Authelia's supported password plus WebAuthn login.
The operator delivers credentials and verification codes directly. Email delivery,
enforced first-login password changes, and annual password expiry are not enabled.

## Create the account

On the authentication host, after installing the updated standalone manager:

```sh
sudo anvil-connect-ctl users create developer --email developer@example.test --grant workbench:member --confirm
```

Replace the example identity and resource with the intended developer and a declared
browser service. Repeat `--grant` for additional services. Each grant selects
`member` or `admin` for that service. The account defaults to the `members` group;
`--role admin` selects `admins`, but does not automatically grant other services or
Connect operator privileges.

The command returns a root-only handoff file path. Deliver its contents privately
to the developer, then remove the handoff file. It includes the generated password,
sign-in URL, and service-home URL. Credentials are not printed in command output.
If creation reports that the account exists but access provisioning failed,
inspect the retained account and retry with `users access` and the intended grants;
do not create the same account again.

## Complete the first sign-in

1. The developer opens the supplied service-home URL and signs in with the supplied
   username and password.
2. Ask them to replace their initial password through Authelia's password flow.
   This is a manual step; the file backend does not force it.
3. They register a passkey in Authelia. When Authelia requests identity verification,
   export the current code and deliver it directly:

   ```sh
   sudo anvil-connect-ctl users code developer --confirm
   ```

4. After authentication, the service home shows square tiles for their assigned
   browser services, account/passkey settings, installation, and terminal help.

Codes must have been requested within five minutes. The filesystem notifier
retains only the newest message, so handle invitations one at a time. Password
changes may also require a fresh verification code. See Authelia's
[WebAuthn flow](https://www.authelia.com/overview/authentication/security-key/) and
[file backend](https://www.authelia.com/configuration/first-factor/file/).

## Connect from a terminal

The developer uses **Install Anvil Connect** on the service home and receives the
operator's client configuration and a separately provisioned local caller key.
The operator must also configure the explicit link between the browser identity
and an API principal; browser service grants alone do not grant API access.

```sh
anvil-connect login
```

Open the printed URL, enter the terminal's code, sign in, and approve the requested
access. Leave the command running and point the SDK to its printed local API URL.
Use the local caller key; press Ctrl+C to disconnect. Over SSH, run Connect on the
remote machine where the SDK runs. See the [terminal flow](ANVIL-CONNECT-DEVICE-LOGIN.md)
for client configuration and API-principal provisioning.

## Change access or recover an account

```sh
sudo anvil-connect-ctl users access developer --grant workbench:member --confirm
sudo anvil-connect-ctl users reset-password developer --confirm
sudo anvil-connect-ctl users reset-mfa developer --confirm
```

Run only the operation needed. `access` replaces the complete browser grant list
and invalidates existing Connect browser and terminal sessions. Operators can also
use **Manage access** on the service home. Password reset preserves registered
factors; MFA reset removes that person's passkeys and TOTP for fresh enrollment.
These resets do not themselves revoke existing Connect credentials. Disable Connect
access or revoke sessions first when responding to a lost or compromised device.

Workbench enforces a signed role only after its receiver maps `member` and `admin`
to existing native application principals. Applications using passthrough auth keep
their own internal roles. Follow [the role configuration](cli/connect.md#service-home-and-application-roles)
before treating a grant label as application permission.

Existing applications with their own Authelia sign-in can be retained through
`authelia.additional_oidc_clients` in the deployment manifest. Each entry declares
`client_id`, `client_name`, a protected `client_secret_file`, and HTTPS
`redirect_uris`. These confidential clients use authorization code with PKCE S256,
`client_secret_post`, and the `openid`, `email`, and `profile` scopes. Keep existing
credentials in protected files outside the rendered configuration and state trees.
This preserves application sign-in configuration; it does not grant Connect access.

## Keep recovery copies

```sh
sudo anvil-connect-ctl users backup --include-gateway --confirm
sudo anvil-connect-ctl users schedule --confirm
```

Take a combined snapshot after enrollment, then enable the daily schedule. It
first snapshots Authelia accounts and factors, then snapshots gateway authorities
and entitlements; this is sequential, not atomic. The daily job briefly restarts
Authelia, then stops and restores only the native gateway while Caddy remains up.
Validated archive/receipt pairs retain the newest copy for each of the last 14 UTC
days and seven recent copies. Account, factor, and authority backups are owner-only,
outside Git, local, and unencrypted; they do not protect against disk loss.

The [user command reference](cli/connect.md#users) documents checksum-verified
restore into a fresh private directory, retention, and recovery limitations.
