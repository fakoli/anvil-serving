# ADR-0049 — Anvil Agent: a glasses bridge to the operator's own agents

- **Status:** Accepted for implementation
- **Date:** 2026-11-26
- **Relates to:** [ADR-0002](0002-serves-are-compose-defined.md), [ADR-0019](0019-anvil-serving-owns-the-tailnet-edge.md), [ADR-0028](0028-serving-benchmarks-and-thin-capability-gateway.md), [ADR-0042](0042-anvil-serving-product-family-boundary.md), [ADR-0048](0048-anvil-scribe-record-first-dictation.md), [Agent commands](../cli/agent.md)

## Context

A smart-glasses companion app (a WebView plugin with a small HUD and one
button) lets the wearer talk to the operator's own agents, such as a Hermes
gateway or a Pi session, and lets those agents put something on the HUD. A
prototype relay proved the flow end to end. It accepts a spoken or typed turn,
asks the agent, and returns a reply in about a second. It also runs a bounded
event queue that the glasses long-poll, and a minimal MCP server whose one tool
lets an agent queue cards, a teleprompter, captions, memo requests and
questions. The prototype had three problems. It called the STT serve directly,
because device keys could not be granted `/v1/audio/*`. It lived outside any
repository. It shared one listener with the memo endpoints that Anvil Scribe
now owns.

Three properties have to survive any move:

- The shipped client hardcodes its paths and JSON shapes.
- Agent credentials must never reach the client.
- An agent must not be able to open the wearer's microphone on its own.

Device vendors' terms can also forbid using device audio or transcripts to
train, fine-tune or evaluate models.

## Considered options

1. **Keep the prototype relay.** No product change. Audio still bypasses the gateway's admission and audit, and the service stays unowned.
2. **Fold it into Anvil Scribe.** Both serve the same glasses client from one origin. But Scribe is a memo store and rewrite product, while this is a stateless conversation bridge that holds third-party agent credentials. Merging them widens Scribe's trust boundary and couples two release cadences behind one container.
3. **Add it to the router.** The gateway would hold agent credentials, trust a tailnet identity header and keep session state. That breaks the thin-gateway contract (ADR-0028).
4. **A separate Anvil Agent family.** `anvil-serving agent run` owns the turn API, the glasses event queue and the MCP tool. It transcribes only through the gateway with a least-privilege key.

## Decision

Adopt option 4. This extends ADR-0042's umbrella with a ninth family, **Anvil
Agent** (`anvil-agent`, root command `agent`). It shares the package, CLI,
release line and safety contract like every other family.

**Turns.** `POST /v1/agent/turn` takes typed `text` or Ogg Opus `audio`. Audio
is decoded and transcribed exactly as Scribe does (ADR-0048): ffmpeg to 16 kHz
mono PCM16, segments sized for the gateway's decoded-input limit, and the
gateway's `/v1/audio/transcriptions` with a device key whose only grants are
the STT route and that path.

The selected backend answers, and the service returns `{agent, transcript,
reply, sessionId, status}`. Backends are declared in
`[agent.backends.<id>]`, and their order is the client's picker order. A
`hermes-api` backend posts to the Hermes API server's chat endpoint inside a
named API-server session. A `pi-web` backend prompts an existing pi-web session
through its mobile bridge and waits for the session to stop streaming.

Each backend credential is an environment reference or a protected file,
resolved at startup. A declared backend with no credential fails closed rather
than being hidden. Backend requests use no proxy discovery and follow no
redirects. An error status is reported by number only, and a timeout returns
504 `unconfirmed`, because the agent may still finish.

**Glasses.** `GET /v1/glasses/events` is a long-poll over an in-memory ring of
the last 50 events, with millisecond ids that stay monotonic across restarts.
NOTIFY cards persist. Control ops expire after 60 s, so nothing queued while
the phone slept fires later. A poll within the last 35 s counts as "glasses app
open".

`POST /v1/glasses/notify` keeps the v0.1 card shape. An ASK blocks its tool
call until `POST /v1/glasses/answer` settles it, the wearer declines, or the
timeout passes. A settled ASK is withdrawn from the queue.

**MCP tool.** `POST /mcp` is a minimal streamable-HTTP MCP server with JSON
responses only and no server-initiated stream. Its one tool, `memomind_glasses`,
uses op names that mirror the vendor's own agent-control vocabulary (NOTIFY,
TP, LS, RC, MR, ASK, STATUS), limited to what a plugin can do. Mirroring the
names means a future vendor hook maps one to one. Every result says honestly
whether the glasses app is open.

**Consent.** LS, RC, MR and ASK only queue a request card. The microphone opens
only after the wearer presses the glasses button, and a stop request applies
at once.

**Trust.** The listener binds loopback only. Requests are accepted only when the
`Tailscale-User-Login` header names an allowlisted login and any browser
`Origin` matches the configured pattern, because `tailscale serve` is the only
remote path in. Agents on the same tailnet reach `/mcp` through the same edge
under the owner's identity, so the tool needs no token of its own.

**State and privacy.** No turn, transcript, reply, question or answer is
stored. Logs carry method, path, status, sizes, latency and allow decisions
only.

**Deployment.** Anvil Agent is a Compose-defined container (ADR-0002) built
from the repository `Dockerfile` target `agent`, which reuses the Scribe stage's
ffmpeg layer and has no data volume. Host networking lets the loopback bind,
the gateway, the agent backends and `tailscale serve` all meet on 127.0.0.1.
Lifecycle uses the generic `serves` verbs over the `agent` entry in
`serves.agent.toml` (`engine = "none"`). `anvil-serving init` scaffolds
`agent.toml`, `serves.agent.toml` and `docker-compose.agent.yml`.

Scribe and Anvil Agent share one client origin through a path split at the
edge: `/v1/memos` goes to Scribe and the paths above go to Anvil Agent.

## Consequences

- The client contract is byte-compatible with the prototype for every agent and glasses path. The memo paths now answer 404 here, because they belong to Scribe.
- Spoken turns regain the gateway's rate limits, audio concurrency pool, accounting and decision log. A voice host move changes only `[[router.audio_routes]]`.
- The event queue and pending questions live in one process. A restart drops queued cards, and the service cannot run as more than one replica.
- A pi-web bridge that accepts only one bearer token forces Anvil Agent to share that token with every other bridge client. Declare a `pi-web` backend only when that is acceptable.
- A glasses turn cannot answer an agent's own approval prompt, so approval-gated agent actions are denied.
- Identity relies on loopback reachability. Any process on the host can reach the listener and present the header; host access is already operator trust.
- Real logins, tailnet names, session ids, backend URLs and the cutover from the prototype belong in the private operator repository. The glasses plugin's source lives outside this repository.
