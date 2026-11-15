# Windows managed CLI launcher cannot traverse its Python runtime

Status: runtime binding repaired and payload verified; transport diagnosis remains separate.

The installed uv-owned CLI fails before Anvil Serving loads because the Python
minor-version path is a junction reported by Windows as an untrusted mount point.
The exact versioned interpreter exists separately. This prevents managed recipe
status and owner log retrieval; it is not evidence that the model stopped.

Preserve the installed package and runtime identities. Diagnose the supported
uv installation and repair its interpreter binding through the installer; do not
weaken Windows trust checks, replace shared junctions, or change model lifecycle.
Return to the managed status/log commands after repair and verify package identity.

Narrow read-only Docker diagnostics are permitted while the managed launcher is
broken. Current context/status reads also time out, and SSH sometimes stalls
before key exchange, so those failures do not yet identify a Docker root cause.
Private exact paths, hashes and transport receipts remain in operator evidence.

## Verified repair

The currently installed wheel was newer than the historical recovery note. All
519 package files matched its retained archive, so recovery reinstalled that
exact wheel through `uv tool install --force --offline --no-python-downloads
--python 3.14.8`. The explicit patch request binds the environment to the exact
versioned interpreter instead of the failing minor-version junction. The public
CLI starts again, and all 519 payload files still match with no extra files.
No shared junction, trust setting, service, image or model was changed.

A second private configuration gap omitted the declared `model-serve` owner.
It is repaired in the operator topology, independently of the package repair.
Separate intermittent SSH/HTTPS delays are not explained by this launcher fix.
