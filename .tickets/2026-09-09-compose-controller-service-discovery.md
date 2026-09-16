# Compose controllers are omitted from host service discovery

Status: open; recovery is available through an explicitly inspected service binding.

On Windows, `controller inspect` identified an existing stopped Compose-owned
controller, while `host services discover` omitted it. The latter filters only
the model-recipe ownership label; the controller instead carries the exact
Compose service label `controller` used by controller diagnostics.

The diagnostic API does not project the image ID or Compose project identity
needed to pin a host service binding. Recovery therefore required one bounded,
read-only Docker inspection of that exact container's image, state, restart
policy, and Compose project/service labels. No environment or raw logs were
retrieved. A private `services.toml` binding can then use the existing managed
`host services up` path without recreating the container or changing its policy.

Follow-up: provide bounded discovery/adoption of verified Anvil Compose
controllers, preserving exact ownership, image pins, local-daemon checks, and
the controller self-shutdown recovery boundary. Cover unrelated Compose
containers and identity mismatch refusals with regression tests.

All operator identities and paths are omitted from this public record.
