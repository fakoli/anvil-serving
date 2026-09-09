# Docker image build identity

Before qualifying a new runtime, inspect its immutable local image through
`anvil-serving host docker-image inspect`. It returns the resolved image ID,
repository digests, OS, architecture, and the OCI source, revision, and version
labels. Use `--label` to request additional build labels explicitly.

```bash
anvil-serving host docker-image inspect \
  registry.example/serving@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef \
  --label ai.release.cache-schema --transport local
```

The image must already be cached. Tags and abbreviated IDs are rejected. The
command makes one read-only inspection, verifies that the resolved identity
matches the requested digest, and returns no image environment or unselected
labels. Missing labels are recorded as null or empty strings; they are not
proof of build provenance. Labels are publisher assertions, so compare them
with the pinned source and release record before drawing qualification claims.

The 30-second command deadline and retained-result limit bound normal inspection
work. Docker output is captured before validation, so the result limit is not a
hard memory limit against a malfunctioning Docker process.

For guarded deletion, see [exact image removal](host.md#exact-docker-image-removal).
