# Voice latency final recommendation (2026-07-08)

> Status: captured for `voice-latency-model-ab:T007`.

## Recommendation

Do not promote a voice LLM candidate yet. Gather more comparable data with
Fakoli Mini kept model-free before changing the production model or routing
profile.

Production promotion remains explicitly human-gated through the normal
`router_promote` / `anvil-serving router promote` workflow. This benchmark work
does not change `[router].profile_path`, router policy, OpenClaw production
model selection, or cloud settings.

## Evidence Summary

The only successful voice timing row is the historical optional Mini-local
audio baseline from
`docs/findings/2026-07-08-voice-latency-candidate-matrix.md`:

| Profile | Candidate | TTFA ms | Turn ms | STT ms | LLM ms | TTS ms |
|---|---|---:|---:|---:|---:|---:|
| `mini-audio` | `baseline-qwen36-27b` | 611.29 | 789.06 | 106.28 | 356.82 | 325.95 |

The LLM and TTS stages are co-dominant: LLM is about `45%` of total turn
latency and TTS is about `41%`. STT is not the bottleneck in this row.

Post-report topology correction: Fakoli Mini's 16 GB RAM is reserved for
OpenClaw Gateway, Anvil Voice Realtime/proxy, Claude Code, and Codex. Do not
run STT/TTS/LLM model serves on Mini for reference Talk validation or candidate
A/B. Treat the table above as optional same-host/local-audio evidence only.

The candidate rows are retained evidence, but they are not valid latency
comparisons. They failed before STT because the run happened from a
non-gateway checkout whose `127.0.0.1` was not Fakoli Mini's loopback. That is a
topology negative control, not proof that the candidate LLMs are slow or fast.

The live OpenClaw Talk validation in
`docs/findings/2026-07-08-openclaw-talk-live-validation.md` showed the current
path is functionally healthy: session memory persisted, tool calls worked, chat
session transcript delivery was visible, hidden control text was absent, and
duplicate message spam did not recur.

## Benchmark Workflow

Run the baseline with Mini as gateway/realtime/proxy only. Use `dark-audio`
after the Dark bridge is verified, or `mini-dark-audio-proxy` after the Mini
proxy forwards to Dark:

```bash
anvil-serving voice benchmark \
  --config examples/voice/openclaw-anvil-voice.toml \
  --profile dark-audio \
  --evidence-out .anvil/evidence/voice-baseline-dark-audio.json
```

Run an LLM candidate with the same audio topology:

```bash
anvil-serving serves --manifest examples/fakoli-dark/serves.toml up voice-qwen3-32b
anvil-serving voice benchmark \
  --config examples/voice/openclaw-anvil-voice.toml \
  --profile dark-audio \
  --candidate-overlay examples/voice/candidates/qwen3-32b-nvfp4.toml \
  --candidate qwen3-32b-nvfp4 \
  --evidence-out .anvil/evidence/voice-qwen3-32b-dark-audio.json
```

Use `--profile mini-audio` only for explicit optional same-host/local-audio
validation. A non-gateway checkout cannot validate Mini proxy or optional
Mini-local audio loopback paths by calling its own `127.0.0.1`.

## Stage Decision Rule

Use comparable successful runs with the same audio topology, prompt set, and
gateway host.

- A stage dominates when its p50 elapsed time is at least half of total turn
  latency, or at least twice the next-largest stage.
- Work on the LLM/model path when LLM dominates, or when LLM first-output is
  above about `300 ms` while STT and TTS are below their thresholds.
- Work on STT when STT p50 exceeds about `200 ms`, WER is unacceptable, or STT
  errors are present.
- Work on TTS/chunking when TTS p50 exceeds about `350 ms`, TTS first-output
  exceeds about `250 ms`, or TTS stream errors recur.
- If no stage dominates, prefer cheaper prompt/chunk/profile tuning before
  loading another model.

## Next Data To Gather

1. Re-run the baseline with Mini model-free and capture a durable JSON artifact.
2. Run at least one candidate overlay on the same Dark-host or Mini-proxied
   audio profile.
3. Include one tool-relevant OpenClaw Talk turn after the candidate run, not
   only generated PCM benchmark audio.
4. Promote only if the candidate improves latency without regressing tool use,
   memory, transcript delivery, or TTS stability, and only after a human approves
   the router promotion gate.
