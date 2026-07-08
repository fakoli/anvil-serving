# OpenClaw Anvil Voice option discovery

Date: 2026-07-06

> **Topology addendum (2026-07-08).** The live Mini-local audio validation
> below remains historical evidence for the optional same-host/local-audio
> mode. It is no longer the reference OpenClaw Talk topology. Fakoli Mini's
> 16 GB RAM is reserved for OpenClaw Gateway, Anvil Voice Realtime/proxy, Claude
> Code, and Codex; normal Talk validation and candidate A/B should use Dark-host
> STT/TTS or a Mini proxy to Dark.

## Scope

This note captures the baseline research for Anvil task `openclaw-anvil-voice-option:T001`. The goal is to add Anvil Voice as an OpenClaw realtime voice option without changing the existing OpenClaw client relay protocol.

## Sources inspected

anvil-serving checkout:

- Commit: `0d67db9`
- `README.md`
- `CLAUDE.md`
- `docs/VOICE.md`
- `docs/OPENCLAW-INTEGRATION-SPEC.md`
- `docs/OPERATOR-PLAYBOOKS.md`
- `docs/OPERATOR-SKILLS-AND-SUBAGENTS.md`
- `examples/voice/fakoli-mini.toml`
- `docs/findings/2026-07-voice-16gb-mini.md`
- `anvil_serving/voice/config.py`
- `anvil_serving/voice/realtime/events.py`
- `anvil_serving/voice/realtime/service.py`
- `anvil_serving/voice/realtime/ws.py`
- `tests/voice/test_realtime_service.py`

OpenClaw checkout on Fakoli Mini:

- Deployment worktree: `/Users/sdoumbouya/openclaw-node`
- Local branch state before implementation research: `main...origin/main [behind 6588]`
- Local dirty file: `npm-shrinkwrap.json`
- Local HEAD before fetch: `5e1fbca3cb`
- Current `origin/main` after fetch: `80537c1ba4 feat(macos): load provider catalog during AI onboarding (#101132)`
- Root `AGENTS.md`
- `extensions/AGENTS.md`
- Current `origin/main` files inspected via `git show`:
  - `src/talk/provider-types.ts`
  - `extensions/openai/realtime-voice-provider.ts`
  - `extensions/openai/openclaw.plugin.json`
- Deployment worktree files inspected before discovering staleness:
  - `src/talk/provider-registry.ts`
  - `src/talk/provider-resolver.ts`
  - `src/talk/session-runtime.ts`
  - `src/config/talk.ts`
  - `src/config/zod-schema.ts`
  - `src/gateway/server-methods/talk-client.ts`
  - `src/gateway/server-methods/talk-session.ts`
  - `src/gateway/talk-realtime-relay.ts`
  - `ui/src/ui/chat/realtime-talk.ts`
  - `ui/src/ui/chat/realtime-talk-shared.ts`
  - `ui/src/ui/chat/realtime-talk-webrtc.ts`
  - `ui/src/ui/chat/realtime-talk-google-live.ts`
  - `ui/src/ui/chat/realtime-talk-gateway-relay.ts`
  - `extensions/elevenlabs/index.ts`
  - `extensions/elevenlabs/realtime-transcription-provider.ts`
  - `extensions/elevenlabs/openclaw.plugin.json`
  - `extensions/google/realtime-voice-provider.ts`

Implementation must target a fresh checkout based on current OpenClaw `origin/main`, not the stale Mini deployment worktree.

## OpenClaw contract findings

OpenClaw realtime voice is exposed through `RealtimeVoiceProviderPlugin` and `RealtimeVoiceBridge` contracts. The important bridge callbacks are:

- `onAudio(Buffer)` for provider audio deltas.
- `onClearAudio()` for barge-in playback clearing.
- `onTranscript(role, text, isFinal)` for user and assistant transcript events.
- `onEvent(...)` for lifecycle/debug events.
- `onToolCall(...)` for provider tool calls.
- `onReady()`, `onError(Error)`, and `onClose(...)` for session lifecycle.

