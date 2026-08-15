# Model cache cannot verify snapshot files against Hub object identities

**Status:** Open

## Problem

`models pull` verifies that an exact Hugging Face snapshot is present and
`models cache inventory` reports incomplete files and broken links, but neither
surface computes local file digests or compares them with the repository's LFS
object identities. A clean completeness result therefore does not independently
prove that locally cached Safetensors bytes match the immutable source objects.

During the 2026-08-15 Qwen3.8 NVFP4 safety intake, the operator required a full
local Safetensors digest check before loading a third-party quantization. The
managed surfaces could not perform that check, so the campaign used one narrow
read-only container with the model-cache volume mounted read-only to calculate
SHA-256 values. It did not start a model, request GPUs, or modify cache bytes.

## Required behavior

1. Add a typed read-only `models cache verify` command and restricted
   controller/MCP operation for one exact repository revision.
2. Resolve the snapshot only through the named cache volume, fail closed on
   missing or broken files, and never download or mutate data.
3. Compare every LFS-backed file with its immutable Hub SHA-256 identity and
   report ordinary-file hashing separately when a source identity is absent.
4. Support a Safetensors-only mode that also validates each header, dtype,
   tensor range, and declared file length before reporting success.
5. Emit bounded human output plus a machine-readable artifact without local
   paths, credentials, signed URLs, or unrelated cache inventory.

## Acceptance

- Hermetic tests cover a complete snapshot, one corrupted blob, missing and
  broken links, a malformed Safetensors header, non-LFS files, and a revision
  that is not cached.
- Verification is read-only and refuses broad repository or volume selectors.
- JSON records repository, revision, file identity, expected digest, observed
  digest, size, Safetensors structural status, and an aggregate pass/fail.
- The same exact-revision safety gate can run locally or through the restricted
  controller without raw Docker access.
