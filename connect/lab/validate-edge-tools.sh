#!/usr/bin/env bash
# Download only pinned release assets, render a synthetic deployment, and run
# upstream validation commands. It never starts Caddy or Authelia.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
lock="$repo_root/connect/lab/edge-tools.json"
cache_root=${ANVIL_CONNECT_EDGE_CACHE:-/data/cache/anvil-connect/edge-tools}
umask 077
mkdir -p "$cache_root"

run_timeout() {
  timeout --foreground 30s "$@"
}

read_lock() {
  python3 - "$lock" "$1" "$2" <<'PY'
import copy, json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
if value.get("schema") != "anvil-connect.edge-tools/v1": raise SystemExit("unsupported edge tools lock")
for item in value["components"]:
    if item["name"] == sys.argv[2]:
        for part in sys.argv[3].split("."):
            item = item[part]
        print(item)
        break
else: raise SystemExit("missing component")
PY
}

fetch_asset() {
  local name=$1 version artifact url algorithm expected binary_expected
  name=$1; version=$(read_lock "$name" version); artifact=$(read_lock "$name" artifact)
  url=$(read_lock "$name" url); algorithm=$(read_lock "$name" checksum.algorithm); expected=$(read_lock "$name" checksum.value)
  binary_expected=$(read_lock "$name" binary_sha256)
  local directory archive extract
  directory="$cache_root/$name-v$version"
  archive="$directory/$artifact"
  extract="$directory/extract"
  mkdir -p "$directory"
  if [[ ! -f "$archive" ]]; then
    curl --fail --location --proto '=https' --tlsv1.2 --retry 2 --connect-timeout 10 --max-time 120 --output "$archive.partial" "$url"
    mv "$archive.partial" "$archive"
  fi
  local actual
  actual=$("${algorithm}sum" "$archive" | awk '{print $1}')
  [[ "$actual" == "$expected" ]] || { echo "$name: checksum mismatch" >&2; return 1; }
  # Recreate the executable on every run from the verified archive: a cached
  # extracted binary is never trusted independently of its source archive.
  rm -rf -- "$extract"
  mkdir -p "$extract"
  tar -xzf "$archive" -C "$extract" --no-same-owner
  [[ -f "$extract/${name}" && ! -L "$extract/${name}" && -x "$extract/${name}" ]] || {
    echo "$name: archive does not contain an executable regular binary" >&2; return 1;
  }
  local binary_actual
  binary_actual=$(sha256sum "$extract/${name}" | awk '{print $1}')
  [[ "$binary_actual" == "$binary_expected" ]] || { echo "$name: extracted binary checksum mismatch" >&2; return 1; }
  printf '%s\n' "$extract/${name}"
}

caddy=$(fetch_asset caddy)
authelia=$(fetch_asset authelia)
work=$(mktemp -d "$cache_root/validation.XXXXXX")
trap 'rm -rf -- "$work"' EXIT

run_timeout python3 - "$repo_root" "$work" <<'PY'
import copy, json, sys
from pathlib import Path
repo, work = map(Path, sys.argv[1:])
sys.path.insert(0, str(repo))
from anvil_serving.connect.render import render
manifest = json.loads((repo / "connect/examples/deployment.json").read_text(encoding="utf-8"))
manifest["config_root"] = str(work / "rendered")
manifest["caddy"]["tls"] = {"mode": "provided", "certificate_file": str(work / "secrets" / "caddy-certificate.pem"), "key_file": str(work / "secrets" / "caddy-key.pem")}
auth = manifest["authelia"]
auth["state_directory"] = str(work / "state")
for key in ("users_file", "client_secret_file", "session_secret_file", "storage_encryption_key_file", "identity_validation_secret_file", "oidc_hmac_secret_file", "oidc_rsa_private_key_file"):
    auth[key] = str(work / "secrets" / key)
for name, content in render(manifest)["files"].items():
    target = work / "rendered" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
acme = copy.deepcopy(manifest)
acme["caddy"]["tls"] = {"mode": "acme", "certificate_file": "", "key_file": ""}
(work / "rendered" / "caddy-acme.json").write_text(render(acme)["files"]["caddy.json"], encoding="utf-8")
(work / "secrets").mkdir()
(work / "secrets" / "users_file").write_text("users: {}\n", encoding="utf-8")
PY
for secret in session_secret_file storage_encryption_key_file identity_validation_secret_file oidc_hmac_secret_file; do
  run_timeout openssl rand -hex 48 > "$work/secrets/$secret"
done
run_timeout openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$work/secrets/oidc_rsa_private_key_file" >/dev/null 2>&1
run_timeout openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$work/secrets/caddy-key.pem" >/dev/null 2>&1
run_timeout openssl req -x509 -new -key "$work/secrets/caddy-key.pem" -subj /CN=dash.example.test -days 1 -out "$work/secrets/caddy-certificate.pem" >/dev/null 2>&1
run_timeout "$authelia" crypto hash generate argon2 --random --no-confirm --profile low-memory | sed -n 's/^Digest: //p' > "$work/secrets/client_secret_file"
[[ -s "$work/secrets/client_secret_file" ]] || { echo "Authelia did not emit a client secret hash" >&2; exit 1; }

run_timeout "$caddy" validate --config "$work/rendered/caddy.json"
run_timeout "$caddy" validate --config "$work/rendered/caddy-acme.json"
run_timeout "$authelia" --config "$work/rendered/authelia/configuration.yml" --config.experimental.filters template config validate

# Negative control: upstream syntax/config errors must fail before activation.
run_timeout python3 - "$work" <<'PY'
import copy, json, sys
from pathlib import Path
work = Path(sys.argv[1])
caddy = json.loads((work / "rendered/caddy.json").read_text(encoding="utf-8"))
caddy["apps"]["http"]["servers"]["anvil_connect"]["routes"][-1]["handle"][0]["handler"] = "not-a-caddy-handler"
(work / "invalid-caddy.json").write_text(json.dumps(caddy), encoding="utf-8")
authelia = (work / "rendered/authelia/configuration.yml").read_text(encoding="utf-8")
(work / "invalid-authelia.yml").write_text(authelia.replace("default_policy: two_factor", "default_policy: deny", 1), encoding="utf-8")
PY
if run_timeout "$caddy" validate --config "$work/invalid-caddy.json" >/dev/null 2>&1; then
  echo "Caddy accepted an invalid negative control" >&2; exit 1
fi
if run_timeout "$authelia" --config "$work/invalid-authelia.yml" --config.experimental.filters template config validate >/dev/null 2>&1; then
  echo "Authelia accepted an invalid negative control" >&2; exit 1
fi
printf 'validated Caddy %s and Authelia %s with pinned Linux amd64 artifacts\n' \
  "$(read_lock caddy version)" "$(read_lock authelia version)"
