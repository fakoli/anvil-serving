# Install Connect independently

Connect has its own versioned bundle and installer. It does not require an
Anvil Serving installation, Python package, router, controller, or model stack.
The standalone Linux manager contains the same Connect lifecycle implementation
used by `anvil-serving connect`.

| Bundle | Roles | Runtime prerequisites |
|---|---|---|
| Linux amd64 | Gateway, origin connector, local API client | Python 3.11+ and systemd for the manager; native client needs neither |
| macOS arm64 or amd64 | Local API client | Native executable; Python 3.9+ runs the installer only |

Linux arm64 and Windows bundles are not currently supported. A macOS client
bundle cannot start a gateway or origin connector. Browser users and ordinary
HTTP SDKs can also connect directly to the public HTTPS endpoint without a
local forwarder.

## Install a bundle

Obtain the platform archive and its independently retained release receipt.
Verify the archive SHA-256 **before extracting or executing its installer**.
The receipt also supplies the `manifest_sha256` required below. Checksums detect
changed bytes; they are not a substitute for trusting the release publisher.

```sh
# Run in the extracted bundle. This previews and verifies every component.
python3 install.py --role client --prefix "$HOME/.local/anvil-connect" \
  --manifest-sha256 MANIFEST_SHA256

# Install after inspecting the preview.
python3 install.py --role client --prefix "$HOME/.local/anvil-connect" \
  --manifest-sha256 MANIFEST_SHA256 --confirm
"$HOME/.local/anvil-connect/bin/anvil-connect" --help
```

Use a dedicated, owned prefix. The installer refuses symlinked or writable
ancestry, unowned existing files, a mismatched platform/role, or changed bundle
bytes. It installs only the selected role's components, never starts a service,
never discovers credentials, and never edits shell profiles. Add the shown
`bin` directory to your own PATH if desired.

For a Linux gateway, use `--role gateway --prefix /opt/anvil-connect` as root.
That bundle includes the native component, pinned Caddy/Authelia/wstunnel and
`anvil-connect-ctl`, a self-contained Python zipapp. An origin-only installation
uses `--role connector` and needs neither Caddy nor Authelia.

## Configure and operate

Gateway host provisioning must create the dedicated non-root service identity
and owned state/secret directories before initialization. Private topology,
certificates, OIDC material, and enrollment grants remain external to the
bundle. Follow the [operator guide](ANVIL-CONNECT.md) and use the same commands
with `anvil-connect-ctl` in place of `anvil-serving connect`:

```sh
/opt/anvil-connect/bin/anvil-connect-ctl validate \
  --manifest /etc/anvil-connect/deployment.json --service gateway
/opt/anvil-connect/bin/anvil-connect-ctl init \
  --manifest /etc/anvil-connect/deployment.json --service gateway --confirm
/opt/anvil-connect/bin/anvil-connect-ctl up \
  --manifest /etc/anvil-connect/deployment.json --service gateway --confirm
```

Pin manifest executable paths to an immutable `releases/VERSION-REVISION/bin`
directory. Managed `up --upgrade` checks retained binary identity and preserves
its own selected-service rollback. Installing a newer bundle only updates the
CLI links; it does not change a running deployment.

For an HTTPS tunnel origin, set optional `caddy.listen` to an explicit loopback
address, for example `127.0.0.1:19443`, with provided certificates. This disables
implicit public redirect listeners while retaining TLS. Configure the upstream
tunnel to verify that certificate and its expected server name. Omitting the
field retains the existing `:443` behavior.

After connector enrollment, `anvil-connect-ctl identity` can inspect the pending
public fingerprint before any connector service activation. Approval remains
explicit. Managed activation now requires stable owned processes before
committing rollback state; live login, authorization and origin probes still
establish application readiness separately.

The optional client consumes a `anvil-connect.client-runtime/v1` declaration
and two explicit environment references: a local caller key and a separately
scoped remote Connect key. Extract the client declaration from your approved
deployment or provision it separately. It binds only the declared loopback port:

```sh
anvil-connect validate --mode client --config /absolute/client.json
anvil-connect client --config /absolute/client.json
```

Supply the referenced variables through your private secret mechanism before
starting the process. The first macOS release supports this foreground process;
it does not silently install a LaunchAgent or embed keys in a plist. Gateway
availability remains independent of any client laptop.

For interactive use from SSH or a terminal, provision the optional
`device_authorization` declaration and run `anvil-connect login --config
/absolute/client.json`. It displays a browser verification URL and short code,
then starts the same local listener after approval. Only the local caller key
is supplied beforehand; the short-lived remote key stays in memory. Follow
the [terminal sign-in guide](ANVIL-CONNECT-DEVICE-LOGIN.md).

## Repeat, upgrade, and rollback

Repeating the same installation validates the installed bytes and reports
`current`. Releases are immutable and retained. Run the installer from a
different verified bundle with the same role/prefix to switch CLI links; run it
from a retained prior bundle to roll them back. One atomic link selects both
the executable release and its receipt. Interrupted first installation never
publishes a partial prefix. Neither operation restores authority databases,
revocations, sessions, or native credentials.

Gateway service certificates renew through the existing supervised restart
before their 24-hour expiry. That restart can interrupt active streams; clients
must reconnect without replaying ambiguous mutation requests. Continuous
certificate rotation is not implemented.

## Build a release

Build from a clean committed checkout. `connect/packaging/build_bundle.py`
compiles the platform command, verifies the pinned Linux component binaries,
packages only Connect, and writes an archive plus `receipt.json`:

```sh
python3 connect/packaging/build_bundle.py --platform linux-amd64 \
  --output /absolute/new-release --cache /absolute/build-cache \
  --caddy /absolute/verified/caddy --authelia /absolute/verified/authelia \
  --wstunnel /absolute/verified/wstunnel
python3 connect/packaging/build_bundle.py --platform darwin-arm64 \
  --output /absolute/new-mac-release --cache /absolute/build-cache
```

Build CPU concurrency is capped at four and the Go memory target is 2 GiB.
The receipt records the exact source revision, platform, archive checksum and
manifest checksum. Cross-compilation alone does not prove execution on a Mac;
native tests and an installation smoke are required before claiming that target
qualified. Consult the deployment record for actual public-edge qualification.
