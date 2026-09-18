# Primary references and how to use them

Checked 2026-09-18. These links are reference material, not install commands or
permission to upgrade. Upstream `main` moves; T02 records the exact package/source
revision actually tested. The earlier inspection saw Pi Web 0.9.0 locally; verify
that again before implementation. No production version was changed here.

| ID | Primary reference | Use in this plan | Important limit |
|---|---|---|---|
| R1 | [Pi Web repository and README](https://github.com/agegr/pi-web) | T02/T05: session reuse, project files, worktrees and configuration capabilities; locate owning Node/UI code | Pi Web shares Pi's local configuration/session storage. A project view does not prove sandboxing or multi-user isolation. The managed installer stays pinned; do not copy `latest` commands into provisioning. |
| R2 | [Official Pi RPC contract](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md) | T02/T05/T07: request/response correlation, session events, model/thinking commands, extension UI and error responses | This is the engine protocol, not the Workbench HTTP API. Compare with `pi_rpc.py`, `pi_sessions.py` and the installed pin; a command documented upstream may still be intentionally unavailable in Workbench. |
| R3 | [MDN: CSP frame-src](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/frame-src) | T02: distinguish permission to load a child frame from permission to embed a page | `frame-src` controls loaded frames; `frame-ancestors` controls allowed parents. Missing frame-src falls back through child-src/default-src. Check child/parent/edge policy together. |
| R4 | [W3C APG: modal dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/) | T04: keyboard/focus acceptance for a modal mobile navigation drawer | Put focus inside the modal, contain Tab navigation, support Escape and restore focus. A pinned desktop rail is nonmodal and should not trap focus. |

## Reference hierarchy

1. Current project instructions and supported owner contracts.
2. Verified source/installed package for the selected deployment.
3. The source map in [REFERENCES.md](REFERENCES.md).
4. Primary upstream documentation, checked against the selected pin.
5. Examples in this packet, which are proposed implementation guidance.

When documentation and runtime differ, record the difference and use a bounded
compatibility test. Do not “fix” it by silently upgrading Pi, relaxing policy,
expanding thinking levels or replacing the user's selected provider.

## Tiny compatibility inventory for T02

Record these as measured values in the private spike receipt:

- Pi Web package version, source revision or integrity digest, Pi engine version,
  Node version and relevant license/patch provenance.
- Supported session listing/resume/branch and model/thinking commands.
- Actual HTTP, SSE/WebSocket, asset and service-worker paths.
- Parent, child and edge framing/authentication behavior.
- Extension methods that work, those explicitly unsupported, and their UI behavior.
- Whether host sessions are owner-only/unconfined and whether task runner binding
  is supported. Unproved capabilities remain unavailable.
