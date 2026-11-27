# Scribe

[CLI overview](../CLI.md) · [Product families](../PRODUCT-FAMILIES.md#anvil-scribe) · [ADR-0048](../adr/0048-anvil-scribe-record-first-dictation.md)

Anvil Scribe is record-first dictation. A browser or WebView client records
Ogg Opus audio; Scribe saves the raw transcript, then rewrites it only when the
caller asks, with a saved profile on an allowlisted target. Raw text and every
rewrite stay in a private history. Scribe consumes Anvil Voice speech-to-text
and the Capability Gateway's chat aliases through one route-bound device key;
it never calls a model serve directly.

## Command map

| Command | Purpose |
| --- | --- |
| `scribe validate` | Check `scribe.toml` and its key and login references offline; prints presence only. |
| `scribe run` | Run the loopback Scribe service in the foreground. |

Both read `$ANVIL_SERVING_HOME/scribe.toml` unless `--config PATH` is given;
the reference file is `examples/primary-node/scribe.toml`.

## Client API

| Request | Result |
| --- | --- |
| `POST /v1/memos` (multipart `audio`, `durationMs`, `droppedFrames`) | Stores `<id>.ogg` and `<id>.json`; returns `{id, createdAt, durationMs, transcript, outputs: []}`. |
| `POST /v1/memos/preview` (same input, a trailing window) | Returns `{transcript}`; stores nothing. |
| `POST /v1/memos/{id}/process` (`{profile, target?}`) | Rewrites the stored raw transcript and appends `{id, profile, profileLabel, target, model, text, createdAt, ms}` to `outputs`; 502/504 append nothing. |
| `GET /v1/memos?limit=&before=` | History, newest first, with a `next` cursor. |
| `GET` / `DELETE /v1/memos/{id}` | One memo with all outputs; delete removes its audio, transcript and outputs. |
| `GET /v1/memos/profiles` | Built-in (read-only) and custom profiles, prompts included. |
| `POST` / `PUT` / `DELETE /v1/memos/profiles[/{id}]` | Create, edit or delete custom profiles; built-ins answer 403. |
| `GET /v1/memos/targets` | Rewrite targets: `id`, `label`, `detail`, `default`; never the alias. |
| `OPTIONS` on any `/v1/memos` path | CORS preflight for an allowlisted `Origin`. |
| `GET /healthz` | Token-free liveness. |

Each recording is decoded to 16 kHz mono PCM16 with ffmpeg and transcribed in
segments through the gateway's `POST /v1/audio/transcriptions`, so the gateway
applies its admission, accounting and decision log. A failed transcription
keeps the audio and leaves a visible record whose transcript is `null`.

The listener admits at most 16 connections and 4 buffered uploads at once,
drops idle sockets after 30 seconds, and refuses a body that takes longer than
60 seconds to arrive. Two more limits bound decoding. `max_recording_seconds` (900 by default) refuses
longer recordings while decoding, and `max_concurrent_transcriptions` (2 by
default) caps decodes in flight. A preview that finds no free slot gets 503 at
once and retries on its next tick. A final upload waits up to 30 seconds, then
keeps the audio and answers 503 with the memo id.

Rewriting is a separate, optional step: the client picks a **profile** (a saved
prompt) and a **target** (an allowlisted gateway chat alias from
`[scribe.targets.<id>]`), and the service sends the stored transcript to
`POST /v1/chat/completions` with the same device key. Each result is appended
to the memo's `outputs`, so one memo can carry several rewrites beside its raw
text. A rewrite that fails returns 502, one that exceeds
`conversion_timeout_seconds` (45 by default) returns 504, and neither appends
anything. Eight built-in profiles (email, technical-doc, note, message, minutes,
tasks, clarify, summary) are read-only; custom profiles live in `profiles.json`
inside `data_dir`, so every device sees the same set. Preview requests never
rewrite.

## Access

The device key is the protected file written by `router keys create --out`
(`router_key_file`; a relative path sits beside `scribe.toml`), or an
environment reference (`router_key_env`). The login allowlist is an environment
reference resolved like every other one: the shell first, then
`$ANVIL_SERVING_HOME/.env`, then `~/.env`. The service refuses to start when
either is empty or when `listen_host` is not loopback. The store directory is
made private (0700) and must be owned by the service user, and every file in it
is written owner-only (0600). Only requests whose
`Tailscale-User-Login` header names an allowlisted login are accepted, so
publish the listener through `tailscale serve` and nothing else.

Issue a key bound to the STT route, the transcription path, and the chat path
plus every target alias:

```bash
anvil-serving router keys create --name scribe --model STT_ROUTE_ID --model llm.voice --model llm.primary --path /v1/audio/transcriptions --path /v1/chat/completions --rpm 120 --out ~/.anvil-serving/scribe.key
```

Then add the allowed logins to `~/.anvil-serving/.env`
(`ANVIL_SCRIBE_LOGINS=you@example.test`; `init` scaffolds the empty entry in
`.env.example`).

## Deploy

Build the `scribe` image target, which adds ffmpeg to the shared runtime, then
manage the container through the generic serve lifecycle:

```bash
docker build --target scribe -t anvil-serving-scribe:3.2.0 .
anvil-serving scribe validate
anvil-serving serves up scribe --manifest serves.scribe.toml --dry-run
anvil-serving serves up scribe --manifest serves.scribe.toml --confirm
```

## Privacy

The store carries an `EVALUATION-EXCLUDED.txt` marker. Recordings, transcripts,
outputs and saved prompts are user content, never evidence: `voice corpus
validate` refuses any case audio under a marked directory. Logs record method,
path, status, byte counts, latency and allow decisions, never audio,
transcripts, prompts, outputs or identity values.
