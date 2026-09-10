# Pi workspace reuse decision

September 10, 2026 · Source research and design proposal, not an installed integration.

The requested experience is a conversation with Pi as the coding harness, with
model/thinking controls, session navigation, tool activity and extension menus.
A list of individual test commands does not satisfy that experience.

## Recommendation

Test **Pi Web as a separately owned session UI/service**, presented from the
Workbench. Keep its chat, session store, RPC translation and extension UI in one
place. Add only authorized project context, navigation, runner lifecycle and
Anvil task/evidence correlation at the Workbench boundary. Avoid a permanent
Open WebUI fork while this more directly matched reuse candidate is untested.

This is an integration-spike recommendation. The current prototype imitates the
proposed arrangement with small local fixtures; it imports no Pi Web code.

## What the sources establish

| Candidate | Documented fit | Work still required |
| --- | --- | --- |
| Official Pi RPC / SDK | Prompts, abort, model and thinking selection, persisted sessions, streamed messages/tool events and extension UI requests | Browser UI, session transport, authentication integration and environment ownership |
| Third-party Pi Web | Conversational sessions, resume/branch/export, model/thinking controls, files/Git/worktrees, extension dialogs/widgets/status | Pin and test compatibility; prove embedding and identity boundaries; add Anvil correlation and isolated runner policy |
| Terminal / PTY window | Existing terminal presentation | Structured browser dialogs, session controls and event correlation are still needed; terminal pixels alone do not provide them |
| Open WebUI fork | Rich chat and configuration | Pi-specific controls and runner still need integration, alongside Anvil workflows and fork maintenance |

Official Pi exposes a TUI library, SDK and RPC protocol. The official
[repository](https://github.com/earendil-works/pi),
[SDK reference](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md)
and [RPC reference](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md)
are the protocol authorities. The RPC host renders and responds to extension
requests such as select, confirm, input and editor. Text widgets/status translate
to web controls; terminal-specific custom components may degrade or require an
explicit adapter. The official
[RPC extension example](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/examples/rpc-extension-ui.ts)
is a terminal example, not a browser application to embed.

[Pi Web](https://github.com/agegr/pi-web) is a third-party MIT-licensed project,
not an official Pi browser package. Its inspected
[manifest](https://github.com/agegr/pi-web/blob/main/package.json) required Node
22.19 or later and selected Pi 0.85.1. Its architecture includes a local
Node/Next service, session manager and browser UI. Its PTY/xterm dependencies
support optional terminal tabs; core Pi chat uses the session integration.
These are source observations, not locally verified runtime compatibility.
Upstream main branches move: record exact immutable revisions before the spike.

## Smallest useful spike

1. Pin Pi Web and Pi in an isolated development environment. Run against a fake
   or explicitly authorized model connector; no production recipe lifecycle is
   necessary to evaluate the UI seam.
2. Open one session from a Workbench project/task context. Prove whether a
   same-origin routed panel is supported, including asset/API/WebSocket paths,
   CSP/frame policy and reconnect. A context-preserving link is the fallback
   if embedding needs extensive modifications. Do not assume iframe support.
3. Send, stream, steer and abort a turn. Change the next-turn model/thinking
   setting. Resume and branch a session without overwriting either history.
4. Round-trip select/confirm/input/editor requests and text widget/status updates.
   Inventory unsupported installed extensions explicitly; never silently treat a
   missing confirmation UI as approval.
5. Exercise identity expiry/revocation, scoped session ownership, cursor recovery,
   cancellation and resource cleanup through fake State/Connect adapters first.
   Browsers must not receive provider credentials or unscoped runner authority.
6. Compare integration changes and dependency footprint with a thin official-RPC
   adapter. Choose reuse if the documented features work without broad internal
   patches or duplicate session state; otherwise bound the custom adapter to the
   controls actually required by the first workflow.

Pi Web supplies UI, not sandbox isolation. A runner still owns its process,
worktree, credentials, tools and resource limits. Workbench should not transplant
Pi Web's optional terminal service into the Serving Python runtime. The existing
[runner contract](RUNNER-CONTRACT.md) remains the lifecycle design.

## Agent and evaluation separation

The Pi session model is the operator's explicit cloud connection in the concept.
The compute/model selector identifies the local evaluation target. Changing one
does not change the other. A cloud agent can help prepare a plan, but the fixed
evaluation runner executes the steps and captures independent results without
spending agent tokens on routine orchestration. Test output and correctness gates
remain independent of claims made by the evaluated model or the driving agent.

No installation, fork, live Pi call, container launch or authentication smoke was
performed in this design revision.
