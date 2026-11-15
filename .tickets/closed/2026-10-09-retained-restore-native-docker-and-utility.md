# Retained exclusive restoration refuses native Docker ownership and GPU monitoring

Status: Resolved in source; publication and deployment are separate gates.

The command registration restricted the resource runtime to native, even though
the native operator manages the same host's Docker model resource. Earlier CLI
coverage mocked target resolution and missed that refusal. Use the existing
native-to-Docker transport rule, and enforce native local ownership inside the
restoration handler so direct callers share the boundary.

The complete-container guard also treated utility-only GPU monitoring as model
ownership. Inspect only the driver-capability setting and device policy, permit
the exact utility-only case, and retain physical-compute and recipe-owner
denials. Duplicate, missing, compute-enabled and privileged declarations refuse.

The existing ad-hoc Compose lifecycle now supports explicit named-service
`--no-deps` repair without running secret-initialization dependencies. Invalid
unnamed use refuses before router startup. Regression coverage lives in
`tests/test_serves_retained_restore.py` and `tests/test_serves_manage.py`.
