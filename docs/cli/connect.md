# Anvil Connect commands

`anvil-serving connect` manages application access for API clients and browsers.
It is the Anvil Connect product family. A local deployment JSON selects fixed
resources, component binaries, service identities, and secret references.
The existing `edge` commands continue to manage Tailscale independently.

This delivery initially targets Linux amd64. Native Connect, Caddy, Authelia,
and wstunnel run as separate processes. The Python package adds no runtime
dependencies and does not download or start those binaries when imported.

Deployment commands require `--manifest /absolute/deployment.json`. `--service` selects
`gateway`, `connector:ID`, or `client:ID` from that manifest. These are local
deployment selectors; the command does not perform implicit SSH or controller
dispatch. Mutations preview by default; `--confirm` applies the operation and
`--dry-run` keeps it a preview even when both flags are supplied.

The standalone `anvil-connect-ctl` displays help when run without arguments.
`users` lists account operations, and `users create --help` (or another operation)
shows only relevant arguments. Examples put options after the operation; existing
option-first user commands remain accepted. Invalid arguments
point to the relevant help without echoing private operands. Standalone command
results remain JSON, now indented for readability.

## Resources

```sh
sudo anvil-connect-ctl resources
```

Lists declared browser resource IDs, URLs, native authentication modes and
copyable `resource:member` / `resource:admin` values. Use these exact IDs with
`users create --grant` or `users access --grant`; application names need not match
resource IDs. API resources are excluded because browser grants do not authorize
API access. Passthrough applications retain their own internal permissions.

The command defaults to `/etc/anvil-connect/deployment.json`, accepts `--manifest`,
and works identically as `anvil-serving connect resources`. It reads only the
declaration; it does not contact services, read referenced secrets or establish
live readiness or current access. A deployment without browser resources returns
an empty list.

## Native OIDC clients

