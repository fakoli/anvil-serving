# Support DeepSeek `max` reasoning effort in qualification commands

**Observed:** 2026-08-01

## Problem

DeepSeek V4 Flash 0731 publishes `low`, `high`, and `max` reasoning modes, but
Anvil Serving's preflight, capacity, quality, multimodal, and MCP schemas stop
at `high`. This prevents an exact max-effort qualification and encourages an
operator to approximate the publisher's strongest mode with a different value.

## Resolution

Add `max` to the generic OpenAI-compatible `reasoning_effort` choices on every
qualification surface. Preserve model-specific validation: GPT-OSS continues
to reject values outside its documented `low`, `medium`, and `high` set, and
DeepSeek V4 rejects values outside its documented `low`, `high`, and `max`
set. Chat-template-controlled model families continue to reject this
mechanism.

## Acceptance

- Local preflight and benchmark parsers accept `--reasoning-effort max`.
- The multimodal evidence parser and MCP `preflight_probe` schema expose the
  same value.
- The request body forwards `max` unchanged.
- Quality and capacity dry-run plans expose the exact resolved reasoning
  control that a live request will use.
- DeepSeek V4 fails closed on generic values it does not publish, such as
  `medium`.
- Existing GPT-OSS and Qwen model-family restrictions remain fail closed.
- Focused parser, request, MCP-parity, and command-tree tests pass.
