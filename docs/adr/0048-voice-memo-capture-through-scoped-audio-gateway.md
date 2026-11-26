# ADR-0048 — Voice memo capture through the scoped audio gateway

- **Status:** Accepted for implementation
- **Date:** 2026-11-25
- **Relates to:** [ADR-0002](0002-serves-are-compose-defined.md), [ADR-0019](0019-anvil-serving-owns-the-tailnet-edge.md), [ADR-0024](0024-normalized-audio-gateway.md), [ADR-0028](0028-serving-benchmarks-and-thin-capability-gateway.md), [ADR-0042](0042-anvil-serving-product-family-boundary.md), [voice CLI](../cli/voice.md#voice-memo)

## Context

A browser or WebView client on a wearable companion app records short voice memos as Ogg Opus,
sends them to the operator's own host, and shows the transcript on the device. A first prototype
was a standalone relay that called the STT serve directly, because device keys could not be
granted `/v1/audio/*` and the relay must not hold the router's operator token. Calling the serve
directly skipped the Capability Gateway's admission, accounting, and metadata-only decision trail,
and copied the STT route's upstream URL and model into a second place.

The client contract is small and already deployed:

- `POST /v1/memos` with multipart `audio` (Ogg Opus), `durationMs`, and `droppedFrames`;
  converts to 16 kHz mono WAV, transcribes in segments, stores the audio and transcript, and
  returns `{id, createdAt, durationMs, transcript}`.
- `POST /v1/memos/preview` with the same input; transcribes a short trailing window for live
  text and stores nothing.
- `OPTIONS` CORS preflight and `GET /healthz`.
- Identity is the `Tailscale-User-Login` header added by `tailscale serve`, checked against an
  allowlist that fails closed, plus a browser `Origin` allowlist.

Device vendors' terms can forbid using device audio or transcripts to train, fine-tune, or
evaluate models. Memos are user content, not evidence.

## Considered options

1. **Keep the standalone relay.** No product change, but audio bypasses the gateway's admission
   and audit, and a second copy of the route topology drifts when voice moves hosts.
2. **Add a memo endpoint to the router.** The gateway would decode audio, run ffmpeg, persist user
   content, and trust a tailnet identity header. That breaks the thin-gateway contract
   (ADR-0028): no storage, no content processing, bearer-token authentication only.
3. **An Anvil Voice memo service that is a client of the gateway.** `anvil-serving voice memo run`
   owns the capture contract (multipart parsing, Ogg-to-WAV conversion, segmentation, storage,
   client identity, CORS). It transcribes each segment through `POST /v1/audio/transcriptions`
   with a device key scoped to that endpoint and one audio route. The gateway gains only a
   least-privilege grant.

## Decision

Adopt option 3.

**Gateway grant.** Device keys may be granted `/v1/audio/transcriptions` and `/v1/audio/speech`
like any other inference path. For audio, a key's model grant names the audio route id the key
may select, because callers select a route or purpose and never a model. The front door resolves
the route the request would use and requires that id in the key's model grants before it acquires
the audio pool; tracked admission rechecks it atomically. A request that cannot resolve a route
is rejected by the gateway's existing validation before any upstream call. Audio audit rows now
record the real path instead of `[other]`. The operator token, configured scopes, and the
route-selection rules of ADR-0024 are unchanged.

**Memo service.** `anvil-serving voice memo run` reads `[voice.memo]` from the voice manifest.
It binds loopback only, because the identity header is trustworthy only when `tailscale serve` is
the sole path to the listener. The device key is an environment reference or the protected file
written by `router keys create --out`; the login allowlist is an environment reference. Both are
resolved at startup, and the service refuses to start when either is empty. Each memo is converted
with ffmpeg to 16 kHz mono PCM16 and split into segments that fit the gateway's decoded-input
limit, so long memos never exceed `audio_max_input_bytes`. Audio is written before
transcription, so an STT failure never loses the recording. Logs carry method, path, status,
byte count, latency, and allow decisions; never audio, transcript text, or identity values.

**Conversion modes.** Dictation is often more useful rewritten: a clean note, a ready-to-send
message, meeting minutes, a task list, or the same thought said more clearly. Each
`[voice.memo.modes.<id>]` declares a label, a chat alias, and instructions, all server-side, so
clients hold no model choice or credential. A memo upload may name one mode. After
transcription the service sends the raw transcript through the gateway's
`POST /v1/chat/completions` with the same device key, which therefore also needs the chat path
and a grant for each alias. It stores and returns both the raw `transcript` and the `converted`
text. A failed or slow conversion (bounded by `conversion_timeout_seconds`) returns the raw
transcript with `converted: null`. `GET /v1/memos/modes` lists ids and labels only. Preview
requests never run a mode, so live text stays fast. Conversion is ordinary chat traffic: the
decision log and usage accounting remain metadata-only, and the result is user content under the
same evaluation exclusion.

**Deployment.** The service is a Compose-defined container (ADR-0002) built from the repository
`Dockerfile` target `voice-memo`, which adds ffmpeg to the shared runtime. The container uses host
networking so the loopback bind, the gateway on host loopback, and `tailscale serve` all meet on
the host's 127.0.0.1. Lifecycle uses the generic `serves` verbs over a `[[serve]]` entry with
`gpu_inference = false`.

**Evaluation exclusion.** The memo store writes an `EVALUATION-EXCLUDED.txt` marker at its root.
STT corpus validation refuses any case audio under a directory that carries the marker, so memos
cannot be turned into benchmark or qualification inputs by accident. The gateway's decision log
and usage accounting stay metadata-only, as ADR-0024 requires.

## Consequences

- The client contract stays byte-compatible; the plugin needs no change when the operator points
  the same tailnet edge at the new service.
- The gateway again applies device-key rate limits, the audio concurrency pool, accounting, and
  the decision log to memo transcription. A voice host move changes only `[[router.audio_routes]]`.
- A route-bound key fails closed when the operator renames its route; reissue the key with the new
  route id.
- Segments use fixed cuts and can split a word at a boundary. Cut on silence if accuracy at seams
  matters.
- Conversion latency is the chat alias's latency. A reasoning-heavy alias can exceed the client's
  upload timeout; such a memo still returns its raw transcript, so pick a fast alias for modes.
- Preview transcription re-sends a trailing window every few seconds. It is not streaming STT; the
  Realtime proxy remains the streaming surface.
- Identity relies on loopback reachability. Any process on the host can reach the listener and
  present the header; host access is already operator trust.
- Real logins, tailnet names, route ids, and the cutover from the prototype belong in the private
  operator repository.
