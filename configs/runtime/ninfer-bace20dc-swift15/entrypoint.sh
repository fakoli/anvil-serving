#!/bin/sh
set -eu
sha256sum --check /opt/provenance/binary.sha256
printf '%s  %s\n' 16f313c043c06a19f7c27d74f8259ed9d86c8cb5beed631efbb96f392b373d96 "$1" | sha256sum --check
exec /opt/ninfer-build/apps/ninfer-serve "$@"
