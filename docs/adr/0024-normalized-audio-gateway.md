# ADR-0024 — Normalized authenticated one-shot audio gateway

- **Status:** Accepted
- **Date:** 2026-07-22
- **Relates to:** [issue #280](https://github.com/fakoli/anvil-serving/issues/280), [ADR-0004](0004-router-as-a-service-containerized-and-authed.md), [ADR-0013](0013-openclaw-layers-and-mcp-control-plane.md), [voice pipeline](../VOICE.md)

## Context

The reference voice topology deliberately keeps Parakeet and Kokoro on Fakoli Dark. Their raw
wire contracts differ: STT accepts multipart files, while TTS returns raw PCM16 audio bytes. A
future Workbench HTTP surface or other private client that calls both directly would have to learn
host/port details and contain an adapter, which duplicates auth, limits, error handling, and privacy
decisions. The current Workbench Realtime relay remains a separate WebSocket protocol. The router
already owns the token-authenticated data-plane boundary but must not put one-shot audio through
chat quality routing or provider fallback.

## Considered options

1. Keep a Workbench-only adapter. It leaves raw topology in a client and duplicates a serving
   boundary for every future caller.
2. Treat audio as a chat tier or purpose model. That would make model selection, quality profile,
   and fallback semantics apply where they do not make sense.
3. Add a configured, normalized `/v1/audio/*` router seam. It preserves the existing raw serves
   and concentrates auth, bounds, format validation, and metadata-only observability.

## Decision

Adopt option 3. `[[router.audio_routes]]` declares private Dark STT/TTS routes. The existing
router token authenticates `POST /v1/audio/transcriptions` and `POST /v1/audio/speech`; callers
select only the endpoint-matching `purpose` or an opaque route id. They cannot provide an upstream
URL or model. The route is selected once and has no fallback.

The router converts JSON/base64 STT input to Parakeet multipart and converts live-qualified Kokoro
PCM16 bytes to a JSON/base64 response with an explicit configured sample rate. It enforces decoded
input, upstream output, text, concurrency, and wall-clock limits. The gateway requires a resolved
front-door bearer token, validates upstream media types, and records only route id, byte counts,
latency, outcome, and safe correlation ids. WAV and MP3 TTS output are deferred until separately
live-qualified rather than being inferred from mocks or relabelled.

## Consequences

The topology names an `anvil-audio-gateway` resource that shares the router listener; it is not a
second public listener. Raw audio serves remain loopback/private on Dark, and Mini remains
model-free. Configuration must be added deliberately to every promoted router-mode/rollback
recipe, so audio routing is preserved across a chat-tier switch.

Clients must migrate to the documented canonical JSON fields and `audio_b64` response. The
one-shot gateway does not replace the Realtime proxy's streaming audio protocol, URL, or token. A
live audio preflight remains an operator evidence gate before promotion; unit tests prove the
boundary but do not claim a running Dark service.
