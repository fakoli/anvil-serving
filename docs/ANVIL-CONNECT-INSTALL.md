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

Download three assets for your platform from the same `connect-vVERSION`
release into one directory: the archive
`anvil-connect-VERSION-PLATFORM.tar.gz`, its Sigstore signature
`anvil-connect-VERSION-PLATFORM.tar.gz.sigstore.json`, and its receipt
`anvil-connect-VERSION-PLATFORM.receipt.json`. The receipt supplies the
`manifest_sha256` used below.

### How verification works

The [release workflow](https://github.com/fakoli/anvil-serving/blob/main/.github/workflows/connect-release.yml) builds every
archive from the tagged source in GitHub Actions and signs it with Sigstore
keyless signing. No private key exists: GitHub issues a short-lived OIDC token,
Sigstore's certificate authority binds it to the workflow identity, and the
signature is recorded in the public Rekor transparency log. A valid signature
proves that the archive bytes were produced by this repository's workflow at
that release tag. Replacing a release asset without re-running that workflow
cannot produce one.

A signature is accepted only for this exact certificate identity and issuer:

| Field | Value |
|---|---|
| Identity | `https://github.com/fakoli/anvil-serving/.github/workflows/connect-release.yml@refs/tags/connect-vVERSION` |
| Issuer | `https://token.actions.githubusercontent.com` |

`install.py --archive` checks the signature with an installed
[`cosign`](https://docs.sigstore.dev/cosign/system_config/installation/)
3 or later (preferred) or the [`sigstore`](https://pypi.org/project/sigstore/)
Python CLI found on `PATH`, pinning that identity and issuer, **before it extracts anything**. It then
extracts only regular files beneath the expected top directory into a private
temporary directory, requires the archive name, manifest version and platform
to agree, and still enforces the receipt's `manifest_sha256` and every member
checksum. An invalid, foreign or mismatched signature is always refused.

### Verify by hand, then install

An installer shipped inside an archive cannot vouch for that archive, so verify
the download yourself before extracting or running anything from it:

```sh
cosign verify-blob \
  --bundle anvil-connect-VERSION-PLATFORM.tar.gz.sigstore.json \
  --certificate-identity https://github.com/fakoli/anvil-serving/.github/workflows/connect-release.yml@refs/tags/connect-vVERSION \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  anvil-connect-VERSION-PLATFORM.tar.gz
# Equivalent with the Python verifier (pip install sigstore):
python3 -m sigstore verify identity \
  --bundle anvil-connect-VERSION-PLATFORM.tar.gz.sigstore.json \
  --cert-identity https://github.com/fakoli/anvil-serving/.github/workflows/connect-release.yml@refs/tags/connect-vVERSION \
  --cert-oidc-issuer https://token.actions.githubusercontent.com \
  anvil-connect-VERSION-PLATFORM.tar.gz
```

Both print a success line and exit 0 only for a genuine release. Then run the
verified installer against the archive; it re-verifies the same bytes:

```sh
tar -xzf anvil-connect-VERSION-PLATFORM.tar.gz
# Preview: verifies the signature, the manifest and every component.
python3 anvil-connect-VERSION-PLATFORM/install.py \
  --archive anvil-connect-VERSION-PLATFORM.tar.gz \
  --role client --prefix "$HOME/.local/anvil-connect" \
  --manifest-sha256 MANIFEST_SHA256

# Install after inspecting the preview.
python3 anvil-connect-VERSION-PLATFORM/install.py \
  --archive anvil-connect-VERSION-PLATFORM.tar.gz \
  --role client --prefix "$HOME/.local/anvil-connect" \
  --manifest-sha256 MANIFEST_SHA256 --confirm
"$HOME/.local/anvil-connect/bin/anvil-connect" --help
```

The result's `signature` field records the verifier and identity used.

### Unsigned releases

Releases before signing (`connect-v0.2.0` and earlier) publish no
`.sigstore.json`. The installer refuses them, and refuses any archive when
neither `cosign` nor `sigstore` is installed, unless you pass
`--allow-unsigned`. Installing from an already-extracted bundle directory
(`--bundle`, for example a retained bundle used for rollback) cannot be checked
against a signature and also requires `--allow-unsigned`. With that flag the
installer prints a warning, records `"signature": {"verified": false}`, and
relies only on the checksums you supplied, so first compare the archive
SHA-256 with an independently retained receipt. `--allow-unsigned` never
overrides a signature that fails verification.

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

For interactive use from SSH or a terminal, initial setup places the approved
client declaration (including `device_authorization`) at
`~/.config/anvil-connect/client.json` and its protected local caller key in the
sibling `local-key` file. Then run:

```sh
anvil-connect login
```

Connect loads both files automatically, displays a browser verification URL and
short code, and starts the local listener after approval. No environment
preamble is needed. The short-lived remote key stays in memory. Use `--config`
for an alternate declaration or `--json` for automation. Follow the
[terminal sign-in guide](ANVIL-CONNECT-DEVICE-LOGIN.md).

## Repeat, upgrade, and rollback

Repeating the same installation validates the installed bytes and reports
`current`. Releases are immutable and retained. Run the installer with a
different verified `--archive` and the same role/prefix to switch CLI links; run
it with a retained prior archive and its `.sigstore.json` to roll them back. One atomic link selects both
the executable release and its receipt. Interrupted first installation never
publishes a partial prefix. Neither operation restores authority databases,
revocations, sessions, or native credentials.

Gateway service certificates renew through the existing supervised restart
before their 24-hour expiry. That restart can interrupt active streams; clients
must reconnect without replaying ambiguous mutation requests. Continuous
certificate rotation is not implemented.

## Build a release

Published releases are built and signed by CI, not uploaded by hand: bump
`connect/VERSION`, tag `connect-vVERSION`, and publish a GitHub release for that
tag without archive assets. The
[release workflow](https://github.com/fakoli/anvil-serving/blob/main/.github/workflows/connect-release.yml) builds all three
platforms from the tag, signs each archive, verifies the pinned identity, and
attaches the archives, receipts and `.sigstore.json` files. It never overwrites
an existing asset, and a locally built archive has no valid signature.

For local builds and qualification, build from a clean committed checkout. `connect/packaging/build_bundle.py`
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