Add a native application to `authelia.additional_oidc_clients` with this entry
([complete entry in the repository](https://github.com/fakoli/anvil-serving/blob/main/connect/examples/native-oidc-client.json)):

```json
{
  "client_id": "native-example",
  "client_name": "Native Example",
  "public": true,
  "redirect_uris": ["app.example.connect.ios:/oauth/callback"],
  "groups_scope": true
}
```

`public` is a boolean and defaults to `false`. A public client must omit
`client_secret_file`; the native application holds no client secret. Omitting
`public`, or setting it to `false`, preserves the existing confidential profile,
which requires a protected secret file and exact HTTPS callbacks.

Public callbacks use an exact lower-case reverse-DNS scheme, one slash after the
colon, and a canonical non-root path. Register the identical URI in the
application. This profile rejects web and loopback callbacks, authorities,
wildcards, escapes, query strings, fragments, and ambiguous paths. The private-use
scheme follows [RFC 8252 section 7.1](https://www.rfc-editor.org/rfc/rfc8252#section-7.1).
Client IDs remain unique across additional clients and the Connect gateway.

Rendering fixes the public client to authorization code, PKCE `S256`,
`token_endpoint_auth_method: none`, explicit consent, two-factor authorization,
and RS256 ID tokens. Allowed scopes are `openid`, `email`, and `profile`, with
`groups` only when `groups_scope` is `true`. Refresh and implicit grants are not
part of this profile. Authelia's existing public subject identifiers are retained.

Preview the managed gateway configuration after editing the deployment:

```sh
anvil-serving connect up --manifest /etc/anvil-connect/deployment.json --service gateway --dry-run
```

Use the existing confirmed gateway update after reviewing that preview. Client
registration supplies sign-in identity; service grants and a receiving
application's explicitly trusted token exchange remain separate policies.
Server introspection credentials stay in that server's protected configuration.

## Native Memory service

`connect native-memory-service` owns one personal OIDC Memory gateway as an
exact three-unit systemd deployment: the gateway, a root authority projector,
and its ten-second refresh timer. It is deliberately separate from the Connect
gateway lifecycle, Capability Gateway, model services, and Tailscale edge
publication.

Preview and apply a protected declaration:

```sh
sudo anvil-serving connect native-memory-service install --declaration /protected/native-memory-service.json
sudo anvil-serving connect native-memory-service install --declaration /protected/native-memory-service.json --confirm
sudo anvil-serving connect native-memory-service status --declaration /protected/native-memory-service.json
```

The declaration is root-owned, non-writable, outside Git, and contains paths and
SHA-256 digests rather than credential values. It pins a single zipapp, Python
interpreter, gateway configuration, Memory receipt, introspection secret,
installed Connect manager, fixed UID/GID, Memory/task bounds, current Connect
manifest, and one browser resource. Installation copies the artifact and config
into deployment-specific directories, freezes both declared credential sources
as root-only deployment snapshots for `LoadCredential`, creates only the
declared system account, installs the three exact units, publishes initial
authority, and starts the timer and gateway. Source credentials remain
untouched. A failed apply rolls back resources created by that attempt; an
interrupted operation retains a declaration-bound receipt for managed removal.

Removal also previews by default:

```sh
sudo anvil-serving connect native-memory-service remove --declaration /protected/native-memory-service.json --confirm
```

It first proves the receipt, every owned unit, file, and directory membership,
then stops only those units, requires the service UID to own no processes,
removes exact digest-matching copies and the generated authority file, and
finally removes the dedicated account. It never removes the Connect manifest,
source credential files, installed manager, router, model service, or unrelated
unit. Manage the single HTTPS mapping separately with the maintained `edge`
lifecycle.

## Users

Follow [Invite a developer](../ANVIL-CONNECT-ONBOARDING.md) for password setup,
passkey enrollment, terminal connection, and operator-assisted recovery.

Inspect accounts first:

```sh
sudo anvil-connect-ctl users list
sudo anvil-connect-ctl users show developer
```

Both require local root access and return only username, email, groups and disabled
status from the checked Authelia file backend. They do not restart services or
read factor storage. Output remains private account metadata. Groups and disabled
status describe sign-in accounts, not Connect service grants; inspect those through
**Manage access** on the service home. A missing username fails explicitly.

Create an invited developer on the authentication host:

```sh
sudo anvil-connect-ctl users create developer --email developer@example.test --grant workbench:member --confirm
```

The standalone manager and `anvil-serving connect users` expose the same commands.
User administration defaults to `/etc/anvil-connect/deployment.json`; another
installation can select `--manifest`. Omit `--confirm` to preview. This Linux
command manages the pinned Authelia file backend through its native password
hashing and factor-removal commands. Optional `authelia.smtp` makes Authelia email
password-setup links and verification codes directly; see the
[email configuration](../ANVIL-CONNECT-ONBOARDING.md#configure-email-delivery).

Creation defaults to role `member` (Authelia group `members`). Use `--role admin`
explicitly for group `admins`. These groups record the intended role; Connect
operator privileges still require separate authorization. `--grant` assigns
exact service entitlements and an application role; repeat it for each declared
browser service. With memory configured, creation also provisions a personal
Hindsight bank even when browser grants are omitted.
Resets preserve existing groups and do not enable disabled accounts.

Install protected per-user bank settings, then provision an existing account:

```sh
sudo anvil-connect-ctl users configure-memory --input /protected/user-memory.json --confirm
sudo anvil-connect-ctl users memory developer --confirm
```

Omit `--confirm` to preview. Installation input must be root-owned, mode `0600`,
and outside Git. Its `memory` settings and optional email-based `user_defaults`
are documented in [Per-user banks](../HINDSIGHT.md#per-user-banks). The memory
command preserves the person's current browser grants, creates and protects
their personal bank, and invalidates existing Connect browser and terminal
credentials without restarting Authelia. Shared defaults require explicit grants.
Suspending or deleting an account closes bank access and retains its memories.

When `memory.router` is installed, `users create`, `access`, and `memory` also
provision a personal API key for `memory.user`. It resolves through the same
principal and bank policy for REST, MCP, and application clients. Resume only the
key phase with this idempotent command (it does not rotate the account generation):

```sh
sudo anvil-connect-ctl users memory-key developer --confirm
```

The key stays in the configured root-only directory; output contains its protected
file path and metadata, never the token. Lost pipe responses recover the saved
token. Compatible router approvals and manual keys are preserved; existing
pending, denied, or incompatible approvals require an explicit policy decision.
The command renews its managed key within one hour of expiry or after an account
generation change, then revokes only the prior receipt-owned key. Schedule this
command with the application's native enrollment owner to maintain credentials.
Preview reports known creation/rotation needs; live router validation is deferred
to confirmation. Bank access remains ready if key setup fails: retry `memory-key`,
not the account-generation-changing `memory` command. Renewal needs one free slot
within the existing ten-key account limit. If all slots are occupied, revoke an
unused personal key in Connect Home and retry; onboarding never revokes manual
keys to make space.

Replace an existing person's complete service grant list:

```sh
sudo anvil-connect-ctl users access developer --grant workbench:member --grant pi:admin --confirm
```

The command resolves the user's actual Authelia OpenID identifier; a username
is not an OIDC subject. Create and access send the validated command username
only as account metadata alongside that authoritative subject. Resources omitted from this list are withdrawn. A
successful access change enables the Connect account and invalidates existing browser and terminal
credentials for the person. API principal grants remain separately declared.
For a suspended account, it also re-enables the Authelia account, preserving its
password, groups and registered factors, after applying the explicit grant list.

Suspend or delete a person by username:

```sh
sudo anvil-connect-ctl users suspend developer --confirm
sudo anvil-connect-ctl users delete developer --confirm
```

Omit `--confirm` to preview. Suspension disables the Connect identity and revokes
its browser sessions and human-approved terminal credentials, while preserving
the password, factors and service grants. Use `users access` with the intended
grants to resume it.

Permanent deletion removes the password-file account, authentication factors,
Authelia OpenID identifiers and related OAuth records, and the Connect principal,
sessions and human-approved terminal credentials. The username can then be used
for a new account with a fresh identity and explicitly assigned grants. Configured
Connect operators cannot be deleted; change the operator configuration first.
Deletion backs up authentication state before removing it, briefly restarts
Authelia if active, and retains a protected progress record if interrupted. Retry
the same deletion to finish; an incomplete deletion stays disabled.

Backups, application data and separately issued API keys are retained; these keys
have no automatic username association and require separate revocation. An old
backup can contain a deleted account: review revocations before recovery. Use
suspension when access may be needed again.

Applications that opt into `authelia.additional_oidc_clients[].groups_scope`
receive the `groups` claim. The installed account-reconciliation worker runs the
equivalent of this command before independently processing a pending deletion:

```sh
sudo anvil-connect-ctl users sync-operators --confirm
```

It derives `anvil-connect-operators` from configured operators that are enabled,
have no pending deletion, retain the declared browser-administration resource,
and resolve to an enabled local sign-in account. It removes stale members and
does not restart Authelia when the file is already converged.

With filesystem delivery, the result names an exclusive, root-only handoff file beneath
`/etc/anvil-connect/handoffs`; credentials never appear in command output.
Deliver its contents directly to the intended developer, then remove the file.
Create and password reset store only the hash of a random bootstrap password;
the usable value is discarded and never written to a handoff. The handoff instead
contains Authelia's one-time password-setup link, valid for five minutes. With SMTP,
Authelia emails this link and no local handoff file is created. The
developer opens it and chooses a password before signing in. Authelia must be
running to issue the setup link. A newly created account has no passkey, so it
must complete this setup before signing in. These checks run before account
mutation. Keep the
users file owned by the Authelia service identity with mode `0600`, so the
provider can save the password chosen by the developer. The managed service
permits writes to this exact file; existing installations do not need to move it.
Authelia still requires an email attribute for identity-verification messages,
even with manual delivery through its filesystem notifier.

```sh
sudo anvil-connect-ctl users code developer --confirm
sudo anvil-connect-ctl users reset-password developer --confirm
sudo anvil-connect-ctl users reset-mfa developer --confirm
```

With SMTP, users receive codes directly by email; `users code` explains this and
does not read historical local notifications. With filesystem delivery, after the
developer requests an enrollment code or password reset on the Authelia
page, `code` exports only a matching recipient's notification less than five
minutes old to a private handoff file. It returns either a verification code or
an exact-provider password-reset link. Use this to replace an expired welcome
link after the developer requests another reset. The filesystem notifier holds
only the latest message; handle invitations one at a time. Export does not prove
that a code or link remains unused. `reset-mfa` explicitly removes
that user's WebAuthn/passkeys and TOTP so they can enroll again. Password reset
alone retains those factors. Both website login and terminal browser approval
continue to use Authelia's existing browser flow.

Password reset preserves groups, grants and factors, invalidates the old password,
and revokes Connect browser sessions and human-approved terminal credentials.
Pending browser sign-ins begun before the reset are also rejected. After a
successful replacement, old create/reset handoffs for that account are removed.
If setup-link delivery fails after the password was replaced, the command reports
a partial operation; request a fresh reset link rather than relying on the old
password. For SMTP the response reports a requested email, not confirmed delivery:
Authelia's reset endpoint conceals delivery errors to prevent account enumeration.

Create/password reset briefly stop and restart the running Authelia service,
clearing its in-memory sessions. Factor changes and manual backups restart it only
if it was already running and do not themselves revoke Connect credentials.
Separately issued API keys require their own revocation.

Account files, email addresses, password hashes, registered factors, storage
keys, handoff files and their backups are classified **restricted-authentication**.
Keep them outside Git, ordinary logs, shared folders and unencrypted cloud sync.
Use only synthetic identities in tests and published evidence. This is a local
handling label, not a legal compliance certification.

```sh
sudo anvil-connect-ctl users backup --include-gateway --confirm
sudo anvil-connect-ctl users schedule --confirm
sudo anvil-connect-ctl users restore --input /etc/anvil-connect/backups/ARCHIVE.zip --sha256 DIGEST --destination /etc/anvil-connect/backups/recovery --confirm
```

A backup runs automatically before create/reset/suspend/delete operations and
when resuming a suspended account. Run `users backup`
after a developer finishes enrollment or changes their own password/passkeys,
and before maintenance. It includes the users file and, when initialized, a
consistent SQLite copy plus its storage encryption key. The root-only archive
records its classification and member checksums; output includes its SHA-256.
Keep that digest separately. The command retains the newest verified snapshot
for each of the last 14 UTC calendar days, plus the seven newest event snapshots.
It preserves unrecognized, future-dated, and timestamp-mismatched archives.
Archives are limited to 64 MiB and are **not encrypted**. These are local rollback
copies; they cannot recover a lost disk. Encrypt any copy taken to another device.
Host configuration and other secrets need their own backups; the gateway authority
is included by the option below.

Add `--include-gateway` for the nightly recovery set. It takes the Authelia
snapshot first, then stops only the native gateway for its offline authority
archive and starts that exact prior gateway again. Caddy and Authelia remain
running for that gateway step. The two archives are sequential rather than
atomic, and the gateway receipt binds the auth archive digest, gateway archive
digest, and native digest. Validated pairs use the same 14-day and seven-recent
retention policy. Both archive types are bounded local copies and are unencrypted.

Restore requires the independently retained digest, checks archive and SQLite integrity, then extracts only into a fresh
root-only directory. It does not overwrite live accounts or activate recovered
factors. Review account/factor revocations before a separately managed recovery
activation. An old authentication snapshot can contain credentials that have
since been revoked. The existing `connect backup`/`connect restore` commands
below separately handle gateway authorization state.

`users schedule` installs an owner-checked systemd service and persistent timer
for **03:17 UTC daily**. It supports the default manifest and the verified
standalone gateway installation under `/opt/anvil-connect`; install this manager
release before enabling it. It uses the same backup operation, including its
sequential Authelia and gateway snapshots. The timer catches up after downtime.
It does not send email notifications.
An existing unrelated unit with the same name is refused.

To enable permanent deletion from the browser, install the verified manager and
its fixed worker first:

```sh
sudo anvil-connect-ctl users deletion-schedule --confirm
```

Then enable `gateway.gateway.browser_administration.user_deletion` in the managed
deployment. The Connect home access editor and Workbench access console show
**Delete user**, with the username and full account ID. Self-deletion and deletion
of configured operators are refused. Confirmation immediately disables the
account; the root worker processes one pending deletion every ten seconds.
Refresh accounts to see completion. The worker runs only the fixed
`users process-deletions --confirm` operation and shares the deployment lock with
configuration changes. No browser request supplies a command or filesystem path.

### Service home and application roles

Set optional `gateway.gateway.portal_host` to a dedicated hostname such as
`home.example.test` to publish the chooser independently of an application.
The gateway serves its root, home assets, and sign-in endpoints directly; it
does not create a connector resource or a service grant. Publish that hostname
through the verified TLS edge like the existing browser hosts. Rendering adds
its exact OIDC callback and makes it Authelia's landing destination. It must be
distinct from every application, authentication, control, and tunnel hostname.
Its cookie is bound to the home host and cannot authorize application access.
Enabled Connect accounts see only their existing service entitlements there.
Access editing remains on the configured administration service's own host.

Open `/_anvil-connect/home` beneath a declared browser service's path prefix.
For a service rooted at `/workbench`, the home is
`https://workbench.example.test/workbench/_anvil-connect/home`. Login returns to
that page. It lists only the person's current browser entitlements, links to
Authelia account/passkey settings, explains terminal approval, and signs out of
Connect. Each service host retains its own Connect cookie; Authelia supplies SSO
when opening another tile. An enabled Connect account can open the chooser even
without access to the service hosting it. This grants no application, device or
administrative access. Those endpoints still require their existing entitlements.
Without a dedicated home host, root sign-ins return to the chooser. With
`portal_host` configured, opening an application tile returns to that application
after sign-in; its explicit home path remains available for access editing.
Links to deeper application pages retain their destination.

A browser resource may declare up to eight fixed external OIDC return endpoints:

```json
"external_redirects": ["https://auth.example.test/api/oidc/authorization"]
```

Each endpoint is an exact HTTPS host and non-root path on a host different from
the browser resource. Connect permits only `302` or `303` responses to a
declared scheme, host, and path; the response may add its query parameters.

The operator's home on the configured `browser_administration.browser_resource`
also has an access editor. It uses the existing CSRF-protected, generation-checked
administration endpoint. Choose no access, member, or admin for each service;
saving invalidates the edited person's sessions. Account IDs are opaque Connect
identifiers. Use the username-based CLI when identifying a new person. To suspend
the final service, disable the account; the authority retains a nonempty grant list.
The last Connect operator cannot be removed. Application admins do not become
Connect operators or inherit other services, and new services are never granted
automatically. A browser gateway resource may also set an optional `display_name` for chooser tiles and access-editor labels. It does not change `rule.id`, which remains the authority key for grants, roles, sessions, enrollment, and transport.

For Workbench/Observatory to enforce these roles inside the app, declare
`native_auth: signed-identity` at the Connect browser resource and configure
the receiver's `authentication.connect.roles`, for example:

```json
{"member":"workbench-member","admin":"workbench-admin"}
```

Each value names an existing application principal with explicit resources and
actions in the application's `users` configuration. A signed role without a
matching receiver mapping is denied. When `roles` is configured, assertions
without a role are also denied, even if a legacy subject mapping exists. Assign
roles to existing users before enabling this receiver mode. Receivers that omit
`roles` retain the existing subject-based behavior. A role downgrade changes the signed request identity and
invalidates previous sessions. Passthrough applications without an explicit application-role binding, including
a separate Pi deployment without a Connect identity adapter, still manage their own roles;
their editor says that explicitly and their tiles omit the role badge. Merely storing a role label does not
grant or restrict an action inside such an application.

### Application roles owned by Connect

An application owner can install a reviewed adapter and opt one existing browser
passthrough resource into native member/admin management. The top-level deployment
setting is explicit; it does not change routing or replace application credentials:

```json
{
  "application_role_bindings": [{
    "resource": "chat",
    "adapter_path": "/opt/anvil-connect-adapters/webui/adapter.py",
    "adapter_sha256": "REPLACE_WITH_EXACT_SHA256",
    "configuration_path": "/etc/anvil-connect/adapters/webui.json",
    "configuration_sha256": "REPLACE_WITH_EXACT_SHA256"
  }]
}
```

Use real lowercase SHA256 digests from the reviewed installation. Both files and
all ancestors must be root-owned, outside Git, and not writable by other users.
The configuration file is mode 0600 and contains protected deployment metadata.
The application owner verifies native account linkage, applies roles through its
supported APIs, and revokes native sessions on reductions. No shared administrator
credential is distributed to clients. See the [adapter contract](../specs/connect-application-roles/spec.md).

Home offers Member/Admin choices for a bound resource. `users create --grant`
and `users access --grant` use the same authority. Inspect current reconciliation:

```sh
sudo anvil-connect-ctl users sync-roles
sudo anvil-connect-ctl users sync-roles --confirm
```

Omission of `--confirm` is a read-only adapter preview. Output contains per-resource
counts; awaiting first login is reported separately from failure. Connect resolves
exact issuer/subject identities, never email or global administrator groups.
Configured applications must preserve their recovery administrator during adoption.
For Open WebUI, a first browser sign-in establishes the linked account; the native
exchange does not create that account. The application remains pending until the
worker applies the explicit grant. Account data is retained when access is removed.

Install or refresh the existing account worker after installing a new Connect
manager release:

```sh
sudo anvil-connect-ctl users deletion-schedule --confirm
```

That worker reconciles native roles before deletion and still reconciles operator
groups independently. Failed role updates, revocations, or authority drift block
destructive deletion and remain retryable. Pending first-login linkage alone does
not block deletion. The timer runs 10 seconds after the preceding run finishes;
role changes are eventually consistent, not instantaneous. A missing adapter,
configuration drift, or failed native API leaves reconciliation incomplete. The
worker keeps the retained native mappings for recovery and never reports a failed
revocation as complete. Deployments without bindings keep their previous behavior.

Authelia 4.39.28's file backend has no forced-first-login-change or annual-expiry
flag. Connect requires initial password setup by withholding the discarded
bootstrap password and using Authelia's supported reset flow. When passkey login
is enabled, reset preserves registered factors; with the experimental
verified-passkey-as-two-factors option, a registered qualifying passkey can
continue passwordless sign-in. The reset link establishes the new password before
password sign-in. Annual password expiry is not enabled. The managed passkey
profile disables remembered sessions and retains its five-minute inactivity and
one-hour expiry limits. Passkey-only login satisfying two factors is experimental.

### Root Observatory with Grafana

A browser resource rooted at `/` can use the managed Caddy origin mux when a
single authenticated host must serve Observatory and Grafana. The mux is
loopback-only and uses longest-prefix routing. Its required `/` fallback points
to Observatory and is the only route that may set `preserve_identity: true`;
that resource must use `native_auth: signed-identity`. Route `/grafana` points
to Grafana with `preserve_identity: false`, so Connect identity assertions do
not enter Grafana. Keep Grafana's own authentication URL loopback-only and use
relative `/grafana/...` links so browser navigation stays behind the same
Connect resource.

For Observatory's optional fallback login, `fallback_authentication.origin` is
an exact HTTPS origin distinct from the Connect origin, and
`fallback_authentication.grafana_url` is a loopback HTTP(S) URL. Its `users`
list may contain only existing allowlisted native user IDs; it does not create
accounts or widen Connect access.

## Qualify

Run the unattended Linux browser baseline with saved qualification settings:

```sh
anvil-serving connect qualify --lane baseline
```

The default settings file is `~/.config/anvil-connect/qualification.toml`.
Use `--config /absolute/qualification.toml` to select another file. This is a
separate test configuration: it contains source, artifact, dependency-cache and
preinstalled tool paths, without production identities or credentials. It does
not select a deployment manifest or contact a model endpoint.

Save these settings once, replacing the example paths with prepared local
paths. `playwright_root` contains the pinned `node_modules` directory;
`go_module_cache` contains the already downloaded Go modules. Select actual
executables, not symlinks. The artifact directory must be owned by the test
user and mode 0700; the command can create it beneath a writable parent.

```toml
schema = "anvil-connect.qualification-config/v1"
source_root = "/srv/anvil-serving"
artifact_root = "/srv/connect-qualification/results"
playwright_root = "/srv/connect-qualification/dependencies"
go_module_cache = "/srv/connect-qualification/go-modules"
timeout_seconds = 600

[tools]
go = "/opt/go/bin/go"
node = "/opt/node/bin/node"
chromium = "/opt/chromium/chrome"
certutil = "/usr/bin/certutil"
caddy = "/opt/connect-tools/caddy"
authelia = "/opt/connect-tools/authelia"
wstunnel = "/opt/connect-tools/wstunnel"
```

The baseline runs the existing Caddy, Authelia, wstunnel and Chromium fixtures
with synthetic accounts and isolated state. It checks certificate rejection
before trusting the fixture CA, authenticated browser access, and the fixture's
logout/revocation behavior. Required binaries are checked against recorded pins;
missing prerequisites fail preflight. The command does not install dependencies.

Results distinguish passed, failed, skipped and not-run tests. Preflight failures
report both tests as not-run. A failure after execution begins reports only known
counts; `counts: null` means no complete test result is available. Cleanup failure
keeps any completed test counts but still fails the qualification. Skipped tests
never qualify the lane, and an unexecuted second test is explicitly not-run.
The private result artifact records revisions, tool identity, test outcomes and
cleanup evidence. A failed qualification exits nonzero. Raw authentication
responses and credential-bearing browser diagnostics are excluded from output.
Failed test evidence includes a safe failure-stage label. Each run keeps
`result.json`, `evidence.json`, `junit.xml` and `SHA256SUMS` after removing its
temporary source copy, profiles and caches. The source checksum binds the
tracked Connect files actually staged for that run; untracked files are excluded.

This baseline does not qualify virtual passkeys, terminal browser approval,
physical biometrics, 1Password, host systemd isolation or the public deployment.
Those require their own lanes and evidence. Fixture loopback listeners alone
are not proof of operating-system-enforced network isolation.

Prepare the separate pinned Linux amd64 browser toolchain image explicitly:

```sh
anvil-serving connect qualify --prepare-container
```

This preparation uses the same saved settings and the local Docker engine. It
may download public dependencies during the image build. Only nine named public
build and dependency files enter the build context; it does not copy the checkout,
home directory, Docker account configuration or deployment secrets. An owner-only
receipt binds the build-input digest to the immutable image ID. Repeating the
command reuses that image when its receipt and metadata match. Routine baseline
runs remain download-free. Preparation alone does not run or qualify the terminal
login flow.

Run the existing browser baseline in the prepared container:

```sh
anvil-serving connect qualify --lane container-baseline
```

This lane refuses root users and groups. It uses the recorded immutable image
with downloads disabled, no external networking, no published ports, no GPUs,
and no Docker socket or writable host mount inside the container. Its only host
mount contains the staged public source read-only. Runtime state uses private
container temporary files; CPU, memory and process counts are bounded. The runner
removes its named container after success, failure or interruption and records
cleanup and source/image identity with the test results. This qualifies the same
two browser fixtures under container isolation. Terminal approval, virtual and
physical passkeys, host service identities and production routes need separate
evidence.

Run the terminal-login scenarios in the same isolated image:

```sh
anvil-serving connect qualify --lane device
```

This drives the actual standalone CLI, synthetic Authelia browser sign-in and
approval form, then makes keyed requests through the CLI's loopback listener.
Only the CLI child trusts its fixture CA; normal HTTPS hostname and certificate
verification stay enabled. The lane checks absent/wrong local keys, declared
models access, GET-only grant enforcement, and CLI exit/port release. Its negative
scenario also checks unauthenticated approval, forged and foreign-origin CSRF,
a real browser denial, and terminal cancellation before approval. A third scenario
checks user disable and browser logout: an approved CLI request must return 401
without reaching the origin. The logout check uses a fresh Connect session
through the existing identity-provider SSO session. Codes and credentials stay
in transient fixture state, outside retained evidence.

A fourth scenario uses Chromium's virtual authenticator to enroll a discoverable
passkey through the pinned provider's real registration and elevation flows.
It requires user verification, clears browser cookies, signs in using the
passkey, and approves an actual CLI request. The fixture's separate operator
identity keeps the tested user unprivileged. Evidence labels this coverage
`virtual-webauthn-only`: it does not verify physical presence, biometrics,
Touch ID, Face ID, 1Password integration, or credential sync and backup.

The remaining passkey scenarios check missing user verification, a credential
for the wrong relying party, an expired signed assertion, sequential replay,
and a disabled Connect user. UV and relying-party failures are browser policy
checks; replay checks rejection in the resulting authenticated session, without
claiming concurrent replay resistance. A pre-enrolled spare credential restores
dashboard and CLI access while user administration remains forbidden. The
existing browser session and CLI key must remain usable after those forbidden
requests; recovery does not grant an operator role. These are synthetic recovery
and authorization checks, not validation of a production security audit log.

Device-approval expiry and single-use redemption after a lost response are covered separately by
authority and CLI unit tests, using an injected clock where appropriate. The
device lane does not claim those are browser end-to-end tests. Two browser stream
scenarios hold SSE and WebSocket connections open across human disable and logout.
They require client closure and native-handler return within one second measured
from before the authority mutation, then fresh denial without another origin
dispatch. Passed stream cases retain integer `closure_ms` in evidence and JUnit; missing
or invalid measurements fail qualification. This is a synthetic fixture target,
not a production latency guarantee.
Two further scenarios exercise SSE and WebSocket through the actual standalone
CLI after browser approval. They validate the native bearer and host, wait for
an SSE marker and a complete WebSocket handshake, then disable the user or log
out. Both clients and native handlers must close within the same one-second
fixture bound. Fresh requests with the formerly approved local key must return
401 without another native stream, and the CLI must exit and release its port.
Other authority events, authority recovery and physical passkeys require
separate coverage.

Run the bounded stream-revocation subset in the same prepared container:

```sh
anvil-serving connect qualify --lane revocation
```

This uses the saved qualification settings, the pinned image, disabled downloads,
the same configured timeout (600 seconds by default), and the existing CPU,
memory and cleanup bounds. It runs exactly ten browser and CLI lifecycle cases:
human disable, browser logout, synthetic session expiry, normal restart,
authority reset, restore, selective key revocation and browser grant removal.
The expiry fixture uses a full-precision inventory deadline across browser SSE
and WebSocket plus CLI SSE and WebSocket transports, then
denies fresh use of the old browser cookie and CLI key. The restart fixture
stops and starts the runtime, retains a valid browser cookie and CLI credential,
rejects an old revoked cookie, and checks that an interrupted POST has exactly
one origin execution. The authority-reset fixture uses the supported local
reset operation, creates a new epoch, denies the old cookie and CLI key, then
re-enrolls a fresh connector and admits new browser and CLI credentials through
the normal flows; its interrupted POST also has one origin execution. Restore
uses the supported backup and fresh-destination restore APIs, denies old
credentials before explicit human/API-principal reapproval and connector
reenrollment, then checks fresh access while the original browser cookie and
still-running old CLI remain denied. Its `closure_ms` measures the preceding runtime stop, not backup or restore latency.
The key-revocation case keeps a second CLI credential usable while the revoked
one remains denied across restart. The grant-removal case denies the removed
dashboard resource across restart while fresh login to the retained resource
succeeds. Each passed case retains its validated `closure_ms` observation.
This documents the bounded fixture scope; it does not claim an executed
qualification result, reboot behavior or external-supervisor recovery. Virtual
and physical passkeys and component-level authority checks have separate evidence.

Prepare the pinned systemd guest image, then run service-isolation qualification:

```sh
anvil-serving connect qualify --prepare-vm
anvil-serving connect qualify --lane isolation
```

Both commands use the same saved qualification settings. Preparation verifies
the signed image checksum and pinned image bytes, then stores an owner-only
receipt. Repeating preparation verifies and reuses the cache. The isolation lane
requires that prepared image and never downloads it implicitly. It also requires
the pinned local component tools, cached Go modules, QEMU, KVM, xorriso and the
declared firmware on Linux amd64.

The runner uses a disposable guest with two CPUs and 2 GiB of guest RAM. It has
no guest network interface, GPU, shared host directory or host service socket.
The payload contains a verified source snapshot and synthetic configuration;
accounts, keys and certificates are generated only inside the guest. The host
runner remains unprivileged. Host memory, disk, time and output checks bound the
run; guest RAM is not a claim of a host cgroup memory limit. Qualification
children cannot lock host memory. This lets QEMU consistently use its epoll
event loop instead of partially initializing io_uring and failing on a later
ring allocation. Address-space and resident-memory bounds remain enforced.

This lane checks actual installed service identities and unit bytes, Unix peer
authorization, private-file and administration denials, TLS/provider discovery,
certificate replacement, restart, failed-activation rollback and CLI shutdown
cleanup. It retains the same four evidence files after removing guest staging.
A missing final guest report has unavailable per-case outcomes and cannot pass;
it is not evidence that the cases were never attempted. Full browser sign-in and
physical passkeys retain their separate qualification scopes. Command availability
does not establish that a guest run or production deployment has passed. An
interrupted isolation run returns a nonzero, redacted result with its safe
execution state and available case counts.

## Validate

`connect validate` checks declarations and component prerequisites. An optional
`--service` narrows the validation to the selected role. Component validation
does not prove public reachability, successful login, or origin readiness.

## Render

`connect render` reports the intended configuration generation. With `--confirm`,
it stages generated files for inspection. It does not start services. Existing
unmanaged files and changes to owned generated files are refused.

## Up

`connect up --service gateway` previews the selected deployment. Applying it
validates the candidate, activates only owned configuration and units, and starts
the declared processes. Connector and client selections operate one exact named
installation. A selected update cannot silently change another role's files.

For a policy change spanning roles on this host, use one explicit set such as
`--services gateway,connector:dashboard,client:dashboard-api` in place of
`--service`. The selected roles share one activation and rollback transaction.
Artifact upgrades additionally require `--upgrade`: install the new artifact at
a versioned path and retain the previous verified executable for rollback.
Replacing an executable in place is refused. The preview includes the proposed
artifact digests; ordinary identity/admin commands still refuse binary drift.

For an initialized Authelia database, an Authelia artifact upgrade takes a
validated authentication snapshot and briefly stops Caddy and Authelia. Its
Authelia configuration must be unchanged. A normal configuration upgrade keeps
its existing rollback behavior. If the provider upgrade is interrupted, recovery
continues forward from its durable marker: rerun the identical `connect up`
command with `--upgrade --confirm`. The marker is recovery state, not a health
proof: after a failure or host restart, verified new services may already be
running, and retry stops and reconciles the provider and edge. Never manually
restore the old Authelia binary or database.

```sh
anvil-serving connect up --manifest /etc/anvil-connect/deployment.json --service gateway --dry-run
anvil-serving connect up --manifest /etc/anvil-connect/deployment.json --service gateway --confirm
```

The gateway requires explicit prior initialization. A connector requires its
invitation to be redeemed and its fingerprint approved before it can run.

## Down

`connect down --service SERVICE` previews stopping the selected owned services.
`--confirm` applies the stop. Application state, authority records, and secrets
remain separate from generated service configuration.

## Status

Public reverse tunnels open connections on demand. Zero idle registrations are
reported as idle, not a failed tunnel, and do not trigger a connector restart on
an unchanged apply. Status does not replace an authenticated application request.

`connect status` reports bounded owned service metadata. `--service` narrows it.
A process reported as running is not a claim that its origin is ready.

If an interrupted gateway leaves ingress or administrative sockets behind, confirmed
`connect up --service gateway` can recover it without restarting healthy
Authelia or Caddy services. Recovery stops the gateway, verifies the declared
directory and socket ownership, requires a refused socket connection, and
rechecks the socket identity before removal. Live, replaced, or unowned sockets
are refused. Preview performs no recovery; only `ingress.sock` and `admin.sock` are eligible. Account state and credentials
are not part of socket recovery.

## Doctor

`connect doctor` checks component paths and declared ownership. `--service`
narrows the checks. Diagnostics exclude secret file contents and request bodies.

## Logs

`connect logs --service SERVICE --tail 100` returns bounded service event metadata.
The maximum tail is 200. Credential-bearing raw process logs are not a public
diagnostic contract.

## Init

`connect init --service gateway` previews initial authority creation. Applying
initialization refuses to overwrite existing authorities. Ordinary `up` never
regenerates a missing authority silently.

For a connector, supply `--service connector:ID --bundle /absolute/invitation.json`.
The private invitation file is produced by gateway administration. The connector
generates installation keys locally; it never receives a shared private key.

## Identity

`connect identity --service connector:ID` returns its public installation
fingerprint. Verify that fingerprint independently before approving enrollment.
The output excludes private key material.

## Admin

`connect admin --request /absolute/request.json` previews a closed administrative
operation against the manifest's gateway Unix socket. `--confirm` performs it.
The native command runs under the declared service identity. Status, principal
grants, API key issuance/revocation, installation invitations/approval/revocation,
human grants, and authority reset use this boundary.

Issuance and invitations require `--output /absolute/private/response.json`.
The output must not exist; its parent must be owned and mode 0700. It is reserved
as a mode-0600 file before the issuing request is sent. Credentials are never
printed in command output and should not be placed in the deployment manifest.

For the generic deployment example, a request file granting an API principal
read access contains:

```json
{"operation":"principal-set","principal":"sdk-reader","grants":[{"resource":"dashboard-api","methods":["GET"]}],"disabled":false}
```

After applying that request, a separate issuance request is:

```json
{"operation":"api-key-issue","principal":"sdk-reader","grants":[{"resource":"dashboard-api","methods":["GET"]}],"lifetime_seconds":86400}
```

Apply it with `--output` to receive the one-time private response. Provision its
`secret` through the caller's secret mechanism. An ordinary SDK then uses
`https://api.example.test/v1` and that key; a native application token stays in
the connector's declared environment. Key grants are explicit and must be a
subset of the principal's current grants; they are not inherited. This example
grants GET only. Chat or
other POST operations require an explicit matching method in both the resource
declaration and caller grant.

A browser grant uses the managed issuer's exact opaque subject, replacing the
placeholder before applying:

```json
{"operation":"human-set","issuer":"https://auth.example.test","subject":"REPLACE_WITH_EXACT_OIDC_SUB","resources":["dashboard"],"disabled":false}
```

The subject is not the username. An invitation for the example connector names
its exact resource set:

For managed provisioning, `operators-ensure-resource` atomically adds one
declared browser resource with the `admin` application role to every enabled
configured browser-administration operator. It retains each operator's other
grants and roles. The request contains only the operation, one resource, and
the matching role; it does not create, enable, or repair accounts. Repeating
an already-satisfied request leaves operator generations and Connect sessions
unchanged.

```json
{"operation":"operators-ensure-resource","resources":["dashboard"],"application_roles":{"dashboard":"admin"}}
```

```json
{"operation":"invite","installation":"dashboard","role":"connector","resources":["dashboard","dashboard-api"],"lifetime_seconds":300}
```

Save its private output, redeem it with `connect init --service
connector:dashboard --bundle ...`, then inspect `connect identity`. The final
approval request uses `operation: approve`, `installation: dashboard`, and the
independently checked `fingerprint` returned by identity inspection.

## Native authority

`connect native-authority --manifest /absolute/deployment.json --resource pi-web
--output /absolute/private/native-authority.json --reader-gid GID` previews a
short-lived projection of the exact Connect human grants for one declared browser
resource. `--confirm` publishes it atomically as root, readable by the broker's
dedicated group. A managed publisher must refresh it before its 30-second expiry;
stale projections deny access. The broker must pin the same issuer and source
resource. This projection neither creates accounts nor grants new authority.
This first broker supports at most 64 granted principals per source resource.
Exceeding that capacity stops publication; existing authority expires after 30
seconds and access fails closed. Check roster capacity before extending grants.

## Native agents

`connect native-agents --config /absolute/private/native-agents.json` runs the
native Agents broker in the foreground. A managed service owns process lifecycle.
The protected configuration declares fixed OIDC introspection/userinfo endpoints,
the allowed native client, the fresh authority projection, owned workspaces and
their separate private Pi owner endpoints. Provider and owner secrets remain on
the host. Every operation and stream is scoped to the verified issuer/subject and
current Connect grant; a browser cookie, email address or WebUI token is not an
admission credential. Deploy with the managed infrastructure role and retain its
artifact digests and rollback record before exposing the HTTPS endpoint.

## Native agents check

```bash
anvil-serving connect native-agents-check
```

This finite local check needs no email activation or OAuth sign-in. It runs the
real broker HTTP handler and receipt store with two synthetic users and synthetic
owners, checking session creation, state, Stop, cross-user denial and local
admission revocation. A temporary loopback listener uses random credentials held
only in memory; it closes and its private state is removed when the check ends.
There is no production authentication setting or persistent development token.

For actual guest owners, supply `--config /absolute/private/guest-check.json` to
preview, then add `--confirm` to run. This uses the protected broker config format
but requires exactly two guest principals, two to four sandboxed workspaces, and
no personal-user workspace. Each owner endpoint and credential must be distinct.
The command ignores the configured production listen port and broker store. It
creates empty native sessions, reads their state, checks access boundaries, and
Stops them. Session IDs are returned for audit; the empty native sessions remain.
It sends no model prompts and changes no accounts or grants. `--dry-run` overrides
`--confirm`; without a config only disposable synthetic state is used.

These checks exercise broker behavior and, in configured mode, owner control
paths. They do not establish OAuth, browser, model, filesystem/network isolation,
private Hindsight or whole-process revocation acceptance. Those remain separate
deployment checks. Reading protected owner credentials requires the existing
local operator permissions; this command does not grant them.

## Keygen

`connect keygen --service client:ID --output /absolute/private/local-key` previews
a local SDK-forwarder key. `--confirm` creates it exclusively. Provision this key
and the distinct remote Connect API key through the client's declared environment
file. Native application credentials belong only to the origin connector.

Generated configuration rollback does not restore authority databases, sessions,
invitations, or revoked credentials. Database recovery requires its separate
restore workflow. Public deployment and Observatory migration still
require the isolated acceptance and migration evidence described in the delivery
plan; command availability alone does not qualify a production cutover.

## Backup

`connect backup --output /absolute/private/backup.json` previews an offline gateway
authority backup. Stop the gateway first; `--confirm` reserves an exclusive private
output before opening state. The archive contains private CA keys and authorization
history and is limited to 1 MiB; an oversized archive fails without truncation.
Keep the returned SHA-256 separately from the backup. The file is not encrypted.
Authelia storage/secrets, operator configuration, and application state have
separate backup procedures.

## Restore

`connect restore --input /absolute/private/backup.json --destination
/absolute/private/fresh-gateway --sha256 DIGEST --native-sha256 BINARY_DIGEST`
previews recovery using the independently retained backup digest and approved
native executable digest. The latter is available in Connect validation output.

`--confirm` restores only into a fresh directory. Recovery retains CA identity,
assigns a new authority epoch, disables recovered human/API grants, and discards
historical credentials, installation bindings, invitations, and sessions. Review
and reapprove grants, reenroll connectors, and issue new keys before resuming
access. The command leaves configuration and running services untouched; updating
the manifest's gateway state directory and activating it are separate operations.
An interrupted recovery can leave an inert private directory: inspect it and retry
with another fresh destination. Never replace the active authority with an old
database as part of a binary or configuration rollback.

## Migration

`connect migration --observatory-config /absolute/observatory.json --resource ID`
produces a no-write plan for one browser resource and the existing Observatory
canonical origin. It verifies native passthrough and path compatibility. The plan
preserves native login, session, CSRF, and action authorization; it does not perform
a public cutover or establish public reachability.

## Edge status

`connect edge-status --manifest /absolute/deployment.json --edge-config /absolute/edge-cloudflare.json`
compares the resources declared in the deployment manifest with the live
Cloudflare state: the required CNAME record per published host, the required
tunnel ingress rule per host, and the tunnel's connector status. Hosts outside
the configured zone are rejected. Read-only; the API token is read from the
environment reference named in the edge configuration and never appears in
output.

## Edge apply

`connect edge-apply --manifest /absolute/deployment.json --edge-config /absolute/edge-cloudflare.json --confirm`
applies exactly the declared difference through the Cloudflare API: the tunnel
ingress rules are merged (managed hosts first, foreign rules preserved, the
catch-all kept last) and the DNS records are created or updated in place.
Ingress rules for hosts that the manifest does not declare are preserved
untouched. A repeated apply converges without API writes, and the apply
verifies the tunnel is reporting an active connector before it reports
success. Without `--confirm` the command prints the same plan and changes
nothing.

## Extend

`connect extend --manifest /absolute/deployment.json --service connector:<id> --confirm`
widen one enrolled connector's resource set to its declaration in one managed
operation: the preflight accepts purely additive resource-set changes
(removals and renames are rejected with revoke-and-redeclare guidance), the
new generation is activated with only gateway units restarted, the native
admin revoke/invite pair runs against the running gateway, and the connector
is stopped before an exact retained local identity and the invitation stage an
explicit replacement. Ordinary `init --bundle` then redeems that staged
identity, the new fingerprint is approved, and the connector starts without
changing its enablement policy. The whole sequence runs inside the deployment
lock with rollback of the rendered tree and gateway units. Without `--confirm`
it prints the same plan and changes nothing. Existing principals keep their
prior resource lists until `connect admin human-set` extends each one.

After a revoke, the operation keeps a private recovery record rather than
restoring the old enrollment. A later `connect extend` first verifies the
exact native installation status and either completes the retained forward
transition or stops without further authority changes when the status has
drifted. A retained staged replacement can be retried only with the same
invitation and exact prior identity; invitation material is never shown or
recreated from that record.
