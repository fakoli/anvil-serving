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

## The `memomind_glasses` tool

| Op | What happens | Microphone |
| --- | --- | --- |
| `NOTIFY` | A card on the HUD, `style` `notify` or `task`. | no |
| `TP` | `v=1` shows `content` as teleprompter pages; `v=0` closes it. | no |
| `LS` | Live captions on (`v=1`) or off (`v=0`). | after a press |
| `RC` | Record a memo on or off, with an optional Scribe profile as `mode`. | after a press |
| `MR` | Meeting minutes: `RC` with mode `minutes`. | after a press |
| `ASK` | Shows a question and waits up to `timeoutMs` (5–120 s) for the wearer's answer, `declined` or `timeout`. | after a press |
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
hermes mcp add memomind-glasses --url https://HOST.TAILNET.ts.net:PORT/mcp
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

The device key is either an environment reference (`router_key_env`) or the
protected file written by `router keys create --out` (`router_key_file`); each
backend credential is likewise `key_env` or `key_file`; the login allowlist is
an environment reference. The service refuses to start when any of them is
empty or when `listen_host` is not loopback. Only requests whose
`Tailscale-User-Login` header names an allowlisted login are accepted, and a
browser `Origin`, when sent, must match `allowed_origin_pattern`. Publish the
listener through `tailscale serve` and nothing else.

Issue a key bound to the STT route and the transcription path only:

```bash
anvil-serving router keys create --name agent --model STT_ROUTE_ID --path /v1/audio/transcriptions --rpm 120 --out ~/.config/anvil-serving/device-secrets/agent.key
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
