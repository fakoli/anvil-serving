# No node agent on the voice host, and the router advertises unbacked voice routes

**Status:** Open — blocks ADR-0034 step 3; live routing gap on the reference topology

> **2026-08-09 update — voice relocated (see ADR-0036 draft).** The voice
> capability (STT/TTS + the low-latency voice LLM) moved off this host to
> `ai-mbp25` by operator action, so the framing below is partially superseded:
>
> - **Gap 2 is now a repoint, not a bring-up.** The serving host's router
>   advertises `llm.voice` and both audio routes at endpoints on a host that
>   will not serve voice again. The fix is repointing (or parking) those routes
>   to the new owner's declared endpoints, confirm-gated per ADR-0035 — not
>   restarting serves here.
> - **Gap 1 retargets to two hosts.** The node agent is required on `ai-mbp25`
>   (the voice owner, catalog scoped to voice + benchmark operations) and on
>   this host under its new qualification/ComfyUI role (catalog scoped to
>   serve-lifecycle + benchmark operations). An empty host remains the ideal
>   first deployment target per ADR-0034 sequencing.
> - **Verification impact resolves differently.** `runtime = "docker"`
>   live-verification on this host can now be completed with a qualification
>   or ComfyUI serve instead of the departed voice stack.
>
> The underlying finding stands unchanged: nothing reports the divergence
> between declared routes and what any host actually serves, and a host that
> returns empty is indistinguishable from one that is down.

## Problem

Two related gaps, observed together on 2026-08-08.

**1. No node agent on the voice host.** The voice host is reachable on the
tailnet (ICMP-equivalent ping answers in ~1 ms) but every service port is
closed, *including the controller port 8765*:

```text
30110 (stt)  closed/filtered
30111 (tts)  closed/filtered
30113 (llm)  closed/filtered
8765  (controller) closed/filtered
```

ADR-0034 §2 makes the controller the per-node agent on every host. Today it runs
only on the serving host. With no controller on the voice host there is no
managed path from the operator host to inspect or start anything there — the
only remaining options are raw Docker or a manual session on the box, both of
which the ADR forbids as the normal path.

This also means the host cannot self-report. From the operator host, "voice
serves are stopped" and "voice host is unreachable" and "voice host is
mid-reboot" are indistinguishable.

**2. The router advertises routes whose backing serves are down.** The serving
host's installed router profile routes `llm.voice` to the voice host's LLM port
and both audio routes to its STT/TTS ports. Those serves are not running, so
every one of those aliases currently resolves to an endpoint that cannot answer.

Readiness gates the request at call time, so this is not a correctness bug — a
caller gets a clean failure rather than a wrong answer. It is an observability
gap: nothing surfaces "three configured capabilities have no backing serve"
until a user tries one.

## Why this is not simply "the operator turned it off"

The serves were stopped deliberately. The host was later powered back on, and
the serves did **not** come back with it. That is the actual finding: there is
no declared desired-state reconciliation for a host that returns, and no signal
that a returning host came back empty.

ADR-0033 deliberately rejects a self-healing daemon, and that decision stands.
The gap is not automatic restart — it is that nothing *reports* the divergence
between the router's declared routes and what any host is actually serving.

## Required behavior

1. Deploy the controller as a node agent on the voice host, with an operation
   catalog scoped to that host's roles (ADR-0034 §2). This is the concrete first
   task of ADR-0034 step 3 and should be done read-only first.
2. Provide a fleet-level capability report that answers "for every configured
   alias, is there a reachable backing serve, and on which host" — the
   fleet-level question ADR-0034 §5 says is in scope, as distinct from
   placement, which is not.
3. A host that returns to the tailnet with no serves running should be
   *reportable* as such. Reconciliation stays operator-initiated; only the
   visibility is automatic.

## Impact on verification

`runtime = "docker"` migration coverage could not be live-verified on the voice
host during the 2026-08-08 deployment. Its manifests were migrated and parse
correctly, and its manifest set loads under the new schema when read from the
serving host, but no serve on that host was started to confirm end to end.