The current provider capability surface supports the transports `webrtc`, `provider-websocket`, `gateway-relay`, and `managed-room`. OpenAI Realtime currently declares `webrtc` and `gateway-relay`. Google Live has a provider WebSocket path, but the browser-side `provider-websocket` implementation in the inspected deployment worktree is Google protocol specific rather than a generic OpenAI-Realtime WebSocket client.

ElevenLabs is not a realtime voice provider in the inspected OpenClaw code. It registers speech, media understanding, and realtime transcription providers. "Next to ElevenLabs" therefore means next to the broader voice/STT provider options and documentation/setup surfaces, while the Anvil speech-to-speech path belongs in the realtime voice provider family alongside OpenAI Realtime.

The extension boundary in `extensions/AGENTS.md` is strict: bundled extension production code must import from `openclaw/plugin-sdk/*`, local extension files, Node built-ins, and declared plugin dependencies. It must not deep-import OpenClaw `src/**` internals or another extension's private code. The Anvil provider therefore cannot reuse OpenAI extension internals directly unless common behavior is first promoted to a public SDK helper. The initial implementation should keep provider logic local to the new extension unless a small public helper is clearly warranted.

## Anvil Voice protocol findings

Anvil Voice already exposes an OpenAI-Realtime-like WebSocket through `anvil_serving.voice.realtime.ws`. The same-host example in `examples/voice/fakoli-mini.toml` binds:

- host: `127.0.0.1`
- port: `8765`
- path: `/v1/realtime`
- LLM backend: `http://100.87.34.66:8000/v1`
- model: `fast-local`
- auth env: `ANVIL_ROUTER_TOKEN`

The realtime subset accepted by `RealtimeService` is:

- `session.update`
- `input_audio_buffer.append`
- `input_audio_buffer.commit`
- `input_audio_buffer.clear`
- `conversation.item.create`
- `response.create`
- `response.cancel`

The emitted server event subset is:

- `session.updated`
- `input_audio_buffer.speech_started`
- `input_audio_buffer.speech_stopped`
- `input_audio_buffer.committed`
- `conversation.item.created`
- `conversation.item.input_audio_transcription.completed`
- `response.created`
- `response.output_audio.delta`
- `response.output_audio_transcript.delta`
- `response.done`
- `error`

Audio deltas use base64-encoded PCM bytes. Inbound audio append also uses base64 PCM bytes. `RealtimeService` frames committed audio at 16 kHz mono PCM16 frame sizes internally (`DEFAULT_FRAME_BYTES = 640`, 20 ms), then the pipeline owns VAD/STT/LLM/TTS.

The WebSocket transport allows missing bearer auth for loopback clients. Non-loopback deployments must configure a token. Examples must use `127.0.0.1` for local URLs and environment/secret references for tokens.

## Chosen implementation shape

The v1 implementation should add a bundled OpenClaw extension with realtime voice provider id `anvil`.

The provider should declare only `gateway-relay` transport support initially. This is the lowest-risk path because:

- OpenClaw already has a generic gateway-managed relay that talks to provider bridges server-side.
- The inspected direct browser `provider-websocket` path is not a generic OpenAI-Realtime WebSocket transport.
- Anvil Voice auth and private/tailnet endpoints are better kept server-side.
- The implementation can be tested daemonlessly with a fake Anvil WebSocket server without involving browser media APIs.

The provider bridge should:

