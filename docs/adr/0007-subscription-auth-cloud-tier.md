# ADR-0007: Subscription-auth cloud tier — feasible, opt-in only, text-only, no tool broker

- **Status:** Accepted
- **Date:** 2026-07-02
- **Extends:** [ADR-0001](0001-cloud-cost-and-subscription-auth.md) (answers its open
  "subscription auth" question; ADR-0001's core decision — no cloud API key in the default
  path — stands unchanged)

## Context

ADR-0001 chose advise-and-defer (local-only default, opt-in **metered** cloud tier) and left one
question open: could a cloud tier run on the operator's **Claude subscription** (Pro/Max) instead
of a metered `ANTHROPIC_API_KEY`, so overflow traffic costs no marginal API spend? The project's
own golden rule (CLAUDE.md) already mandates the Claude Agent SDK for any code that calls Claude.

Researched 2026-07-02 against official sources (docs.claude.com authentication / headless /
legal-and-compliance pages; `claude-agent-sdk` on PyPI). Findings:

1. **Headless subscription auth is real and documented.** `claude setup-token` mints a one-year
   OAuth token (`CLAUDE_CODE_OAUTH_TOKEN`), explicitly supported for "CI pipelines, scripts, or
   other environments where interactive browser login isn't available", scoped to inference,
   requiring a Pro/Max/Team/Enterprise plan. Two sharp edges: `ANTHROPIC_API_KEY` **outranks**
   the OAuth token in the credential chain (a subscription backend must scrub it from the
   subprocess environment or it silently bills metered API), and `--bare` mode skips OAuth
   entirely (must not be used).
2. **Anthropic's documented position:** "Developers building products or services that interact
   with Claude's capabilities, including those using the Agent SDK, **should use API key
   authentication**… Anthropic does not permit third-party developers to offer Claude.ai login or
   to route requests through Free, Pro, or Max plan credentials **on behalf of their users**."
   The bright-line prohibition targets multi-tenant relaying. A self-hosted operator relaying
   **their own** harness traffic through **their own** token does not hit that line, but a shipped
   product bundling this as a default would sit against the "should use API key" guidance.
3. **Technical shape:** a backend implementing the existing `Backend` protocol can subprocess the
   `claude` CLI (`-p --output-format stream-json`) — stdlib-only (`subprocess` + `json`), no new
   Python dependency; the CLI is one the target operator already has. The Python
   `claude-agent-sdk` package is a real third-party dependency (async, MCP machinery) that spawns
   the same CLI anyway, so it buys nothing the text-only scope needs.
4. **The tool-use broker is structurally fragile.** The harness sends its own tool definitions
   and expects `tool_use`/`tool_calls` back across **stateless HTTP requests**; holding a live
   SDK session between a tool call and its result (two separate harness requests) has no sound
   state story, and history injection is not supported. Tool-carrying turns cannot round-trip
   faithfully through this tier.
5. **What a harness loses:** no `temperature`/`top_p`/stop-sequence control (the SDK/CLI does not
   expose them), 1–3 s CLI spawn latency per request (unfit for `quick-edit`; tolerable for
   `planning`), and the traffic draws from the **same** Pro/Max usage caps as the operator's
   interactive Claude Code sessions.

## Decision

A subscription-auth cloud tier is **feasible and permitted for self-hosted single-operator use,
with constraints** — and those constraints are binding on any future implementation:

1. **Opt-in and OFF by default**, exactly like the metered cloud tier (ADR-0001's billing gate
   applies unchanged — this is a usage-cap and ToS decision instead of a dollar decision, but it
   is still the operator's decision). Never bundled, never auto-configured; the operator mints
   their own token (`claude setup-token`) and wires it via an env-var name in config.
2. **Implementation route: subprocess to the `claude` CLI**, not the Python SDK — preserves the
   stdlib-only hot path. The backend must scrub `ANTHROPIC_API_KEY` from the subprocess
   environment and must not use `--bare`.
3. **Text-only work classes** (`planning` / `review` / `chat`), enforced as a policy hard
   constraint. **No tool-use broker** — tool-carrying turns stay on advise-and-defer or the
   metered `CloudBackend`.
4. **Documented as ToS-gray** wherever it is configured, quoting the legal page's "should use API
   key authentication" guidance and noting enforcement may occur "without prior notice".
5. This tier **does not displace advise-and-defer** as the default posture; it is a third
   residency option (`local` → `cloud-subscription` → `cloud-metered`), not a new default.

No implementation is scheduled by this ADR; it records the answer and the constraints so a future
task starts from settled ground.

## Consequences

- ADR-0001's open question is closed: the mechanism exists, is documented and headless-capable,
  and the constraint set above is what makes it acceptable.
- A future `SubscriptionBackend` slots behind the existing `Backend` protocol (same seam as
  `CloudBackend`) with a fail-fast startup gate (CLI on PATH + token present), mirroring the
  `auth_env` pattern.
- Operators accept: shared usage caps with their interactive sessions, reduced sampling-parameter
  fidelity (tier `extra_body` cannot fix what the CLI does not expose), and higher first-token
  latency than a raw-API tier.
- If Anthropic's documented position on subscription-authenticated programmatic use materially
  changes, this ADR must be revisited (supersede, don't edit).

## Related

- The companion research also assessed **pi** (Mario Zechner's coding agent) as an integration
  target: config-only fit via `~/.pi/agent/models.json` (custom providers, free-form model ids,
  both wire dialects) — recorded as a README recipe, no ADR needed (no design decision involved).
