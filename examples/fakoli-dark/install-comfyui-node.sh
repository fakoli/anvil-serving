#!/bin/sh
set -eu

node_name="$1"
repo="$2"
revision="$3"
node_dir="/app/custom_nodes/${node_name}"

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

if [ -f "$node_dir/requirements.txt" ]; then
  /app/venv/bin/python -m pip install \
    --no-build-isolation \
    --retries 10 \
    --timeout 120 \
    --constraint /opt/anvil/comfy-core-constraints.txt \
    --requirement "$node_dir/requirements.txt"
fi