- Normalize config from `talk.realtime.providers.anvil`.
- Build a WebSocket URL from either an explicit realtime URL or a base URL, defaulting same-host examples to `ws://127.0.0.1:8765/v1/realtime`.
- Reject the loopback hostname alias in shipped docs/examples and avoid logging secrets.
- Attach bearer auth only when configured.
- Send `session.update` after connect.
- Forward microphone PCM as `input_audio_buffer.append` and commit turns through `input_audio_buffer.commit`.
- Send `response.cancel` for barge-in/cancel.
- Map `response.output_audio.delta` and legacy-compatible audio event names to `onAudio`.
- Map final user transcription and assistant transcript deltas to `onTranscript`.
- Emit lifecycle events through `onEvent`.
- Close cleanly and surface malformed events as bounded errors.

## Bugs and blockers found before coding

- The Fakoli Mini OpenClaw deployment checkout is stale relative to `origin/main` and has an unrelated dirty lockfile. It is a validation target only until updated or a separate clean worktree is prepared.
- OpenClaw's direct `provider-websocket` browser path in the inspected deployment worktree is Google Live specific, so Anvil browser-direct transport would require additional UI/client protocol work. That is deferred by PRD decision D002.
- The OpenAI realtime provider hardcodes OpenAI/Azure auth and endpoint behavior; it is not configurable into an Anvil Voice provider. A separate provider is required.
- The Anvil CLI work packet rendered a verification command using `anvil task show`, but this CLI version exposes the command as `anvil show`. For T001 evidence, use `anvil show openclaw-anvil-voice-option:T001 --prd openclaw-anvil-voice-option`.

No Anvil Voice protocol bug blocking the gateway-relay approach was found in the inspected code. The main implementation risk is sample-rate conversion between OpenClaw relay audio and Anvil Voice's internal VAD framing; T006 must prove the bridge behavior with daemonless tests before live validation.

## T002 current OpenClaw branch baseline

A fresh OpenClaw checkout was created in sibling directory `../openclaw-anvil-voice` so the implementation branch is independent from the stale Mini deployment worktree.

- Branch: `codex/anvil-voice-openclaw-option`
- Base: `66b4dcf184 chore(docker): execute Compose and package artifact proofs (#101045)`
- Status after setup: clean
- Node: `v24.5.0`
- pnpm: `11.2.2`
- Dependency install: `corepack pnpm install` completed successfully.

Baseline focused tests:

- `node scripts/run-vitest.mjs run --config test/vitest/vitest.gateway.config.ts src/gateway/talk-realtime-relay.test.ts src/gateway/server-methods/talk.test.ts`
  - Result: pass, 5 files, 150 tests.
- `node scripts/run-vitest.mjs run --config test/vitest/vitest.extension-providers.config.ts extensions/openai/realtime-voice-provider.test.ts extensions/google/realtime-voice-provider.test.ts`
  - Result: pass, 1 file, 27 tests. This provider shard includes the Google realtime provider; OpenAI needs its dedicated config because it aliases `ws`.
- `node scripts/run-vitest.mjs run --config test/vitest/vitest.extension-provider-openai.config.ts openai/realtime-voice-provider.test.ts`
  - Result: pass, 1 file, 63 tests.

The first attempted provider baseline command used `test/vitest/vitest.extensions.config.ts` with repo-root paths and found no tests. That was a command/config mismatch, not a source failure; the corrected provider shard commands above passed.

## T003 OpenClaw Anvil realtime voice provider implementation

Implemented a new bundled OpenClaw extension in the fresh OpenClaw checkout at `../openclaw-anvil-voice`:

- `extensions/anvil-voice/package.json`
- `extensions/anvil-voice/openclaw.plugin.json`
- `extensions/anvil-voice/index.ts`
- `extensions/anvil-voice/realtime-voice-provider.ts`
- `extensions/anvil-voice/index.test.ts`
- `extensions/anvil-voice/realtime-voice-provider.test.ts`
- `pnpm-lock.yaml`

