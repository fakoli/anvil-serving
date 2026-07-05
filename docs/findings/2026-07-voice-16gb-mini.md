# Voice on a 16GB Mini: local STT+TTS, LLM routed to fakoli-dark

> **STATUS: NOT YET EXECUTED.** This is a measurement-template skeleton for
> anvil task T016 (`scripts/voice/mini_validation.py`). No number below is
> real. Run the script ON a real 16GB Mini with local STT/TTS serves and a
> tailnet path to the fakoli-dark anvil router, and paste its `--report` JSON
> into the table.

Related: `docs/findings/2026-07-04-hf-speech-to-speech-review.md` s8 (VRAM/RAM
math: STT ~1-4GB, TTS ~0.5-7GB — comfortably small even on a 16GB box) · the
saved Mini<->router tailnet-binding note (router publishes its tailnet IP,
not loopback) · `scripts/voice/mini_validation.py`

## Known gaps (flagged, not hidden)

1. **Driver-process RSS is not the number that matters.** The script's own
   `resource.getrusage`-based memory reading reflects ITS OWN tiny footprint,
   not the STT/TTS containers' — the metric that matters is `docker stats`
   per container, which the script attempts but can silently return `None`
   if Docker isn't reachable/permissioned the way it expects.
2. **Windows can't run this at all.** The `resource` module doesn't exist on
   Windows; the script degrades that one field to `None` with a clear reason
   rather than crashing, but the actual target platform is macOS/Linux — this
   was never going to run on a Windows dev box in the first place.
3. **No serves.toml entries ship for the Mini's local STT/TTS containers
   yet** (mirrors the same gap noted in the STT/TTS A/B docs) — declare them
   before running, or `bring_up_ok` will read `False` with a
   `ServeNotConfigured` error, which is itself a valid (if boring) recorded
   failure mode.

## How to run

```bash
python scripts/voice/mini_validation.py \
  --config examples/voice/voice.example.toml \
  --serves-manifest ./serves.toml \
  --report docs/findings/mini-run1.json
```

## Measurement template

| metric | value | notes |
|---|---|---|
| STT startup (s) | _TBD_ | |
| STT container memory | _TBD_ | via `docker stats` |
| TTS startup (s) | _TBD_ | |
| TTS container memory | _TBD_ | via `docker stats` |
| TTFA (ms), LLM on fakoli-dark | _TBD_ | via `anvil_serving.voice.benchmark` |
| turn latency (ms) | _TBD_ | |
| driver process peak RSS (MB) | _TBD_ | informational only — see gap #1 |
| failure mode(s) observed | _TBD_ | e.g. OOM-kill, tailnet timeout, cold-start stall |

## Session log

| timestamp (UTC) | STT | TTS | TTFA/error | driver RSS (MB) | report path |
|---|---|---|---|---|---|
| _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |

(`mini_validation.py` appends a row here automatically — see
`append_finding_row` in that script.)

## Findings

_TBD once run — in particular: does total container memory (STT + TTS +
whatever else is resident on a 16GB box) leave enough headroom, or does this
reproduce a variant of the WSL2/`.wslconfig` OOM gotcha (CLAUDE.md gotcha #3)
on a different box?_

## Decision

_TBD — is a 16GB Mini viable as a standing edge node for this split, or does
it need a smaller STT/TTS pairing (e.g. Kokoro-82M + a smaller STT model) to
fit comfortably?_
