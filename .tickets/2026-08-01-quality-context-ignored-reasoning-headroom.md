# Quality chat/context probes ignored reasoning headroom

Status: fixed locally

## Symptom

Protocol-v3 quality evidence correctly applied
`visible_answer_tokens + reasoning_headroom_tokens` to repeated intelligence,
session, and tool checks. Its streamed chat/context probe instead sent only the
legacy `--max-tokens` value and also reserved context space using that smaller
cap. Reasoning models could exhaust the stream in hidden reasoning, producing
`stream completed without visible content`, while every repeated quality suite
passed.

## Fix

Resolve the protocol budget once at quality-run start. Use its combined
`max_completion_tokens` for both context-window clamping and the streamed
chat/context request. A regression test proves 128 visible plus 512 reasoning
headroom sends a 640-token completion cap.