The extension registers realtime voice provider id `anvil` with label `Anvil Voice`, default model `fast-local`, and gateway relay support only. The bridge normalizes explicit realtime URLs or base URLs into the Anvil `/v1/realtime` WebSocket endpoint, rejects loopback hostname aliases, rejects public cleartext WebSocket URLs, attaches bearer auth only when configured, sends `session.update`, resamples OpenClaw relay PCM16 24 kHz audio to Anvil PCM16 16 kHz, commits buffered speech after sustained silence, maps Anvil audio/transcript/lifecycle events back to OpenClaw callbacks, and sends `response.cancel` plus `input_audio_buffer.clear` for barge-in.

Focused validation:

- `corepack pnpm install`
  - Result: pass after stopping a stale timed-out child process and rerunning; workspace was already up to date on rerun.
- `node scripts/run-vitest.mjs run --config test/vitest/vitest.extensions.config.ts anvil-voice/realtime-voice-provider.test.ts anvil-voice/index.test.ts`
  - Result: pass, 2 files, 11 tests.
- `corepack pnpm test:extension anvil-voice`
  - Result: pass, 2 files, 11 tests after marking the new extension files intent-to-add so OpenClaw's `git ls-files` based test planner can discover them.
- `node scripts/run-vitest.mjs run --config test/vitest/vitest.gateway.config.ts src/gateway/talk-realtime-relay.test.ts src/gateway/server-methods/talk.test.ts`
  - Result: pass, 5 files, 150 tests.
- `node scripts/run-vitest.mjs run --config test/vitest/vitest.extension-providers.config.ts google/realtime-voice-provider.test.ts`
  - Result: pass, 1 file, 27 tests.
- `node scripts/run-vitest.mjs run --config test/vitest/vitest.extension-provider-openai.config.ts openai/realtime-voice-provider.test.ts`
  - Result: pass, 1 file, 63 tests.
- `node scripts/check-extension-package-tsc-boundary.mjs --mode=compile`
  - Result: pass, 118 extension package compiles fresh or cached.
- `node scripts/check-extension-plugin-sdk-boundary.mjs --mode=plugin-sdk-internal extensions/anvil-voice`
  - Result: pass, no boundary violations.
- `node scripts/check-extension-plugin-sdk-boundary.mjs --mode=relative-outside-package extensions/anvil-voice`
  - Result: pass, no boundary violations.
- `node scripts/run-oxlint.mjs --tsconfig config/tsconfig/oxlint.extensions.json extensions/anvil-voice`
  - Result: pass after preserving the original URL parse error as an Error cause.
- `git diff --check`
  - Result: pass.

## T004 OpenClaw config, catalog, and docs support

Updated OpenClaw selection documentation and generated plugin catalog outputs so Anvil Voice is discoverable as realtime voice provider id `anvil` next to Google Live and OpenAI Realtime.

OpenClaw docs/catalog files changed:

- `docs/providers/anvil-voice.md`
- `extensions/anvil-voice/README.md`
- `docs/plugins/voice-call.md`
- `docs/nodes/talk.md`
- `docs/web/control-ui.md`
- `docs/plugins/plugin-inventory.md`
- `docs/plugins/reference.md`
- `docs/plugins/reference/anvil-voice.md`
- Generated plugin reference refreshes for provider manifests that were stale against the current generator output.

Documented selection paths:

- Control UI Talk: `talk.realtime.provider = "anvil"`, `talk.realtime.transport = "gateway-relay"`, and `talk.realtime.providers.anvil.realtimeUrl` or `baseUrl`.
- Voice Call: `plugins.entries.voice-call.config.realtime.provider = "anvil"` and `plugins.entries.voice-call.config.realtime.providers.anvil`.
- Same-host examples use `ws://127.0.0.1:8765/v1/realtime` with no token.
- Remote examples use `https://anvil-voice.example.com` plus `apiKey: { source: "env", provider: "default", id: "ANVIL_ROUTER_TOKEN" }`.
- Docs explicitly distinguish Anvil speech-to-speech from ElevenLabs realtime transcription and regular TTS.

Focused validation:

- `corepack pnpm plugins:inventory:check`
  - Result: pass.
