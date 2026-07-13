# Voice consult benchmark: E4B-backed `chat-fast` vs 35B baseline

**Task:** `gpu-reservations:T008` — Voice consult benchmark against E4B
**Date:** 2026-07-13
**Box:** fakoli-dark (RTX 5090), live serves. STT/TTS sidecars and the deployed
router were used read-only; no serve lifecycle was touched.

## Verdict — REGRESSION, retirement blocked

The E4B-backed `chat-fast` voice-consult path **breaches the 5 s turn-latency
budget** (max observed **13801.8 ms**, n=12). Per the T008 gate, this **blocks
finalizing the 35B (`qwen36-35b-a3b-nvfp4`) retirement**. Do not retire 35B on
the strength of this run.

## What was measured

`anvil-serving voice benchmark` replays one STT -> LLM -> TTS turn end-to-end and
records TTFA / turn latency / STT / LLM-stage / TTS / RTF. It was run against
`examples/voice/fakoli-dark.toml`, whose LLM stage points at the deployed router
(`http://100.87.34.66:8000/v1`, model `chat-fast`).

Route identity was verified independently before measuring:

```
POST /v1/route {"model":"chat-fast"}
-> {"tier":"local","model":"gemma4-e4b-it","provider":"fast-local",
    "work_class":"chat-fast","reason":"preset='chat-fast'; quality gate: on","confidence":1.0}
```

i.e. `gpu-reservations:T007` has promoted Gemma 4 E4B into the live Fast tier;
`chat-fast` now resolves to `gemma4-e4b-it`. The manifest's stale
`expected_route_model` (`qwen36-35b-a3b-nvfp4`) was corrected to `gemma4-e4b-it`
as part of this task so the recorded evidence identity is truthful.

## Results

E4B via `chat-fast` (12 runs), turn latency sorted (ms):

```
300.3  413.5  484.8  493.2  959.1  1196.7  1392.8  2051.7  2461.6  2958.5  4783.1  13801.8
```

| Metric | E4B `chat-fast` (n=12) | 35B baseline (n=1) |
|---|---:|---:|
| Turn latency ms — median | 1294.8 | 377.52 |
| Turn latency ms — mean | 2608.1 | 377.52 |
| Turn latency ms — max | **13801.8** | 377.52 |
| Runs over 5 s budget | **1 of 12** (plus run 1 observed live at 6222.3) | 0 |
| TTFA ms — max | 7198.0 | 308.04 |
| LLM-stage ms — range | 89.0 – 7079.4 | 165.4 |
| LLM reply chars — range | 34 – 5388 | 34 |

35B baseline source:
[`fast-tier-bakeoff-evidence/qwen36-35b-a3b-vllm-nvfp4-32k.voice.json`](fast-tier-bakeoff-evidence/qwen36-35b-a3b-vllm-nvfp4-32k.voice.json).
Full per-run E4B evidence and the aggregate gate:
[`2026-07-13-e4b-voice-consult-benchmark-evidence/`](2026-07-13-e4b-voice-consult-benchmark-evidence/).

## Root cause

Turn latency is dominated by TTS, and TTS time scales with the number of
characters the LLM emits. E4B's reply length for this prompt is **highly
unstable** (34–5388 chars); when it rambles (5388 chars on run 10) the turn
takes 13.8 s. The 35B baseline answered tersely (34 chars) and finished in
377 ms.

E4B's *raw* speed is not the problem: when its reply length matches the baseline
(run 13, 34-char reply) its LLM stage is 89 ms vs the 35B's 165 ms. The
regression is **verbosity-driven**, not token-rate driven. A production fix would
cap the voice LLM reply (e.g. a `speech_chunk_max_chars` / max-tokens / stop-style
brevity constraint on the `chat-fast` voice turn) rather than reverting the tier.

## Caveats

- The synthetic STT input yields an empty hypothesis (`stt_wer = 1.0`) in **both**
  the E4B runs and the 35B baseline, so the LLM answers an effectively empty user
  turn. Absolute turn latency is therefore a harness proxy, not a live-consult
  SLO. The comparison is still fair — identical harness, STT, TTS, and input.
- One additional E4B run (observed live at turn latency 6222.3 ms) is not in the
  12-run aggregate because its capture file was truncated by a `head`/SIGPIPE
  race; it is not needed — the recorded set already breaches the budget.

## Recommendation

1. **Do not finalize the 35B retirement** on this evidence (gate: turn latency
   > 5 s).
2. Before re-benchmarking, constrain the voice `chat-fast` reply length (brevity
   / max-tokens / stop tuning) so turn latency is bounded, then re-run this
   benchmark. If the constrained E4B path stays < 5 s across the tail, the
   retirement can proceed.
