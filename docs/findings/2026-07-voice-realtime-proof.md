# Voice Realtime proof: official `openai` SDK client <-> anvil's Realtime server

> **STATUS: NOT YET EXECUTED.** This is a measurement-template skeleton for
> anvil task T014 (`scripts/voice/realtime_sdk_client_demo.py`). No session
> in the log below is real. Run the script with the `openai` package
> installed, the anvil router + STT/TTS serves reachable, and paste its
> printed event log / `--capture` output here.

Related: `docs/findings/2026-07-04-hf-speech-to-speech-review.md` s5 (the
Realtime server, "verified with the official OpenAI Python SDK as client")
· `anvil_serving/voice/realtime/{ws,pool,service,events}.py` ·
`scripts/voice/realtime_sdk_client_demo.py`

## Known gaps / verify-before-running (flagged, not hidden)

1. **SDK surface drift risk.** `realtime_sdk_client_demo.py`'s
   `client.realtime.connect(...)` / `connection.session.update(...)` /
   `connection.conversation.item.create(...)` / `connection.response.create()`
   / `connection.response.cancel()` / `async for event in connection` shape
   is written from the documented usage pattern, NOT verified against a
   specific installed `openai` package version. Check
   `python -c "import openai; print(openai.__version__)"`'s own examples
   before trusting the script to run unmodified.
2. **RESOLVED (PUNCH-LIST #2): `anvil-serving voice run` now exists.**
   `anvil_serving/voice/cli.py`'s `cmd_run` builds the same ws/pool/service
   cascade this script's own `build_server` assembles standalone (real STT/
   TTS/LLM stages via `pipeline.real_pipeline_factory_from_manifest`, a
   `SessionPool`, `realtime.ws.make_ws_server`), plus a reachability preflight
   and the non-loopback-requires-token refusal. This script still self-hosts
   its own server (rather than shelling out to `anvil-serving voice run`) so
   it can drive the official SDK against a server it fully controls for
   capture/barge-in timing; a follow-up could re-point it at a real
   `anvil-serving voice run` process instead and note here whether behavior
   changed.
3. **Coverage caveats inherited from the reference design** (per the review
   doc s5): server-VAD only, partial protocol (no item delete/truncate, no
   granular content-part streaming), `transcription.delta`-style events send
   the full latest hypothesis rather than an incremental suffix, no
   transport auth/TLS on the raw ws (fine for a loopback-bound demo, NOT for
   a public bind).

## How to run

```bash
python scripts/voice/realtime_sdk_client_demo.py \
  --config examples/voice/voice.example.toml \
  --text "Hello, can you hear me?" \
  --barge-in-after 1.5 \
  --capture /tmp/realtime-run1
```

## Session log

| timestamp (UTC) | turn kind (text/audio) | barge-in tested? | events captured | audio captured | notes |
|---|---|---|---|---|---|
| _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |

## Findings

_TBD once run — in particular: does the official SDK actually connect
cleanly to our stdlib `ws.py` server (RFC 6455 handshake compatibility), and
does `response.cancel` actually stop audio deltas from arriving (the
barge-in proof)?_

## Decision

_TBD — is the current partial Realtime protocol surface (see gap #3 above)
sufficient for a first real client, or does something in the "not yet
modeled" list turn out to be load-bearing once a real SDK client is talking
to it?_