- `corepack pnpm docs:check-mdx docs/providers/anvil-voice.md docs/plugins/voice-call.md docs/nodes/talk.md docs/web/control-ui.md`
  - Result: pass.
- `corepack pnpm exec oxfmt --check --config .oxfmtrc.jsonc docs/providers/anvil-voice.md docs/plugins/voice-call.md docs/nodes/talk.md docs/web/control-ui.md extensions/anvil-voice/README.md`
  - Result: pass. The higher-level `corepack pnpm format:docs:check ...` wrapper failed on Windows because it expands the whole docs tree into an overlong command line before `oxfmt`; the direct formatter check above is the same formatter on the touched docs.
- `corepack pnpm test:extension anvil-voice`
  - Result: pass, 3 files, 14 tests.
- `node scripts/run-oxlint.mjs --tsconfig config/tsconfig/oxlint.extensions.json extensions/anvil-voice`
  - Result: pass.
- `node scripts/check-extension-package-tsc-boundary.mjs --mode=compile`
  - Result: pass, 118 extension package compiles fresh or cached.
- Diff-only loopback-alias scan
  - Result: pass. No new diff lines introduce a local URL using that hostname alias.
- `git diff --check`
  - Result: pass.

## T006 daemonless fake Anvil realtime server validation

Added `extensions/anvil-voice/realtime-voice-provider.integration.test.ts` in the OpenClaw checkout. The test starts an in-process `ws` server on `127.0.0.1` and exercises the provider through a real WebSocket handshake rather than the mocked socket used by unit tests.

Covered daemonless scenarios:

- Connect sends `session.update` with model `fast-local` and Anvil PCM16 16 kHz audio format.
- Bearer auth is sent only when a token is configured; loopback sessions without a token omit `Authorization`.
- A microphone audio turn is resampled from OpenClaw relay PCM16 24 kHz to Anvil PCM16 16 kHz, appended, committed after silence, and receives fake-server audio back through `onAudio`.
- Final user transcript and assistant transcript deltas are forwarded through `onTranscript`.
- Server `error` events are surfaced as bounded `onError` callbacks.
- Barge-in sends `response.cancel` and `input_audio_buffer.clear`, calls `onClearAudio`, and suppresses late audio deltas for the cancelled response.

Implementation bug found and fixed during T006: the initial T003 bridge cleared client playback on barge-in but would still forward a late `response.output_audio.delta` for the cancelled response if the fake server emitted one. The bridge now tracks cancelled response ids and suppresses matching late audio until the response is completed or cancelled server-side.

Focused validation:

- `node scripts/run-vitest.mjs run --config test/vitest/vitest.extensions.config.ts anvil-voice/realtime-voice-provider.test.ts anvil-voice/realtime-voice-provider.integration.test.ts anvil-voice/index.test.ts`
  - Result: pass, 3 files, 14 tests.
- `corepack pnpm test:extension anvil-voice`
  - Result: pass, 3 files, 14 tests.
- `node scripts/run-vitest.mjs run --config test/vitest/vitest.gateway.config.ts src/gateway/talk-realtime-relay.test.ts src/gateway/server-methods/talk.test.ts`
  - Result: pass, 5 files, 150 tests.
- `node scripts/check-extension-package-tsc-boundary.mjs --mode=compile`
  - Result: pass, 118 extension package compiles fresh or cached.
- `node scripts/run-oxlint.mjs --tsconfig config/tsconfig/oxlint.extensions.json extensions/anvil-voice`
  - Result: pass.
- `git diff --check`
  - Result: pass.

## T005 anvil-serving OpenClaw Anvil Voice examples and sync support

Updated anvil-serving so it can render the OpenClaw Talk realtime provider block for Anvil Voice and document the Mini topology end to end.

Files changed in anvil-serving:

