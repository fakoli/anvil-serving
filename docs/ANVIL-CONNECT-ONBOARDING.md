# Invite a developer to Anvil Connect

This small-scale flow uses Authelia's supported password plus WebAuthn login.
Authelia sends short-lived password-setup links and verification codes through
the configured SMTP provider. Developers choose their own password before signing
in; no usable starter password is saved or delivered. Annual password expiry is
not enabled. Installations without SMTP retain private operator handoffs.

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

With SMTP configured, the command asks Authelia to email the developer a
password-setup link. Without SMTP, it returns a root-only handoff file path;
deliver that file's contents privately and remove it. The link expires after five
minutes and can be used once. Credentials are not printed in command output.
Authelia must be running
so it can issue the setup link; the command refuses an inactive provider before
changing the account.
If creation reports that the account exists but access provisioning failed,
inspect the retained account and retry with `users access` and the intended grants;
do not create the same account again.

## Complete the first sign-in

1. The developer opens the supplied password-setup link and chooses a password in
   Authelia. They cannot sign in using a supplied starter password: none is handed
   out, and the random bootstrap value is discarded after hashing.
2. They open the service-home URL and sign in with their username and chosen
   password.
3. They register a passkey in Authelia and enter the verification code emailed to
   them. With filesystem delivery, the operator exports and delivers the code:

   ```sh
   sudo anvil-connect-ctl users code developer --confirm
   ```

4. After authentication, the service home shows square tiles for their assigned
   browser services, account/passkey settings, installation, and terminal help.

If the setup link expires, the developer requests a new password reset on the
Authelia page. SMTP sends the fresh link directly. With filesystem delivery,
the operator runs `users code` to export it; that command recognizes both
password-reset links and passkey verification codes.

Links and codes must have been requested within five minutes. The filesystem notifier
retains only the newest message, so handle manual invitations one at a time. Password
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
use **Manage access** on the service home. Password reset invalidates the old
password, Connect browser sessions, pending browser sign-ins, and human-approved
terminal credentials, then requests a new password-setup link. It preserves groups,
service grants and registered factors. Old welcome-password handoffs for that
account are removed after a successful replacement.
MFA reset removes that person's passkeys and TOTP for fresh enrollment; it does
not revoke Connect credentials. Suspend the account first when responding to a
lost or compromised device. Separately issued API keys need separate revocation.

Workbench enforces a signed role only after its receiver maps `member` and `admin`
to existing native application principals. Applications using passthrough auth keep
their own internal roles. Follow [the role configuration](cli/connect.md#service-home-and-application-roles)
before treating a grant label as application permission.

Suspend a developer without discarding their password or passkeys, or remove
their account and factors:

```sh
sudo anvil-connect-ctl users suspend developer --confirm
sudo anvil-connect-ctl users delete developer --confirm
```

Both back up authentication state and invalidate Connect browser and
human-approved terminal credentials. Use `users access` with the intended grants
to resume a suspended account. Deletion retains backups and disabled authority
history; retained OpenID identifiers reserve the username against reuse.
Application data and separately issued API keys need separate removal/revocation.

Existing applications with their own Authelia sign-in can be retained through
`authelia.additional_oidc_clients` in the deployment manifest. Each entry declares
`client_id`, `client_name`, a protected `client_secret_file`, and HTTPS
`redirect_uris`. These confidential clients use authorization code with PKCE S256,
`client_secret_post`, and the `openid`, `email`, and `profile` scopes. Keep existing
credentials in protected files outside the rendered configuration and state trees.
This preserves application sign-in configuration; it does not grant Connect access.

## Configure email delivery

Add `authelia.smtp` to the private deployment manifest, then use the normal managed
preview/apply flow. For example, Resend accepts its API key as the SMTP password:

```json
{
  "address": "submissions://smtp.resend.com:465",
  "username": "resend",
  "password_file": "/etc/anvil-connect/secrets/smtp-password",
  "sender": "Anvil Connect <auth@access.example.test>"
}
```

Use a verified sending domain. Store the key outside Git in a root-owned file,
grouped to the declared Authelia identity with mode `0640`. TLS and certificate
verification remain required. No mailbox server or separate mail client is needed.
See [Resend SMTP](https://resend.com/docs/send-with-smtp) and
[Authelia SMTP](https://www.authelia.com/configuration/notifications/smtp/).

The users file must be writable by Authelia for self-service password changes.
Keep it inside `authelia.state_directory`, owned by that service identity with mode
`0600`; the managed service permits writes there without opening the shared secrets
directory. SMTP delivery does not create local code or setup-link handoff files.
The reset endpoint conceals delivery failures to prevent account enumeration;
a successful request alone does not prove receipt. Check provider delivery status
if the message does not arrive.

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
