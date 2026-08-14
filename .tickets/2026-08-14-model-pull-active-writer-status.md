# Model pull has no typed active-writer status

**Status:** Open

## Problem

`models pull` correctly warns operators not to start competing writers against
the same Hugging Face cache, but Anvil Serving has no typed command that reports
whether a managed `hf download` process is already active. Because pull
containers are unnamed, synchronous, and removed on exit, `models cache
inventory` cannot distinguish an active writer from an idle cache.

The Qwen3.8-27B preparation therefore required the narrow read-only fallback of
inspecting running Docker commands for `hf download` before starting the one
managed pull. The model lifecycle remained under `anvil-serving models pull`;
Docker was not used to download, stop, or modify anything.

## Required behavior

1. Add a typed read-only model-download status surface to the CLI and the
   restricted controller/MCP catalog.
2. Report the cache volume, repository and revision when safely observable,
   container identity, running state, and start time without exposing token
   values or process environment.
3. Make `models pull` fail closed when another writer owns the same cache
   volume, unless it can prove the request is the same resumable operation.
4. Preserve the current exact-revision post-download verification.

## Acceptance

- Hermetic tests cover no writer, one matching writer, a different repository
  writer on the same volume, a writer on another volume, malformed metadata,
  and completed/removed containers.
- Human and JSON output contain no credentials or environment values.
- CLI help and controller/MCP documentation explain the single-writer rule.