- `anvil_serving/harness.py`
- `examples/voice/openclaw-anvil-voice.toml`
- `tests/test_openclaw_sync_anvil_voice.py`
- `docs/VOICE.md`
- `docs/OPENCLAW-INTEGRATION-SPEC.md`
- `docs/OPERATOR-PLAYBOOKS.md`

Implemented sync support:

- `anvil-serving harness sync openclaw --voice` now adds `talk.realtime.provider = "anvil"`, `talk.realtime.transport = "gateway-relay"`, and `talk.realtime.providers.anvil.realtimeUrl`.
- The default generated Realtime URL is `ws://127.0.0.1:8765/v1/realtime`.
- `--voice-api-key-env ANVIL_VOICE_REALTIME_TOKEN` emits an env-backed SecretRef for private/tailnet Anvil Voice endpoints; loopback examples omit `apiKey`.
- Existing model-provider sync output remains unchanged unless `--voice` is explicitly passed.

Documented operator path:

- Start audio lifecycle with `anvil-serving voice up --config examples/voice/openclaw-anvil-voice.toml`.
- Start the foreground Anvil Voice Realtime server with `anvil-serving voice run --config examples/voice/openclaw-anvil-voice.toml`.
- Render or apply OpenClaw config with `anvil-serving harness sync openclaw --voice --voice-realtime-url ws://127.0.0.1:8765/v1/realtime`.

Static hygiene:

- The new example manifest validates through `anvil_serving.voice.config.load_manifest`.
- The new static test verifies the example contains no loopback hostname alias and no common secret-shaped key prefixes.
- The generated OpenClaw Talk config selects provider `anvil` and points at the Anvil Voice Realtime URL.

Focused validation:

- `python -m pytest tests/test_openclaw_sync_anvil_voice.py tests/test_harness.py -q`
  - Result: pass, 50 tests.
- `python -m pytest tests/voice tests/test_openclaw_sync*.py -q`
  - Result on PowerShell: failed before collecting tests because pytest received the wildcard literally (`tests/test_openclaw_sync*.py`) and reported the path missing.
- `python -m pytest tests/voice tests/test_openclaw_sync_anvil_voice.py -q`
  - Result: pass, 414 passed, 2 skipped. This is the Windows-expanded equivalent of the packet test target.
- Broad loopback-alias scan across docs, examples, package code, and tests
  - Result: exit 0. The command finds pre-existing references and tests that reject the hostname alias; it does not indicate a new Anvil Voice example violation.
- Diff-only loopback-alias scan
  - Result: pass. No new diff lines introduce that hostname alias.
- `git diff --check`
  - Result: pass. Git printed Windows line-ending conversion warnings only.

## T007 live Fakoli Mini validation

Raw evidence artifacts are checked in beside this note:

- `docs/findings/2026-07-openclaw-anvil-voice-mini-validation.json`
- `docs/findings/2026-07-openclaw-anvil-voice-gateway-smoke.json`
- `docs/findings/2026-07-openclaw-anvil-voice-talk-catalog.json`
- `docs/findings/2026-07-openclaw-anvil-voice-talk-config.json`
- `docs/findings/2026-07-openclaw-anvil-voice-plugin-inspect.json`
- `docs/findings/2026-07-openclaw-anvil-voice-gateway-status.json`

Live host proof:

- Target host: Fakoli Mini, `Mac16,10`, 16 GB class.
- OpenClaw CLI/Gateway: `2026.6.11`; Gateway running as a LaunchAgent on `127.0.0.1:18789`.
- Anvil Voice STT: `127.0.0.1:30010`, model `mlx-community/parakeet-tdt-0.6b-v3`, ready.
- Anvil Voice TTS: `127.0.0.1:30011`, model `mlx-community/Kokoro-82M-bf16`, ready.
- Anvil Voice Realtime: `127.0.0.1:8765`, foreground process started with the router token in child env only.

OpenClaw installation proof:

