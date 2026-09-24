# Normalize tagged Docker digest identity during managed inspection

Status: open; bounded workaround independently verified.

The RTX 5090 ThinkingCap campaign called `host docker-image inspect` with a pinned `vllm/vllm-openai:v0.29.0@sha256:082ca6f035279109041ffd3fe0695cb568b29bc580b35c4f297a66a08b216c1b` reference. Inspection rejected the identity because Docker RepoDigests uses the tagless repository form. Narrow read-only inspection confirmed the local immutable digest; the managed command then passed using the canonical repository@digest form.

Normalize a tag before comparing repository digest identities, while still requiring the exact repository and digest. Do not accept a different digest or repository. Add focused regression cases for tagged and tagless pinned references, registry ports, and mismatches. Retain immutable image-ID verification.

Until fixed, the operational invariant is to inspect with `repository@sha256:...` and preserve the pinned tag@digest in the launch recipe. This is a product validation gap, not permission to use raw Docker lifecycle commands.

Evidence: [campaign friction record](../friction-log.md).
