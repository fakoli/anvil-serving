# Support native Linux host-memory observation

Status: open.

The host-memory command supports only its WSL path, so native Linux campaign
memory evidence could be retained but the command could not be claimed as
passed.

Add a bounded native Linux implementation with the same documented output
contract and explicit unsupported-state reporting. Do not infer memory limits
from a WSL-only source on native Linux.

Acceptance: unit fixtures cover native Linux, WSL, unavailable data, and
malformed source input. The native result labels observed versus configured
limits, has no private-path or process disclosure, and the focused command
test succeeds without WSL markers.