- `openclaw plugins inspect anvil-voice --runtime --json` reported plugin id `anvil-voice`, status `loaded`, source `/Users/sdoumbouya/anvil/openclaw-anvil-voice-t007/index.ts`, and `realtimeVoiceProviderIds: ["anvil"]`.
- `openclaw gateway call talk.catalog --json` reported realtime providers `anvil`, `google`, and `openai`, with active realtime provider `anvil`.
- The `anvil` catalog row reported `label: "Anvil Voice"`, `configured: true`, `transports: ["gateway-relay"]`, `supportsBargeIn: true`, and `supportsToolCalls: false`.
- `openclaw gateway call talk.config --json` reported Talk realtime provider `anvil` and transport `gateway-relay`; secret-bearing fields were redacted by the CLI response.

Route and auth proof:

- Mini validation route probe called `http://100.87.34.66:8000/v1/route` with request model `fast-local`.
- Route response: tier `local`, model `qwen36-27b`, provider `fast-local`, work class `chat-fast`, confidence `0.9`.
- Missing-token negative probe returned HTTP `401` with `auth_enforced: true`.

Voice benchmark proof:

- Command path used the current CLI shape: `anvil-serving voice benchmark --config examples/voice/fakoli-mini.toml`.
- Benchmark result: `ttfa_ms: 2881.87`, `turn_latency_ms: 2882.2`, `stt_wer: 0.4`, `tts_rtf: 0.1666`.
- STT hypothesis: `Testing the local voice proof.`
- LLM reply: `I understand.`
- TTS result: first audio observed, `76800` output bytes, `1.6` seconds audio.

OpenClaw gateway-relay smoke proof:

- Scripted client connected to `ws://127.0.0.1:18789`, called `talk.session.create` with provider `anvil`, streamed synthesized PCM through `talk.session.appendAudio`, and closed with `talk.session.close`.
- Session result: provider `anvil`, transport `gateway-relay`, mode `realtime`, brain `agent-consult`, model `fast-local`.
- Relay events: `ready: 1`, `inputAudio: 13`, `transcript: 2`, `audio: 19`, `audioDone: 1`, `close: 1`.
- Final transcript: `Testing the local voice proof.`
- Output audio delivered to the OpenClaw playback relay: `76764` PCM bytes across `19` audio frames.
- Gateway smoke verdict: `pass`, with no relay errors or failure modes.

Harness bug found during live validation and fixed:

- Initial `harness sync openclaw --voice --restart` broke the Mini Gateway because it injected default intent-router plugin config keys into an installed OpenClaw plugin whose current schema rejects those keys.
- The Mini config was repaired by removing those injected optional keys and restarting Gateway.
- `anvil_serving/harness.py` now renders only the required intent-router hook by default and preserves operator-owned plugin config when it already exists.
- Regression check against a copy of the Mini config wrote no intent-router config keys and kept Talk set to provider `anvil`, transport `gateway-relay`.

Focused validation after the harness fix:

- `python -m pytest tests/test_harness.py tests/test_openclaw_sync_anvil_voice.py -q`
  - Result: pass, 51 tests.
- `python -m pytest tests/voice tests/test_openclaw_sync_anvil_voice.py -q`
  - Result: pass, 414 passed, 2 skipped.
- Temp Mini merge check:
  - Result: `configKeys: []`, Talk provider `anvil`, transport `gateway-relay`.

Pass/fail:

- PASS for the live speech-to-speech goal. OpenClaw can select the Anvil Voice realtime provider, create a gateway-relay Talk session, stream synthesized speech input through Anvil Voice, receive a final transcript, route the LLM turn to the fast local tier, and deliver response audio back through OpenClaw relay audio frames.
- Caveat at T007 time: the first `mini_validation.py` run reported `unsupported` because its generic bring-up probe looked for `./serves.toml` even when native Mini STT/TTS endpoints were already running and healthy. T008 reran the validator with externally managed serves and produced a `supported` verdict.

## T008 adversarial review and final validation

