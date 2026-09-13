# OpenAI-compatible reasoning aliases lost at the router

**Status:** Implemented; live replay and independent review pending

## Evidence and cause

A model qualification replay returned nonempty `reasoning` in direct upstream
SSE, while the routed response omitted reasoning. Both the streaming assembler
and buffered extractor accepted only `reasoning_content`. Same-dialect history
also recognized only that spelling, so a reasoning-only assistant continuation
using an alternate field could lose its original wire structure.

Results collected without the expected reasoning history are not evidence of a
matched history-preservation comparison. Repeat affected qualification after
the installed router passes direct-versus-routed and actual-client replay.

## Fix and regression contract

- Response extraction selects the first nonempty string in `reasoning_content`,
  `reasoning`, then `reasoning_text`, emitting one canonical downstream field.
  Invalid or empty values do not mask a later valid alias; whitespace is retained.
- Same-dialect assistant history recognizes all three aliases and preserves
  their original fields without mutating the request. No cross-dialect history
  translation or outbound alias rewrite is introduced.
- `strip_reasoning_history` remains authoritative after tier overrides and
  removes all three aliases while preserving tool continuation structure.
- Hermetic tests cover buffered, SSE, JSON streaming fallback, alias precedence,
  malformed values, incremental tool/usage assembly, reasoning-only history,
  input immutability, and the independent history policy.
