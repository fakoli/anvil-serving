# OpenClaw Talk live validation (2026-07-08)

> Status: captured for `voice-latency-model-ab:T006`. This is live evidence
> from Fakoli Mini as the OpenClaw gateway / Anvil Voice host and Fakoli Dark as
> the router host.

## Topology Observed

- OpenClaw Gateway is listening on Fakoli Mini at `127.0.0.1:18789`.
- Anvil Voice is running on Mini as
  `anvil-serving voice run --config examples/voice/openclaw-anvil-voice.toml --profile mini-audio`.
- Mini-local STT/TTS are native MLX Audio processes on `127.0.0.1:30010` and
  `127.0.0.1:30011`.
- The intended Dark audio boundary may be a Mini-local proxy that forwards
  `127.0.0.1:30110` and `127.0.0.1:30111` from Mini to Fakoli Dark. Those
  conventional proxy ports were not listening during this validation.
- Dark router `http://100.87.34.66:8000/v1` was reachable. Direct Dark audio
  ports `100.87.34.66:30110` and `100.87.34.66:30111` were not reachable from
  this operator host during the check.

Interpretation: Mini-local loopback and Mini-local proxy loopback are valid
runtime targets only when the command runs on Mini. A non-gateway checkout
cannot benchmark either path by calling its own `127.0.0.1`.

## Validation Commands

```bash
python examples/openclaw/colo_smoke.py --live --run-interaction-benchmark
python -m pytest tests/voice/test_realtime_service.py tests/voice/test_pipeline_spine.py -q
python -m pytest tests/voice/test_voice_cli.py tests/voice/test_voice_config.py tests/fixtures/operator_workflows/test_voice_latency_model_ab_matrix.py -q
python -m pytest tests/test_openclaw_colo_smoke.py -q
python -m ruff check anvil_serving/voice/cli.py tests/voice/test_voice_cli.py tests/voice/test_voice_config.py tests/fixtures/operator_workflows/test_voice_latency_model_ab_matrix.py examples/openclaw/colo_smoke.py
```

## Results

The live COLO smoke exited `0` and wrote
`.anvil/evidence/openclaw-colo-smoke.json`. Verdict was `warn` only because
the required command did not include `--run-generations`; route and interaction
proofs passed:

- Authenticated router models probe: `200`.
- Route probes: `6`.
- Interaction benchmark requests: `10`.
- Interaction status counts: `10` HTTP `200`.
- Finish reasons: `10` `stop`.
- Latency p50 / p95: `568.6 ms` / `1259.9 ms`.
- Exact-generation throughput p50 / p95: `82.77` / `171.82` tokens/sec.

The focused voice tests passed: `25 passed`.

## Talk Session Evidence

OpenClaw active main session:

```text
~/.openclaw/agents/main/sessions/46b5e46d-3cf9-41c7-a87d-95d1de379204.jsonl
```

The session contains visible spoken-turn transcript delivery and tool use:

- User transcript: "So what's the weather like in San Leandro right now?"
- Tool call: `exec` with `wttr.in/San+Leandro`.
- Tool result: San Leandro weather text.
- Assistant response: a concise weather summary.

Hidden control-text scan of that session returned:

```text
Context: 0
The realtime provider produced: 0
I understand: 0
openclaw_agent_consult: 0
```

This confirms the prior forced-consult control text is not being written into
the visible session history for the checked session.

## Duplicate Message Check

The Mini decision log still contains the earlier historical burst around
`2026-07-07T14:28Z` to `2026-07-07T14:29Z` with repeated
`talk-forced-consult` rows. After cleanup, later Talk entries did not show the
same sustained burst pattern:

- `2026-07-07T15:23Z` and `2026-07-07T15:24Z`: two normal Talk turns, including
  a weather tool call and a greeting.
- `2026-07-07T17:36Z`: three short Talk entries with prompt sizes `7`, `4`,
  and `2` characters.

There was no new dense minute-long repeat sequence like the earlier 19-row
burst.

## Follow-Up Notes

Mini TTS logs retain an older Kokoro broadcast-shape error, but the
`realtime-chunk-56.log` proof for the active `mini-audio` chunk size shows TTS
stage rows with `error=false`. That instability is separate from the
Mini-vs-Dark endpoint selection issue and should be tracked as TTS backend
stability if it recurs.

T006 also corrected the operational path for future A/B runs:

- `voice run` now accepts `--candidate-overlay`, matching `voice benchmark`.
- Audio topology stays in `--profile`.
- Candidate LLM choice stays in `--candidate-overlay`.
- `mini-dark-audio-proxy` is available only for a verified Mini-side proxy on
  `127.0.0.1:30110` and `127.0.0.1:30111`.