Three adversarial reviews were run before PR finalization:

- Huygens reviewed live validation and evidence. Finding: the first Mini rollup said pass while the raw Mini validator said `unsupported`, and process/log proof plus exact commands were thin. Disposition: fixed by rerunning `mini_validation.py` with externally managed Mini STT/TTS serves, adding `2026-07-openclaw-anvil-voice-realtime-process.json`, and refreshing the Gateway smoke.
- Einstein reviewed anvil-serving harness/MCP behavior. Findings: generated config cleanup preserved stale generated OpenClaw plugin defaults, private/tailnet realtime URLs were not tied to env SecretRefs, and MCP `openclaw_sync` did not expose voice fields. Disposition: fixed in `anvil_serving/harness.py`, `anvil_serving/mcp.py`, and focused tests.
- Plato reviewed OpenClaw provider behavior. Findings: Voice Call default audio format was wrong for Anvil, browser relay silence could hang on low-amplitude mic noise, catalog brain compatibility was wrong for realtime relay, transcripts could duplicate, realtime URL validation allowed credentials/query/fragment, and connect had no readiness timeout. Disposition: fixed in the Anvil Voice provider, Voice Call webhook, Talk catalog, and tests.

Final validation evidence:

- `python -m pytest tests/test_harness.py tests/test_openclaw_sync_anvil_voice.py tests/test_mcp.py -q`
  - Result: pass, 153 tests.
- `corepack pnpm test:extension anvil-voice`
  - Result: pass, 3 files, 19 tests.
- `node scripts/run-vitest.mjs run --config test/vitest/vitest.extension-voice-call.config.ts extensions/voice-call/src/webhook/realtime-handler.test.ts`
  - Result: pass, 1 file, 19 tests.
- `node scripts/run-vitest.mjs run --config test/vitest/vitest.gateway.config.ts src/gateway/server-methods/talk.test.ts src/gateway/talk-realtime-relay.test.ts`
  - Result: pass, 5 files, 152 tests.
- `node scripts/run-oxlint.mjs --tsconfig config/tsconfig/oxlint.extensions.json extensions/anvil-voice extensions/voice-call/src/webhook/realtime-handler.ts extensions/voice-call/src/webhook/realtime-handler.test.ts`
  - Result: pass.
- `corepack pnpm plugins:inventory:check`
  - Result: pass.
- `corepack pnpm docs:check-mdx docs/providers/anvil-voice.md docs/plugins/voice-call.md docs/nodes/talk.md docs/web/control-ui.md docs/plugins/reference/anvil-voice.md`
  - Result: pass.
- `node scripts/check-extension-package-tsc-boundary.mjs --mode=compile`
  - Result: pass.
- `node scripts/check-extension-plugin-sdk-boundary.mjs --mode=plugin-sdk-internal extensions/anvil-voice`
  - Result: pass.
- `node scripts/check-extension-plugin-sdk-boundary.mjs --mode=relative-outside-package extensions/anvil-voice`
  - Result: pass.
- `git diff --check`
  - Result: pass in both repositories.
- Mini live validation:
  - `python scripts/voice/mini_validation.py --config /tmp/anvil-voice-mini/fakoli-mini-external.toml --report /tmp/anvil-voice-mini/mini-validation-external.json`
  - Result: `supported`.
- Mini OpenClaw Gateway smoke:
  - `node /tmp/anvil-voice-mini/t008-gateway-smoke.js` against temporary loopback Gateway `ws://127.0.0.1:18790`
  - Result: pass; one final transcript, `76764` output audio bytes, `19` output audio chunks, no errors.

Known live-environment caveat: the Mini still runs installed OpenClaw `2026.6.11`, so live catalog output reports `brains: ["none"]` for Anvil until the OpenClaw source PR is merged and released. The live session path with `brain: "agent-consult"` passed, and the source catalog fix is covered by the updated Gateway tests.
