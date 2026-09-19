#!/bin/sh
set -eu
sha256sum --check /opt/provenance/binary.sha256
printf '%s  %s\n' f21f308d3b23ccd627071cd015e413db08deee4356643900518e2b251750fdc2 "$1" | sha256sum --check
exec /opt/ninfer-build/apps/ninfer-serve "$@"
