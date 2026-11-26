# Agent

[CLI overview](../CLI.md) · [Product families](../PRODUCT-FAMILIES.md#anvil-agent) · [ADR-0049](../adr/0049-anvil-agent-glasses-bridge.md)

Anvil Agent connects a smart-glasses client to your own agents. The wearer
talks or types to an agent and reads its reply on the HUD. The agents drive the
glasses back through one MCP tool, `memomind_glasses`: they can show a card,
run a teleprompter, ask for live captions or a memo, or ask the wearer a
question and wait for the answer. Spoken turns are transcribed through the
Capability Gateway with a route-bound device key. Agent credentials stay in the
service, and it stores no turns.

## Command map

| Command | Purpose |
| --- | --- |
| `agent validate` | Check `agent.toml` and its key, login and backend references offline; prints presence only. |
| `agent run` | Run the loopback Anvil Agent service in the foreground. |

Both read `$ANVIL_SERVING_HOME/agent.toml` unless `--config PATH` is given;
the reference file is `examples/primary-node/agent.toml`.

## Client API

| Request | Result |
| --- | --- |
| `GET /v1/agents` | `{agents: [{id, label}]}` for the declared backends, in declaration order. |
| `POST /v1/agent/turn` (multipart `agent`, plus `text` or `audio`) | `{agent, transcript, reply, sessionId, status}`. Without `agent`, the first backend answers. |
| `POST /v1/hermes/turn` | The v0.1 alias for `agent=hermes`. |
| `POST /v1/glasses/notify` (`{body, title?, kind: notify\|task}`) | Queues a NOTIFY card; returns `{id}`. |
| `GET /v1/glasses/events?after=&wait=` | Long-poll (`wait` capped at 25 s) of `{events: [{id, op, ...}]}`; expired control ops are left out. |
| `POST /v1/glasses/answer` (multipart `askId`, plus `text`, `audio` or `declined=1`) | Settles a waiting ASK; returns `{status, transcript}`, or 404 once it has expired. |
| `POST /mcp` | MCP JSON-RPC (`initialize`, `ping`, `tools/list`, `tools/call`); notifications get 202, `GET` gets 405. |
| `OPTIONS` on the client paths | CORS preflight for an allowlisted `Origin`. |
| `GET /healthz` | Token-free liveness. |

A turn that cannot be transcribed returns 502, and silence returns 422. A
backend that answers with an error status returns 502 naming only that status;
its body is never forwarded or logged. A backend that does not finish within
`turn_timeout_seconds` returns 504 with `unconfirmed: true`, because the agent
may still finish in its own session.

These limits bound memory and threads:

- As in Scribe, `max_recording_seconds` (300 by default) refuses longer
  recordings while decoding. `max_concurrent_transcriptions` (2 by default)
  caps decodes in flight, and a spoken turn that finds no free slot within 30
  seconds returns 503.
- At most eight request bodies are held at once, because an agent turn or `ASK`
  keeps its body until it finishes. A ninth concurrent `POST` gets 503 before
  its body is read. Answers to `ASK` have two slots of their own, so waiting
  questions cannot lock out their answers.
- A body must arrive within 60 seconds, and a socket that goes quiet for 30
  seconds is dropped. A form with more than 16 parts, or with a part that is
  itself `multipart/*` or `message/*`, gets 400 before it is parsed.
- `/mcp` and notify bodies are JSON of at most 64 KiB; only turns and answers
  carry audio up to `max_upload_bytes`. An MCP batch of more than 16 messages
  is refused whole with one `-32600` error.
- As in Scribe, the server holds at most 16 connections and drops further ones.

## The `memomind_glasses` tool

| Op | What happens | Microphone |
| --- | --- | --- |
| `NOTIFY` | A card on the HUD, `style` `notify` or `task`. | no |
| `TP` | `v=1` shows `content` as teleprompter pages; `v=0` closes it. | no |
| `LS` | Live captions on (`v=1`) or off (`v=0`). | after a press |
| `RC` | Record a memo on or off, with an optional Scribe profile as `mode`. | after a press |
| `MR` | Meeting minutes: `RC` with mode `minutes`. | after a press |
| `ASK` | Shows a question and waits up to `timeoutMs` (5–120 s) for the wearer's answer, `declined` or `timeout`. One question waits at a time; another gets `busy`. | after a press |
| `STATUS` | Reports whether the glasses app is open. | no |

Microphone ops only show a request card: nothing records until the wearer
presses the glasses button, and stop requests apply at once. Control ops expire
60 s after they are queued, so a command sent while the phone was locked cannot
start anything later. NOTIFY cards stay in a ring of the last 50 events. Every
result says whether the glasses app is open; `ASK` returns `not_open` at once
instead of waiting for nobody. The queue lives in memory, and a restart drops
it.

Register the tool with an MCP-capable agent by its URL. A Hermes example:

```bash
hermes mcp add memomind-glasses --url https://HOST.example.ts.net:PORT/mcp
```

## Backends

Each `[agent.backends.<id>]` declares one agent. The id is what clients send as
`agent`, and the backend whose id is `hermes` also answers `/v1/hermes/turn`.

| `kind` | Turn API | `session` |
| --- | --- | --- |
| `hermes-api` | The Hermes API server's `POST /v1/chat/completions`, with `X-Hermes-Session-Id`; no model override, so the profile's own model answers. | The API-server session that keeps the glasses history (default `anvil-agent`). |
| `pi-web` | pi-web's mobile bridge: prompt the session, wait until it stops streaming, then read the newest assistant message. | Required: an existing pi-web session id. The bridge cannot create sessions. |

The HUD hint (at most two short plain sentences) is sent as a system message to
Hermes and prefixed to the text for Pi, which has no system channel. Backend
requests use no proxy discovery and follow no redirects, so a credential
reaches only the declared URL.

## Access

The device key is either the protected file written by `router keys create
--out` (`router_key_file`) or an environment reference (`router_key_env`). The
reference config uses `agent.key` beside `agent.toml`: a relative path is read
beside the config, and the Compose file mounts that key next to it. Each
backend credential is likewise `key_file` or `key_env`, and the login allowlist
is an environment reference. As in Scribe, environment references read the
process environment and then the operator `.env`, so `agent validate` on the
host sees the same values as Compose. The service refuses to start when any of
them is empty, or when `listen_host` is not an IPv4 loopback address. Only requests whose
`Tailscale-User-Login` header names an allowlisted login are accepted, and a
browser `Origin`, when sent, must match `allowed_origin_pattern`. Publish the
listener through `tailscale serve` and nothing else.

A page on an allowed origin could send a cross-origin `POST` that skips the
CORS preflight. So `/mcp` refuses any request that carries an `Origin` (MCP
clients are agents, not browsers), and notify from a browser must be
`application/json`, which always preflights. CORS headers are sent only on the
client paths.

One service serves one person. Every allowlisted login shares the event queue,
the waiting `ASK` and each backend's session, so list only your own logins.
Run a second service, with its own port, sessions and tailnet listener, for anyone
else.

Issue a key bound to the STT route and the transcription path only:

```bash
anvil-serving router keys create --name agent --model STT_ROUTE_ID --path /v1/audio/transcriptions --rpm 120 --out ~/.anvil-serving/agent.key
```

## Deploy

Build the `agent` image target, which shares the Scribe stage's ffmpeg layer,
then manage the container through the generic serve lifecycle:

```bash
docker build --target agent -t anvil-serving-agent:3.1.0 .
anvil-serving agent validate
anvil-serving serves up agent --manifest serves.agent.toml --dry-run
anvil-serving serves up agent --manifest serves.agent.toml --confirm
```

Anvil Agent and Anvil Scribe can share one tailnet origin: mount `/v1/memos` on
Scribe and the paths above on Anvil Agent.

## Privacy

The service stores no turns, answers, captions or questions. Logs record
method, path, status, byte counts, latency and allow decisions, never audio,
transcripts, replies or identity values. Glasses audio and transcripts are user
content, never evidence: do not use them to train, fine-tune or evaluate a
model.
