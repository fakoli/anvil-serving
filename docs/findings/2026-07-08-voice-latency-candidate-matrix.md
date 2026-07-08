# Voice latency candidate benchmark matrix (2026-07-08)

> Status: captured for `voice-latency-model-ab:T005`. This report records the
> baseline timing evidence, bounded candidate probes, and explicit failure modes.
> It is not a promotion recommendation.

## Scope

The matrix uses `examples/voice/openclaw-anvil-voice.toml` and the opt-in
candidate profiles added in `voice-latency-model-ab:T004`.

Durable machine-readable evidence lives in:

```text
tests/fixtures/operator_workflows/voice_latency_model_ab_matrix.json
```

The current checkout revision used for the bounded probes was:

```text
b579dab957dc098339f1d175eabad5417bca4982
```

## Results

| Profile | Candidate | Status | TTFA ms | LLM ms | Failure retained |
|---|---|---|---:|---:|---|
| `mini-audio` | `baseline-qwen36-27b` | measured prior rerun | 611.29 | 356.82 | no |
| `mini-audio` | `baseline-qwen36-27b` | Windows negative control failed before STT | - | - | yes |
| `candidate-qwen3-32b` | `qwen3-32b-nvfp4` | failed before STT | - | - | yes |
| `candidate-gemma4-12b` | `gemma4-12b-it` | failed before STT | - | - | yes |
| `candidate-gemma4-e4b` | `gemma4-e4b-it` | failed before STT | - | - | yes |
| `candidate-qwen3-32b` | weather/location tool turn | blocked before live tool validation | - | - | yes |

## Interpretation

The measured Mini rerun remains the only successful timing row in this matrix:
median TTFA `611.29 ms`, median turn latency `789.06 ms`, median LLM stage
`356.82 ms`.

The Windows-side required verification command exited `0`, but it could not
measure latency because `127.0.0.1:30010` is Mini-local and this checkout could
not reach that STT endpoint:

```text
STT stage: request to http://127.0.0.1:30010/v1/audio/transcriptions failed:
WinError 10061
```

The same endpoint failure occurred for the three direct candidate profiles.
Those failed candidates are intentionally retained in the fixture with their
profile, candidate identity, route identity, source revision, turn shape, timing
fields, and error messages.

## Tool-Relevant Turn

The matrix includes a retained tool-relevant row:

```text
What's the weather in 94107?
```

That row is blocked until the live Mini topology is available for T006. It is
kept because the model A/B must validate more than raw first-audio latency: a
winning candidate also has to preserve weather/location tool use, session
memory, and chat transcript behavior.

## Recommendation

Do not promote any candidate from this matrix alone.

Next gate: run `voice-latency-model-ab:T006` from the correct OpenClaw/Mini
topology after Mini-local STT/TTS and the selected Dark candidate serve are
reachable.
