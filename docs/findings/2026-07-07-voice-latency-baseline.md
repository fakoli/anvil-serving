# Anvil Voice latency baseline for OpenClaw Talk (2026-07-07)

> **STATUS: BASELINE CAPTURED for `voice-latency-model-ab:T001`.** The active
> Mini-local audio profile routes STT and TTS through loopback MLX Audio serves
> on Fakoli Mini, and routes the LLM turn to the Fakoli Dark router as
> `fast-local` / `qwen36-27b`.

This note records the current baseline before candidate LLM A/B testing. It is
not a promotion recommendation and does not change production routing.

## Environment

| Field | Value |
|---|---|
| Capture date | 2026-07-07 |
| Gateway / voice host | Fakoli Mini (`Fakoli-Mini-2.local`) |
| Benchmark checkout | `/Users/sdoumbouya/anvil-serving-t007` |
| Benchmark checkout revision | `6d024c2480b9f935b06ae26213ca7cfb69568485` |
| Checkout status | detached `HEAD`; unrelated dirty file `docs/findings/2026-07-voice-16gb-mini.md` |
| Voice manifest | `examples/voice/openclaw-anvil-voice.toml` |
| Voice profile | `mini-audio` |
| Command path | `/Users/sdoumbouya/anvil-serving-t007/.venv/bin/anvil-serving` |
| Realtime endpoint | `ws://127.0.0.1:8765/v1/realtime` |
| LLM endpoint | `http://100.87.34.66:8000/v1` |
| LLM request model | `fast-local` |
| Router auth env | `ANVIL_ROUTER_TOKEN` |
| STT endpoint | `http://127.0.0.1:30010/v1` |
| TTS endpoint | `http://127.0.0.1:30011/v1` |

The non-interactive SSH shell did not load `ANVIL_ROUTER_TOKEN`; the already
running Realtime process did have it. The benchmark command used that token
in-memory as an environment variable and did not print or write the token.

## Active Services

Mini processes at capture time:

| PID | Role | Command |
|---:|---|---|
| 14711 | STT | `.venv/bin/python -m mlx_audio.server --host 127.0.0.1 --port 30010 --log-dir /tmp/anvil-voice-mini` |
| 14712 | TTS | `.venv/bin/python -m mlx_audio.server --host 127.0.0.1 --port 30011 --log-dir /tmp/anvil-voice-mini` |
| 61505 | Realtime | `.venv/bin/anvil-serving voice run --config examples/voice/openclaw-anvil-voice.toml --profile mini-audio` |

Port checks:

| Port | Status |
|---:|---|
| `127.0.0.1:8765` | open |
| `127.0.0.1:30010` | open |
| `127.0.0.1:30011` | open |

Endpoint model identity:

| Stage | Model |
|---|---|
| STT | `mlx-community/parakeet-tdt-0.6b-v3` |
| TTS | `mlx-community/Kokoro-82M-bf16` |

## Route Proof

Decision-only `/v1/route` probe:

| Field | Value |
|---|---|
| URL | `http://100.87.34.66:8000/v1/route` |
| HTTP status | `200` |
| Request model | `fast-local` |
| Provider | `fast-local` |
| Served model | `qwen36-27b` |
| Tier | `local` |
| Work class | `chat-fast` |
| Validation errors | none |

Router response summary:

```json
{
  "confidence": 0.9,
  "model": "qwen36-27b",
  "provider": "fast-local",
  "reason": "pinned; quality gate: on",
  "tier": "local",
  "work_class": "chat-fast"
}
```

## Benchmark Command

The baseline was captured on Fakoli Mini:

```bash
cd ~/anvil-serving-t007
.venv/bin/anvil-serving voice benchmark \
  --config examples/voice/openclaw-anvil-voice.toml \
  --profile mini-audio
```

The same command run from the Windows checkout was a negative control: it could
not reach `127.0.0.1:30010` because that loopback address is Mini-local, not the
operator workstation.

## Three-Run Baseline

