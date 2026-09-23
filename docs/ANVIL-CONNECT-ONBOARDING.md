# Invite a developer to Anvil Connect

This small-scale flow uses Authelia's supported password setup plus WebAuthn login.
Authelia sends short-lived password-setup links and verification codes through
the configured SMTP provider. Developers choose their own password before signing
in; no usable starter password is saved or delivered. Annual password expiry is
not enabled. Installations without SMTP retain private operator handoffs.

## Create the account

On the authentication host, after installing the updated standalone manager:

```sh
sudo anvil-connect-ctl resources
```

Copy the exact browser resource IDs from this list; application names and resource
IDs can differ. For example, a Workbench installation might use `dashboard`
instead of `workbench`. The list includes URLs and `resource:member` / `resource:admin`
grant values. It describes configuration, not live health or existing access.

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
Without `--confirm`, this command only previews the operation: no account is
created and no email is sent. An applied SMTP operation reports **Email requested;
inbox delivery not verified**. Check the provider's delivery record when the
developer has not received the message; do not repeat account creation.

Managed HTML email presents a **Set up your password** button, a replacement-link
action, and a service-home link when a landing destination is declared. Plain-text
email retains the complete link. Authelia uses the same notification for initial
setup and later recovery, so the message explicitly covers both. Its upstream
subject and password page still use reset terminology; the template does not
pretend to distinguish an invitation from an ordinary password reset.
With passkey login enabled, a new account still must complete this setup because
it has no registered passkey yet.
If creation reports that the account exists but access provisioning failed,
inspect the retained account and retry with `users access` and the intended grants;
do not create the same account again.
Partial failures include a `recovery_hint` describing the step that stopped.
If password setup failed after creation, finish the intended grants with
`users access`, then request a new setup email with `users reset-password`.

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

4. Registering a passkey does not necessarily complete the current authentication
   challenge. Finish sign-in with the registered passkey or the required second
   factor. With `gateway.gateway.portal_host` or `authelia.landing_resource`
   declared, the managed default destination leads to the service home when
   there is no explicit service destination. An existing application sign-in
   destination takes precedence.
5. After authentication, the service home shows square tiles for their assigned
   browser services, account/passkey settings, installation, and terminal help.

If the setup link expires, the developer requests a new password reset on the
Authelia page. SMTP sends the fresh link directly. With filesystem delivery,
the operator runs `users code` to export it; that command recognizes both
password-reset links and passkey verification codes.

The pinned Authelia flow consumes the link while validating the password page,
before the new password is submitted. A refresh or second tab can therefore show
an already-used-link error or leave the password fields disabled. Choose
**Cancel**, then **Reset password?**, and request a replacement instead of
reopening the same link. The email also provides that recovery action; it does
not change the upstream token lifecycle.

Password recovery proves access to the account's email address. Password hints
and security questions are not prerequisites. Changing a known password in account
settings is a different flow from recovering a forgotten password.

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

Inspect local sign-in accounts without restarting services:

```sh
sudo anvil-connect-ctl users list
sudo anvil-connect-ctl users show developer
```

These commands show usernames, emails, groups and disabled status, excluding
password hashes and factors. They do not report Connect grants; use **Manage
access** on the service home to inspect current grants. Account groups do not
establish service access. Use `users access --help` for the replacement semantics
and `users create --help` for invitation options.

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
When both passkey login and its experimental verified-passkey-as-two-factors
option are enabled, a registered
qualifying passkey can continue passwordless sign-in; the emailed link establishes
the new password before the next password sign-in. Use MFA reset when factors
must be removed for fresh enrollment.
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
Keep it owned by that service identity with mode `0600`. The managed service
permits writes to the declared users file and its state directory; an existing
users file can remain in place without making its parent secrets directory writable.
SMTP delivery does not create local code or setup-link handoff files.
The reset endpoint conceals delivery failures to prevent account enumeration;
a successful request alone does not prove receipt. Check provider delivery status
if the message does not arrive.
Distinguish requested, delivered, bounced and unknown; a request receipt is not a
delivery receipt. Provider recipient fields may contain display names, so compare
parsed email addresses when correlating records.

## Repeatable checks without a personal sign-in

From a development checkout, run the existing synthetic identity-provider and
browser-edge contracts:

```sh
go -C connect test ./internal/session ./internal/httpedge -count=1
```

These checks use temporary accounts, state and keys. They exercise OIDC callbacks,
service-home destinations, grants, logout, expiry and protected-document caching
without contacting a real provider or using a personal browser session. The native
CI job already runs these packages. For CLI previews, email rendering and managed
template upgrades, run:

```sh
python scripts/run_tests.py tests/connect/ -x -q
```

The existing Chromium fixtures add actual page interaction. After installing the
[browser gate prerequisites](ANVIL-CONNECT-IMPLEMENTATION.md#reproduce-the-gates),
the synthetic provider suite runs without a real account:

```sh
npm --prefix connect run test:browser -- test/browser.spec.mjs
```

The separate `browser_edge.spec.mjs` suite uses isolated pinned Authelia/Caddy
processes and local notification capture. Root-login redirection runs in that
suite; passkey registration/login uses a virtual WebAuthn authenticator and needs
an explicit opt-in:

```sh
ANVIL_CONNECT_BROWSER_PASSKEY_FIXTURE=1 npm --prefix connect run test:browser -- test/browser_edge.spec.mjs
```

It does not need a personal password manager or inbox. Neither fixture establishes real email
delivery, the original first-password field defect, mobile autofill behavior,
1Password integration, or Open WebUI's question/location UI.

Use automated checks for routine refactors. Repeat the live journey when deploying
authentication behavior or changing the provider, mail delivery, public edge or
password-manager integration; it is not a prerequisite for every local edit.

## Browser acceptance

Validate through the normal Connect URL with a dedicated test account after
deployment. Cover first setup, expired/consumed-link recovery, the actual mobile
username field, password-manager enrollment, completion of authentication, service
selection and a return visit after session expiry. Do not treat a legacy
Observatory input test as proof about Authelia's hosted input.

Authenticated browser documents must not survive session expiry in the browser
cache. Connect marks those responses `no-store`; native asset caching remains
available. A previously cached application shell can otherwise display a generic
error while protected API requests correctly return 401. Existing cached copies
are not erased by a server update, so include a fresh navigation in acceptance.

For Open WebUI, separately exercise its native question card, answer/cancel, and
location permission denial. A missing prompt can result from the selected tool
integration, chat state, browser transport or permissions. Inspect the concrete
request before changing proxy settings. The repository's
`.tickets/2026-09-20-connect-onboarding-ux.md` records version-specific evidence
and unresolved acceptance gates.

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
