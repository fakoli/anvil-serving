# Managed Docker Desktop data disk restoration

Status: implemented and verified on Windows.

A Windows migration can leave an intact prior Docker data VHDX beside an empty
new installation. The existing host surface compacts a disk but cannot restore
one, forcing operators toward ad hoc filesystem/lifecycle commands.

`host docker-disk restore SOURCE TARGET --dry-run/--confirm [--move]` now stops Desktop,
requires plain absolute VHDX paths, rejects ancestor reparse points and shared
file identities, checks full-copy capacity, locks both images against writes,
streams and independently verifies SHA-256, then replaces the target while
preserving its timestamped backup. Same-volume move mode skips copying and
consumes the source path. Copy mode leaves source bytes untouched and Desktop
is left stopped. Failed copies remain identified for diagnosis. It never
unregisters a WSL distro, deletes a disk, or changes Docker settings.

Validation: small-file Windows restore plus failure checks cover locks, copy,
checksum, replacement, restart, insufficient capacity, and unsafe paths.
Live same-volume move restored the previous data store; managed restart recovered
25 images, six containers and 16 cached model repositories. Exact-image service
starts and local/HTTPS protocol smokes passed. Private paths, identities and raw
operator receipts are retained outside this public repository.