The benchmark uses a generated STT/TTS round trip. In this capture the STT
hypothesis was empty for all three runs (`stt_wer = 1.0`), so the STT quality
metric is not useful here. The stage timings still exercise the configured
STT, LLM, and TTS HTTP paths and are valid for baseline latency attribution.

| Run | TTFA ms | Turn latency ms | STT ms | LLM ms | TTS ms | TTS RTF | TTS requests | Output bytes |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 3041.72 | 3299.18 | 329.72 | 421.54 | 2547.93 | 0.8025 | 2 | 152400 |
| 2 | 1014.89 | 1230.51 | 500.21 | 364.88 | 365.42 | 0.1151 | 2 | 152400 |
| 3 | 465.04 | 627.60 | 99.15 | 229.42 | 299.02 | 0.0942 | 2 | 152400 |

Median across all three runs:

| Metric | Median |
|---|---:|
| TTFA | 1014.89 ms |
| Turn latency | 1230.51 ms |
| STT | 329.72 ms |
| LLM | 364.88 ms |
| TTS | 365.42 ms |

Steady-state view using runs 2-3 only:

| Metric | Average |
|---|---:|
| TTFA | 739.97 ms |
| Turn latency | 929.06 ms |
| STT | 299.68 ms |
| LLM | 297.15 ms |
| TTS | 332.22 ms |

Run 1 is a warm-up outlier dominated by TTS. Runs 2-3 are more representative
of the warmed Mini-local audio path. Compared with prior live Realtime Talk
timings, the benchmark path is a useful smoke measurement but not a substitute
for a full iOS/OpenClaw conversational turn.

## Interpretation

For this CLI benchmark, first-audio latency is the sum of:

1. STT HTTP round trip and decode,
2. LLM response generation through the Dark router,
3. the first TTS audio response.

The warmed benchmark split is roughly balanced between STT, LLM, and TTS, with
LLM around `229-365 ms` in runs 2-3. The earlier live Realtime Talk traces were
different: STT was much lower, first LLM output was the largest perceived
latency contributor, and TTS controlled the full-turn tail. That difference is
why the model A/B should include both CLI benchmark evidence and live Talk
validation.

## Verification

Local unit tests passed from the Windows checkout on the task branch:

```text
python -m pytest tests/voice/test_voice_benchmark.py tests/voice/test_voice_cli.py -q
67 passed in 3.98s
```

The Mini benchmark command passed three times after providing the router token
through `ANVIL_ROUTER_TOKEN`.

## Evidence Rerun

The T001 evidence gate was rerun on 2026-07-08 after reopening the task because
the earlier verification commands were not captured as typed Anvil evidence.
The rerun used the same Mini checkout and command, with the router token copied
in-memory from the already running Realtime process and not printed or written.

| Run | TTFA ms | Turn latency ms | STT ms | LLM ms | TTS ms | TTS RTF | TTS requests | Output bytes |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 774.13 | 965.70 | 107.45 | 447.99 | 410.26 | 0.1292 | 2 | 152400 |
| 2 | 611.29 | 789.06 | 106.28 | 356.82 | 325.95 | 0.1027 | 2 | 152400 |
| 3 | 586.71 | 762.12 | 94.38 | 353.88 | 313.87 | 0.0989 | 2 | 152400 |

Rerun median:

| Metric | Median |
|---|---:|
| TTFA | 611.29 ms |
| Turn latency | 789.06 ms |
| STT | 106.28 ms |
| LLM | 356.82 ms |
| TTS | 325.95 ms |

The rerun confirms the warmed path remains under one second TTFA in this CLI
benchmark. The first-audio latency and total turn latency are still separate
numbers: TTFA is the user-perceived first audio boundary, while turn latency
also includes complete TTS output generation.

## Follow-Up

- Use this as the `qwen36-27b` baseline for candidate LLM A/B.
- Add candidate profiles or serve recipes without overwriting `mini-audio`.
- For model promotion, require live Talk validation with memory, tool calls,
  session transcript delivery, and no duplicate-message spam.
