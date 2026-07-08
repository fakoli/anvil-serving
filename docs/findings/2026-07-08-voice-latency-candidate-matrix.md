# Voice latency candidate benchmark matrix (2026-07-08)

> Status: captured for `voice-latency-model-ab:T005`. This report records the
> baseline timing evidence, bounded candidate probes, and explicit failure modes.
> It is not a promotion recommendation.

## Scope

The matrix uses `examples/voice/openclaw-anvil-voice.toml` and the opt-in
candidate profiles added in `voice-latency-model-ab:T004`.

T006 correction: the failed non-gateway rows below are topology negative
controls. Later topology review made the correction stricter: Fakoli Mini's
16 GB RAM is reserved for OpenClaw Gateway, Anvil Voice Realtime/proxy, Claude
Code, and Codex. Reference OpenClaw Talk and candidate A/B must not run STT,
TTS, or LLM model serves on Mini. A non-gateway checkout cannot measure
Mini-local audio through its own loopback; `mini-audio` is optional
same-host/local-audio evidence only. For live candidate A/B, run Mini as the
gateway/realtime/proxy host and compose `dark-audio` or
`mini-dark-audio-proxy` with a candidate overlay after the matching Dark bridge
or Mini proxy is listening.

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
| `mini-audio` | `baseline-qwen36-27b` | non-gateway negative control failed before STT | - | - | yes |
| `candidate-qwen3-32b` | `qwen3-32b-nvfp4` | failed before STT | - | - | yes |
| `candidate-gemma4-12b` | `gemma4-12b-it` | failed before STT | - | - | yes |
| `candidate-gemma4-e4b` | `gemma4-e4b-it` | failed before STT | - | - | yes |
| `candidate-qwen3-32b` | weather/location tool turn | blocked before live tool validation | - | - | yes |

## Interpretation

The measured optional Mini-local rerun remains the only successful timing row
in this matrix: median TTFA `611.29 ms`, median turn latency `789.06 ms`,
median LLM stage `356.82 ms`. It is not the reference candidate benchmark
baseline.

The non-gateway required verification command exited `0`, but it could not
measure latency because `127.0.0.1:30010` is gateway-local to Fakoli Mini, not
to this checkout:

```text
STT stage: request to http://127.0.0.1:30010/v1/audio/transcriptions failed:
WinError 10061
```

The same endpoint failure occurred for the three direct candidate profiles
because those profiles were LLM-only shortcuts and inherited the old
Mini-local audio path in that revision. Those failed candidates are
intentionally retained in the fixture with their profile, candidate identity,
route identity, source revision, turn shape, timing fields, and error messages.

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

Next gate: run the candidate benchmark with Fakoli Mini as gateway/realtime/
proxy only. First verify the Dark bridge or Mini-side proxy and select
`dark-audio` or `mini-dark-audio-proxy`. Compose selected LLM candidates with
`--candidate-overlay`. Use `mini-audio` only for an explicit optional
same-host/local-audio validation task.
