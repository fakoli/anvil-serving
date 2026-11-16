# Expose admitted Compose identity in router status

Status exposes container/image identity and configuration mounts but omits the
admitted `com.docker.compose.project.config_files` label. Current-stop setup
needs that exact predecessor path; a mount location can belong to an older
deployment and is insufficient to reconstruct the admitted Compose identity.

Observed during a configuration-only promotion on release 2.0.2. A bounded,
read-only label query supplied the missing path; no environment was inspected.
Add the nonsecret admitted Compose path to the existing native custody status,
with bounded validation and a focused regression check. Until then, retain the
label observation with the protected promotion evidence; do not infer the path
from configuration mounts or reuse a historical deployment profile.
