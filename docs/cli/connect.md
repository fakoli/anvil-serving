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

The baseline runs the existing Caddy, Authelia, wstunnel and Chromium fixtures
with synthetic accounts and isolated state. It checks certificate rejection
before trusting the fixture CA, authenticated browser access, and the fixture's
logout/revocation behavior. Required binaries are checked against recorded pins;
missing prerequisites fail preflight. The command does not install dependencies.

Results distinguish a failed test, a failed preflight and incomplete cleanup.
The private result artifact records revisions, tool identity, test outcomes and
cleanup evidence. A failed qualification exits nonzero. Raw authentication
responses and credential-bearing browser diagnostics are excluded from output.

This baseline does not qualify virtual passkeys, terminal browser approval,
physical biometrics, 1Password, host systemd isolation or the public deployment.
Those require their own lanes and evidence. Fixture loopback listeners alone
are not proof of operating-system-enforced network isolation.

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
