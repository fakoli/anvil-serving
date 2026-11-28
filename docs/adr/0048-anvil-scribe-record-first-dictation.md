# ADR-0048 — Anvil Scribe: record-first dictation as a product family

- **Status:** Accepted for implementation
- **Date:** 2026-11-25 (family scope 2026-11-26)
- **Relates to:** [ADR-0002](0002-serves-are-compose-defined.md), [ADR-0019](0019-anvil-serving-owns-the-tailnet-edge.md), [ADR-0024](0024-normalized-audio-gateway.md), [ADR-0028](0028-serving-benchmarks-and-thin-capability-gateway.md), [ADR-0042](0042-anvil-serving-product-family-boundary.md), [Scribe commands](../cli/scribe.md)

## Context

A browser or WebView client on a wearable companion app records dictation as Ogg Opus. The user
wants three things. First, the exact words are kept. Second, the same recording can later be
turned into an email, a task list, minutes or a cleaner note, on a model they choose. Third,
everything stays on their own hosts. A first prototype was a standalone relay that called the STT
serve directly, because device keys could not be granted `/v1/audio/*` and the relay must not
hold the router's operator token. That skipped the Capability Gateway's admission, accounting and
metadata-only decision trail, and copied the STT route's upstream URL into a second place.

The capability is not a voice-serving concern. Anvil Voice operates STT, TTS and realtime paths.
Dictation capture, a private memo history and saved rewrite profiles are a user-facing product
that consumes those paths and the gateway's chat aliases.

Device vendors' terms can forbid using device audio or transcripts to train, fine-tune or evaluate
models. Memos are user content, not evidence.

## Considered options

1. **Keep the standalone relay.** No product change, but audio bypasses the gateway's admission
   and audit, and a second copy of the route topology drifts when voice moves hosts.
2. **Add memo endpoints to the router.** The gateway would decode audio, run ffmpeg, persist user
   content and trust a tailnet identity header. That breaks the thin-gateway contract (ADR-0028):
   no storage, no content processing, bearer-token authentication only.
3. **A feature inside Anvil Voice.** It works mechanically, but it puts a user product with its own
   store, history and editable prompts inside the audio-operations family, and blurs Voice's
   authority (serve lifecycle and qualification).
4. **A separate Anvil Scribe family that is a client of the gateway.** `anvil-serving scribe run`
   owns the capture API, the memo store, rewrite profiles and the target allowlist. It transcribes
   and rewrites only through the gateway with a least-privilege device key.

## Decision

Adopt option 4. This extends ADR-0042's umbrella with an eighth family, **Anvil Scribe**
(`anvil-scribe`, root command `scribe`), with its own journey and docs. It shares the package, CLI,
release line and safety contract like every other family.

**Gateway grant (Capability Gateway).** Device keys may be granted `/v1/audio/transcriptions` and
`/v1/audio/speech` like any other inference path. For audio, a key's model grant names the audio
route id the key may select, because callers select a route or purpose and never a model. The
front door resolves the route the request would use and requires that id in the key's model
grants before it acquires the audio pool; tracked admission rechecks it atomically. For device
keys, a request that cannot resolve a route is also refused with `403 key_access_denied` before
any upstream call so 403-versus-404 responses do not allow route-id enumeration (the operator
token still receives the gateway's existing validation error).
Audio audit rows record the real path instead of `[other]`. The operator token, configured scopes
and ADR-0024 route selection are unchanged.

**Capture.** `scribe run` reads `$ANVIL_SERVING_HOME/scribe.toml` (`[scribe]`). It binds loopback
only, because the identity header is trustworthy only when `tailscale serve` is the sole path to
the listener. The device key is an environment reference or the protected file written by
`router keys create --out`, and the login allowlist is an environment reference. Both are resolved
at startup, and the service refuses to start when either is empty; `scribe validate` checks the
same references offline. Each recording is converted with ffmpeg to 16 kHz mono PCM16 and split
into segments that fit the gateway's decoded-input limit, and its audio and record are written
before transcription, so an STT failure never loses the recording. Logs carry method, path,
status, byte count, latency and allow decisions; never audio, transcripts, prompts, outputs or
identity values.

**Record first, rewrite later.** Recording always stores the raw transcript. Rewriting is a
separate, optional request: `POST /v1/memos/{id}/process` with a **profile** (a saved prompt) and
an optional **target** (an allowlisted chat alias declared in `[scribe.targets.<id>]`, one of them
the default). The service sends the stored transcript through the gateway's
`POST /v1/chat/completions` with the same device key, which therefore also needs the chat path and
a grant for every target alias. Each result is appended to the memo's `outputs`, so raw text and
any number of rewrites coexist; a failed (502) or slow (504, `conversion_timeout_seconds`) rewrite
appends nothing. Eight built-in profiles ship read-only in the product, with prompts verified
against a small non-thinking instruct model. Custom profiles are created, edited and deleted over
`/v1/memos/profiles` and kept in `profiles.json` inside the memo store, so every device sees the
same set, and they inherit its evaluation exclusion. `GET /v1/memos/targets` exposes ids, labels
and the default, never the alias. History is `GET /v1/memos` (newest first, cursor paged) plus
`GET` and `DELETE /v1/memos/{id}`. Store mutations share one lock and replace records atomically.
Rewriting is ordinary chat traffic: the decision log and usage accounting remain metadata-only.

**Deployment.** Scribe is a Compose-defined container (ADR-0002) built from the repository
`Dockerfile` target `scribe`, which adds ffmpeg to the shared runtime. The container uses host
networking so the loopback bind, the gateway on host loopback and `tailscale serve` all meet on
the host's 127.0.0.1. Lifecycle uses the generic `serves` verbs over the `scribe` entry in
`serves.scribe.toml` (`gpu_inference = false`). `anvil-serving init` scaffolds `scribe.toml`,
`serves.scribe.toml` and `docker-compose.scribe.yml`.

**Evaluation exclusion.** The memo store writes an `EVALUATION-EXCLUDED.txt` marker at its root.
STT corpus validation refuses any case audio under a directory that carries the marker, so memos
cannot be turned into benchmark or qualification inputs by accident.

## Consequences

- The client contract is byte-compatible with the prototype for capture and preview, and adds
  history, profiles, targets and processing under the same `/v1/memos` prefix.
- The gateway again applies device-key rate limits, the audio concurrency pool, accounting and the
  decision log to Scribe traffic. A voice host move changes only `[[router.audio_routes]]`.
- A route-bound key fails closed when the operator renames its route or a target alias; reissue the
  key with the new grants.
- Rewrite latency is the target alias's latency. A reasoning-heavy alias can exceed the bound; the
  raw transcript is already stored, so the default target should be a fast instruct model.
- Segments use fixed cuts and can split a word at a boundary. Cut on silence if accuracy at seams
  matters.
- Preview transcription re-sends a trailing window every few seconds. It is not streaming STT; the
  Realtime proxy remains the streaming surface.
- Memo ids carry second resolution; two memos saved in the same second order arbitrarily.
- Identity relies on loopback reachability. Any process on the host can reach the listener and
  present the header; host access is already operator trust.
- Real logins, tailnet names, route ids and the cutover from the prototype belong in the private
  operator repository.
