# Isolated official Pi runner

Build the pinned official Pi 0.85.1 runtime and its bounded provider proxy:

```sh
anvil-serving workbench build --runner pi --confirm
```

Put the resulting immutable image ID (`sha256:…`) or repository digest in the
private Workbench Pi configuration. The configured unprivileged UID/GID must
own its private state and disposable full task clone. A linked Git worktree is
not a runner checkout. The server never mounts host Pi authentication or shared
configuration into a runner.

For model access, declare exact `provider_egress` HTTP(S) origins, then run:

```sh
anvil-serving workbench pi-egress --config /private/workbench/pi-config.json --provider provider-a --confirm
```

This command accepts a Pi-only JSON block or the full Workbench JSON. Omitting
`--confirm` previews the resolved addresses and intended resources. Repeating a
verified setup reports `changed: false`. Setup freezes each origin's resolved
addresses, rejects metadata/link-local/loopback addresses, and creates a private
per-provider network and proxy. Operator-declared private provider addresses
are allowed explicitly. New DNS addresses require reviewed setup replacement.

The internal Docker network also uses the `isolated` gateway mode for both IP
families. This removes its host bridge address: plain `--internal` alone allows
access to services bound on that address. See [Docker gateway modes](https://docs.docker.com/engine/network/port-publishing/#gateway-modes).
Only the proxy joins an outbound network. The proxy permits exact approved
origins and frozen destination addresses; it exposes no published port and
receives no provider credential. The runner checks the internal network ID,
proxy image ID, readonly allowlist content digest, exact mounts/command/user,
resource limits, membership, and digest-aware health status before attachment.
Without an approved provider setup, the runner uses `network=none`.

The private configuration uses these additional fields:

- `provider_egress`: provider identity to a bounded list of exact origins.
- `provider_endpoints`: optional custom provider definition with `base_url`,
  `api`, `credential_env`, `context_window`, `max_tokens`, and `reasoning`.
- `provider_secret_refs`: each provider's child environment names mapped to
  protected environment names or `file:/absolute/private/file` references.
  Files must be regular, private, and not symlinks. Values never enter argv.
- `max_active`, `max_per_principal`, `max_per_task`, `max_wall_seconds`,
  `max_sessions`, and `max_state_bytes`: bounded runtime/retention policy.

Only the selected provider's custom model configuration and credentials enter
its container. An in-place model change must remain with that provider. Its
successful RPC outcome is retained and the effective target is confirmed with
`get_state` before more commands are accepted. Changing provider requires a
new isolated conversation.

Startup stays `starting` until official Pi confirms the exact native session
ID, provider, model, thinking level, and canonical session path. Official Pi
creates its history file after the first assistant response. Recovery reopens
that live history using its native ID; it never replays a prompt. Branching
stops the parent and uses official `--fork` plus a new `--session-id`.

Stop retained sessions and delete unwanted history in the task conversation UI.
For a provider image/endpoint change, stop its runners and remove only the
verified managed proxy/network before running setup with the updated config:

```sh
anvil-serving workbench pi-egress --config /private/workbench/pi-config.json --provider provider-a --remove --confirm
```

Removal refuses networks containing attached runners. Workbench shutdown and
lease/wall expiry stop proven owned runners; unresolved identities remain
retained and count toward capacity on restart.
