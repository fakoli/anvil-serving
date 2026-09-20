#!/bin/bash
set -euo pipefail
test "$#" -eq 3
test -f "$1"
test "$(stat -Lc %s -- "$1")" = "$3"
# This pinned artifact is 4096-byte aligned. Fail closed on unsupported direct I/O,
# a failed read, or a digest mismatch; never retry through the Linux page cache.
actual="$(dd if="$1" iflag=direct bs=4M status=none | sha256sum)"
test "$(stat -Lc %s -- "$1")" = "$3"
if [[ "${actual%% *}" != "$2" ]]; then
    printf 'model artifact SHA-256 mismatch\n' >&2
    exit 1
fi
printf 'model artifact SHA-256 verified with direct I/O\n'
