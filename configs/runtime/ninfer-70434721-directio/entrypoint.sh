#!/bin/bash
set -euo pipefail
printf '%s  %s\n' 82ec9634bf221bee534bcf26c940a7c63f29fff36cd858de9bc57e75c3e6b25f /opt/ninfer-build/apps/ninfer-serve | sha256sum --check
/opt/verify-artifact.sh "$1" f21f308d3b23ccd627071cd015e413db08deee4356643900518e2b251750fdc2 21492695040
exec /opt/ninfer-build/apps/ninfer-serve "$@"
