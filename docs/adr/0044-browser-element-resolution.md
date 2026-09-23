# ADR 0044: Guarded advisory browser element resolution

Status: implemented advisory consumer and bounded pilot adapter; installed and live acceptance remain separate.

## Decision

Reuse Anvil's `browser_element_resolution` capability and existing typed Jev adapter. A trusted browser owner supplies a bounded DOM projection only after its own source and export authorization. Jev returns one advisory selection. It never establishes browser freshness, scope completeness, predicate truth or action authority. Existing Jev advice enablement does not enable this separate capability or authorize page export.

The owning Anvil implementation is pinned by accepted task receipts, immutable source commits and merge ancestry, not a package version alone. The receiving project retains the exact PRD revision/source digest, task references, independent review verdicts, submit/apply receipts and an authoritative snapshot event cursor in its private execution evidence. Recheck that binding immediately before claiming the consumer. A changed source revision blocks dispatch and needs a replacement prerequisite plus a supported dependency update; never reopen old acceptance or force a claim. This is a coordinated review protocol, not an atomic cross-project scheduler guarantee.

## Projection

`browser-element-resolution-projection/v1` has exactly `schema`, `request_id`, `observation_id`, `source`, `target`, `scope`, `coverage`, and `entities`. Source is `dom`. Limits are UTF-8 bytes: request/observation IDs 64; target description 512 and at most 8 qualifiers of 128 each; scope root 64; incomplete coverage reason 256. Scope kind is document/subtree/viewport. Entity IDs and scope roots are bounded opaque references. They reject empty or whitespace-padded values, URI schemes, protocol-relative forms and any :// substring without trimming; exact UUID:e-N owner handles are allowed. Ordinary UTF-8 opaque references remain valid. A root is an owner handle or literal scope identity, never a URL. Complete coverage requires a null reason; partial/unknown requires a reason.

Offer 1–64 entities with unique case-sensitive IDs of at most 64 bytes, excluding the three abstentions case-insensitively. Each entity has exactly id/role/text/nearby/state/predicate_reasons. Role is bounded 128 bytes, text 1024 (empty allowed), nearby 512 or null. State has exists/in_viewport/occluded/enabled, each boolean or null. Boolean facts have null reasons; null facts have not_applicable/unknown/unsupported reasons. Disabled controls may match with enabled=false; a noninteractive element may match with enabled=null.

The full canonical request, including model and fixed questions, is bounded 32 KiB. Recognized credential patterns are refused. This is closed-field validation and a limited credential check, not general data-loss prevention or proof that the caller owns the source. The owner must not supply HTML, screenshots, URLs, form values, credentials, transcript, handles or epochs as additional fields or disguise them in approved text fields. Preserve the original target and scope rather than silently rewriting semantic intent.

## Bridge and result

Anvil provides a stateless `anvil jev bridge --json` entry point for trusted local consumers. Its stdin envelope has exactly `jev` (validated Jev configuration), boolean `allow_api`, boolean `allow_export`, `capability`, and `input` (the projection). The shared advise path checks disabled, capability, API and explicit export gates before constructing the projection, looking up a key or calling the provider. The bridge does not load project state; caller-supplied policy is only appropriate behind a trusted local owner boundary.

The Serving consumer invokes the existing project-aware command without a shell:

```text
anvil jev evaluate browser_element_resolution --input <private-selected-json> --allow-export --json
```

Its executable, project context and export policy belong to trusted owner configuration. It never accepts them from a resolution request. Project-aware evaluate loads effective Anvil settings and additionally checks configuration/input changes before returning advice. The owner authorizes before creating the private bounded file, enforces process time/output/cancellation bounds, and unconditionally removes it. No credential loading or provider client is duplicated in Serving.

The sole fixed question is `selection`, type `choice`. Accepted values are one offered opaque ID, `NO_MATCH_IN_CANDIDATES`, `AMBIGUOUS`, or `NEEDS_VISUAL_EVIDENCE`. In the CLI success envelope, `data` is an `anvil.jev.annotation.v1` annotation; consumers require the expected command, completed/used status and a well-formed closed selection. Duplicate JSON keys, malformed/unoffered choices, failed calls and unsupported responses stop with typed outcomes. Never infer success merely from process exit 0.

After a reply, the browser owner re-resolves the retained record and validates origin, record identity, epochs, expiry and revocation before exposing selected DOM facts. Partial/unknown coverage cannot prove global uniqueness or absence. Even complete semantic NO_MATCH is inconclusive without an independent absence oracle. Fresh predicates remain separately labeled DOM evidence; the semantic match is advice.

## Remaining gates

The synthetic Anvil corpus and fake-provider tests prove plumbing, not Jev accuracy. Serving now provides owner-backed projection, bounded subprocess integration, real browser fixtures and a session-bound live adapter exported as `@anvil-serving/observations/browser`. Its trusted harness supplies browser launch, up to two exact same-origin HTTPS document URLs and explicit Jev export policy. Eight-entity pages retain partial coverage; paging never establishes global absence or uniqueness. See [the adapter contract](../../browser_owner/README.md).

The pilot transport limits admitted, retained and forwarded response bytes, requests, concurrency and time. Node socket/TLS buffering can receive bytes before application admission; this does not satisfy the general-production strict wire-byte quota gate. That gate remains open. Installed Pi wiring, exact-runtime process cleanup and independently checked live receipts remain acceptance gates. Raw images stay outside primary requests; vision qualification, action authorization, installed enablement and general live deployment retain their own gates.
