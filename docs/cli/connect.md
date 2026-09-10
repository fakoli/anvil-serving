# Anvil Connect commands

`anvil-serving connect` manages application access for API clients and browsers.
It belongs to Control Plane & Fleet. A local deployment JSON selects fixed
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
dispatch. This is a synthetic fixture target, not a production latency guarantee.
Terminal stream revocation, other authority events, authority recovery and
physical passkeys require separate coverage.

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

`connect status` reports bounded owned service metadata. `--service` narrows it.
A process reported as running is not a claim that its origin is ready.

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

```json
{"operation":"invite","installation":"dashboard","role":"connector","resources":["dashboard","dashboard-api"],"lifetime_seconds":300}
```

Save its private output, redeem it with `connect init --service
connector:dashboard --bundle ...`, then inspect `connect identity`. The final
approval request uses `operation: approve`, `installation: dashboard`, and the
independently checked `fingerprint` returned by identity inspection.

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
