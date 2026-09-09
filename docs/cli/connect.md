# Anvil Connect commands

`anvil-serving connect` manages application access for API clients and browsers.
It belongs to Control Plane & Fleet. A local deployment JSON selects fixed
resources, component binaries, service identities, and secret references.
The existing `edge` commands continue to manage Tailscale independently.

This delivery initially targets Linux amd64. Native Connect, Caddy, Authelia,
and wstunnel run as separate processes. The Python package adds no runtime
dependencies and does not download or start those binaries when imported.

Every command requires `--manifest /absolute/deployment.json`. `--service` selects
`gateway`, `connector:ID`, or `client:ID` from that manifest. These are local
deployment selectors; the command does not perform implicit SSH or controller
dispatch. Mutations preview by default; `--confirm` applies the operation and
`--dry-run` keeps it a preview even when both flags are supplied.

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

## Keygen

`connect keygen --service client:ID --output /absolute/private/local-key` previews
a local SDK-forwarder key. `--confirm` creates it exclusively. Provision this key
and the distinct remote Connect API key through the client's declared environment
file. Native application credentials belong only to the origin connector.

Generated configuration rollback does not restore authority databases, sessions,
invitations, or revoked credentials. Database recovery requires its separate
authority-reset workflow. Public deployment and Observatory migration still
require the isolated acceptance and migration evidence described in the delivery
plan; command availability alone does not qualify a production cutover.
