# voice-pipeline v1 — build status + pre-bring-up punch list (2026-07-04)

Branch: `feat/voice-pipeline` (not pushed). Built by Sonnet-5 workflow agents, reviewed per-unit by
Opus-4.8, plus a final independent whole-subsystem Opus-4.8 gate; 3 blocking bugs it found were fixed.
**Full repo suite: 1257 passed. Not merged, not run on hardware.**

## What "done" means here

**Code-complete + unit-tested + reviewed on a branch.** NOT "proven working" — every live/audio/GPU
behavior is unexercised. The honesty banners across `scripts/voice/*` and the findings skeletons say so.

## anvil board (voice-pipeline PRD, 12 done / 6 ready)

| Done (code-complete) | Hardware-gated (still `ready`) |
|---|---|
| T001 scaffold + `voice` verb | **T007** STT A/B preflight (sm_120) |
| T002 manifest + hygiene | **T009** TTS A/B preflight (sm_120) |
| T003 orchestrator spine | **T010** local-loop live proof |
| T004 LLM stage → anvil router | **T014** Realtime SDK live proof |
| T005 VAD + barge-in cancel-scope | **T016** 16GB Mini validation |
| T006 STT serve adapter + stage | **T017** independent live-proof gate |
| T008 TTS serve adapter + stage | |
| T011 stdlib WebSocket transport | |
| T012 Realtime protocol tables | |
| T013 session pool (isolated) | |
| T015 `voice benchmark` | |
| T018 router `chat-fast` work-class | |

The 6 hardware-gated tasks have runnable harness scripts (`scripts/voice/*`) but require fakoli-dark
GPUs + real audio + the official OpenAI Realtime SDK, so they cannot run in CI or an agent sandbox.

## Final-gate blocking bugs — FIXED (commit 867b5d8)

- **B1** barge-in stale-audio leak — drain loop now drops any superseded-generation message.
- **B2** cancelled turn now emits exactly one terminal `response.done status="cancelled"`.
- **B3** SessionPool claim/release race — `in_use` held across the whole drain+reconstruct.
5 regression tests added (stale-audio-dropped, cancel-terminal, cancel-idle-silent, pool
claim-during-drain, double-release-idempotent).

## Pre-bring-up punch list (final gate, non-blocking but real)

Ordered by impact on the live bring-up. **These are the gap between "code-complete" and "a voice
agent that meets its goals on fakoli-dark."**

1. **Streaming latency (highest impact).** `BaseStage` emits only *after* `process()` returns, and
   `LLMStage.process()` accumulates the whole reply first — so first-audio latency ≈ full-reply
   latency, defeating the "chat-fast" premise. Needs generator-style incremental emission from the
   stages (yield chunks as they stream, not a batched return). **Fix before any latency claim.**
2. **`voice run` end-to-end wiring.** `VoicePipeline`'s `stt_stage=`/`tts_stage=` constructor seam is
   unusable (queues built after the stubs), so the real wiring lives in `scripts/voice/_real_pipeline.py`
   (`RealVoicePipeline`), and `voice/cli.py::cmd_run` is still a TODO stub. Promote the real wiring into
   `cmd_run` and fix the seam so `anvil-serving voice run` actually drives the cascade.
3. **Realtime input-side lifecycle.** `speech_started/stopped` and user-turn `conversation.item.created`
   are defined but never emitted (VAD `SpeechEvent` isn't surfaced off the internal queue); server
   `response.created/done` omit a real `response.id`. Wire before a polished demo.
4. **WebSocket hardening (before any non-loopback bind).** Single-frame size is capped but
   fragmented-message total is not (unbounded-memory risk from an authenticated peer streaming
   `fin=0` continuations); no idle/read timeout; `sendall`-under-lock write-backpressure stall.
   Acceptable on a trusted tailnet bring-up; add a running-total cap + timeout before wider exposure.
5. **Operational: deploy the `chat-fast` preset.** The live fakoli-dark router config must gain a
   `chat-fast` preset or voice traffic gets a clean (by-design) 503. Also consider narrowing the
   `raw.get("voice")` truthy net to the `modality:"voice"` marker only.
6. **Minor:** `cancel_scope.discarding` is set on cancel but `mark_settled()` is never called
   (vestigial today; matters only if a future fix keys off `discarding`). TTS per-chunk resample
   boundary drift is documented as a follow-up.

## Recommended live bring-up order (on fakoli-dark)

1. Fix punch-list #1 (streaming) and #2 (`cmd_run` wiring) — otherwise the local-loop proof can't
   show real latency.
2. Deploy #5 (`chat-fast` preset) to the live router.
3. Run **T007/T009** (STT/TTS A/B preflights) to pick the v1 engines on sm_120.
4. Run **T010** (local-loop live proof) — the first real end-to-end voice turn + barge-in + measured TTFA.
5. Wire punch-list #3, then run **T014** (Realtime SDK live proof).
6. **T016** (16GB Mini), then **T017** (independent gate over the captured live evidence).
