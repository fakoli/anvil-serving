# Read-only serve diagnostics blocked by an inactive profile reference

Status: open

`serves status --manifest serves.toml active-serve --json` rejected an entire
operator manifest because a different, inactive serve's `router_config` path
was missing. `serves logs` shares the manifest-read boundary. This impeded
diagnosis of a running serve and required narrow read-only backend inspection.

Sanitized error: `bad manifest serves.toml: serve entry router_config does not
exist: /operator-home/missing-historical-router.toml`.

Keep lifecycle validation strict. Evaluate a diagnostics-only path that can
report the selected serve's status/logs together with invalid unrelated
references, without granting mutation authority or accepting an invalid
deployment. Add a regression with a valid running target and an unrelated
missing profile. Separately reconcile stale private operator references.
