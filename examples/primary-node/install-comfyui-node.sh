#!/bin/sh
set -eu

node_name="$1"
repo="$2"
revision="$3"
requirements_sha256="$4"
node_dir="/app/custom_nodes/${node_name}"

case "$revision" in
  *[!0-9a-f]*|'')
    echo "invalid immutable revision for ${node_name}" >&2
    exit 1
    ;;
esac
[ "${#revision}" -eq 40 ] || {
  echo "revision for ${node_name} must be a full 40-character commit" >&2
  exit 1
}

git init "$node_dir"
git -C "$node_dir" remote add origin "$repo"
attempt=1
while ! git -C "$node_dir" fetch --depth 1 origin "$revision"; do
  if [ "$attempt" -ge 5 ]; then
    echo "failed to fetch ${node_name}@${revision} after ${attempt} attempts" >&2
    exit 1
  fi
  delay=$((attempt * 5))
  echo "fetch failed for ${node_name}@${revision}; retrying in ${delay}s" >&2
  sleep "$delay"
  attempt=$((attempt + 1))
done
git -C "$node_dir" checkout --detach FETCH_HEAD
observed_revision="$(git -C "$node_dir" rev-parse HEAD)"
[ "$observed_revision" = "$revision" ] || {
  echo "revision mismatch for ${node_name}: expected ${revision}, observed ${observed_revision}" >&2
  exit 1
}

if [ -f "$node_dir/requirements.txt" ]; then
  observed_requirements_sha256="$(sha256sum "$node_dir/requirements.txt" | cut -d ' ' -f 1)"
  [ "$observed_requirements_sha256" = "$requirements_sha256" ] || {
    echo "requirements hash mismatch for ${node_name}" >&2
    exit 1
  }
  /app/venv/bin/python -m pip install \
    --no-build-isolation \
    --retries 10 \
    --timeout 120 \
    --constraint /opt/anvil/comfy-core-constraints.txt \
    --requirement "$node_dir/requirements.txt"
elif [ "$requirements_sha256" != "absent" ]; then
  echo "requirements.txt missing for ${node_name}" >&2
  exit 1
fi
