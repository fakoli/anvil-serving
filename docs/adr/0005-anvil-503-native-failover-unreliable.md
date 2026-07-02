# ADR-0005 — anvil-503 native-failover loop: OpenClaw's fallback walk does not escape a `providerOverride`

- **Status:** Accepted (documented finding + operator mitigation; no repo-side code fix exists)
- **Date:** 2026-07-01
- **Relates to:** [ADR-0001](0001-cloud-cost-and-subscription-auth.md) (keyless handoff mechanism this
  finding falsifies part of), `docs/OPENCLAW-INTEGRATION-SPEC.md` §7,
  `docs/OPENCLAW-LIVE-VALIDATION.md` (Gap 4), `plugins/openclaw-anvil-intent-router/`

## Context

ADR-0001 designed anvil's keyless (local-only, no cloud API key) default around a specific
mechanism: when anvil exhausts its local candidate tiers for a request, it returns a 503 with zero
streamed local tokens; OpenClaw's own **native failover**
(`agents.defaults.model.fallbacks`) is a transport-class-error retry mechanism (fires on
auth/429/overloaded/timeout/billing), and a 503 was assumed to trip its "overloaded" category —
handing the request off to the operator's native cloud subscription with no cloud key ever touching
anvil. ADR-0001 flagged this as the one "must validate live (currently UNCONFIRMED)" step.

A real OpenClaw agent turn (v0.6.0, live gateway, 2026-07-01) exercised this path for the first
time end-to-end. `plugins/openclaw-anvil-intent-router`'s T008 upfront routing split classified the
turn as a local-preferred class (quick-edit/review/chat/long-context) and emitted
`before_model_resolve` result `{ providerOverride: "anvil", modelOverride: "<preset>" }`. anvil could
not serve the request locally and returned its keyless-handoff 503. OpenClaw's native failover DID
fire — `agents.defaults.model.fallbacks` was configured with `["openai/gpt-5.5",
"openai/gpt-5.4-mini"]` — confirming the first half of ADR-0001's mechanism (503 trips the
"overloaded" category). But **both fallback attempts also 503'd**, and inspection showed they were
also resolved through the `anvil` provider rather than `openai`. The user saw "couldn't generate a
response" instead of a graceful handoff to their native subscription.

**Source-grounded explanation.** `docs/OPENCLAW-INTEGRATION-SPEC.md` §0 already source-confirms that
`before_model_resolve` "fires once per run, above the attempt loop" (`run.ts` L1033
`resolveHookModelSelection`, applied at `setup.ts` L98–103). The live symptom is consistent with the
`providerOverride` component of that one resolution being applied for the run's ENTIRE attempt loop
— not just the first (primary) attempt — so the fallback walk's model strings get re-resolved
against the same pinned provider rather than the provider named in each fallback entry.

**Scope.** This affects every turn where `before_model_resolve` emits a `providerOverride` — i.e.
every local-preferred-class turn under the current classify table (quick-edit, review, chat,
long-context; the majority of traffic). It does **not** affect cloud-preferred turns (`planning` by
default): those return `{}` (no override at all), so nothing sticks and OpenClaw's normal resolution
runs.

## Considered options

1. **Patch OpenClaw.** Out of scope — this repo does not own or vendor OpenClaw; `CLAUDE.md`'s
   focus-not-couple principle keeps all OpenClaw-specific code in the swappable plugin package, not
   a fork of OpenClaw itself.
2. **Have anvil emit a different HTTP status on exhaustion, hoping OpenClaw's attempt loop resolves
   the provider differently for a different transport-error category.** Unsupported by any
   source-confirmed evidence — the defect is in provider resolution across the attempt loop, not in
   which transport-error category is matched (the fallback DID fire; it just resolved wrong).
   Speculative, not grounded; rejected.
3. **Stop the plugin from ever emitting `providerOverride` for local-preferred classes, i.e. drop
   T008's upfront split entirely and route everything through anvil's own Tier-0 classifier via a
   static `agents.defaults.model.primary: "anvil/chat"`.** Defeats the entire local-first design —
   nothing would ever route to anvil deliberately with tier fidelity. Rejected.
4. **Document the defect precisely and give operators two concrete, already-available mitigations.**
   Chosen — see Decision.

## Decision

**No repo-side code fix exists for the native-failover loop itself** — it is OpenClaw's attempt-loop
provider resolution, not anvil-serving's or the plugin's. Ship the following instead:

1. **Correct the previously-inaccurate "safety net" claims** in `plugins/openclaw-anvil-intent-router/`
   (`route.mjs`, `index.ts`, `README.md`) and `docs/OPENCLAW-INTEGRATION-SPEC.md` §0/§7 — the
   keyless-503 → native-failover handoff is reliable ONLY for cloud-preferred classes (no
   `providerOverride` emitted); it is NOT reliable once a `providerOverride` is in play.
2. **Operator mitigation A — `ANVIL_CLOUD_CLASSES`.** Move a work-class whose local tier is known to
   be flaky/exhausted into the cloud-preferred set (already-shipped T008 knob). Its turns never touch
   anvil at all, so there is nothing for the failover walk to inherit. Zero anvil-side config change;
   trades away local-first routing for that class.
3. **Operator mitigation B — anvil's own opt-in metered cloud tier (durable fix).** Per ADR-0001,
   enable `configs/example-with-cloud.toml` and add the at-risk work-classes to
   `[router].metered_cloud`. anvil's own `fallback.py` serve→verify→escalate ladder then escalates to
   a bound cloud tier **inside the same `provider="anvil"` HTTP response** — anvil never returns 503
   for those classes, so OpenClaw's native failover (reliable or not) is never invoked. This requires
   the explicit billing opt-in ADR-0001 already gates; it is not a silent behavior change.
4. **Record this as a live-validation gap (Gap 4)** in `docs/OPENCLAW-LIVE-VALIDATION.md`, alongside
   the reproduction steps, so a future OpenClaw release can be re-tested and this ADR revisited (if
   OpenClaw's attempt loop changes, mitigation B remains valid regardless — it never depends on
   OpenClaw's failover behavior at all).

ADR-0001 is **not** superseded — its core decision (no cloud API key in the default path; cloud is
opt-in and billing-gated) stands. This ADR narrows ADR-0001's "keyless handoff" *mechanism* claim:
the exhaustion-503 reliably trips OpenClaw's failover category, but the failover's *result* is only
trustworthy when the plugin has not itself pinned a provider for the run.

## Consequences

- **Local-only (keyless) operators using this plugin should not treat `agents.defaults.model.fallbacks`
  as a reliable safety net** for local-preferred classes. They must pick mitigation A or B per
  at-risk work-class, or accept that a local-unable condition on those classes can surface as a
  failed turn rather than a graceful cloud handoff.
- **Mitigation B is the recommended default for anyone who wants the keyless design's original
  promise (graceful cloud handoff on local-unable) to actually hold** — it moves the escalation
  inside anvil, where this repo has full control, rather than depending on unverified OpenClaw
  internals.
- **No anvil-serving runtime code changes.** The router's own contract (503 with zero streamed local
  tokens on exhaustion) is unchanged and correct; this finding is entirely about what happens
  downstream of that 503 inside OpenClaw.
- **Follow-up:** if a future OpenClaw release changes attempt-loop provider resolution, re-run the
  Gap 4 reproduction in `docs/OPENCLAW-LIVE-VALIDATION.md` and update this ADR's status accordingly
  (do not delete it — supersede if the finding no longer holds).
