# ADR-0043 — Model workloads deny network egress by default

- **Status:** Accepted
- **Date:** 2026-08-30
- **Relates to:** [ADR-0002](0002-serves-are-compose-defined.md),
  [ADR-0012](0012-serve-and-router-management-verbs.md),
  [ADR-0032](0032-public-product-private-operator-state.md),
  [telemetry investigation](../findings/2026-08-30-model-runtime-telemetry-investigation.md)

## Context

Model snapshots are data, but an inference process includes a third-party engine,
container image, tokenizer and template handling, native libraries, plugins, and
sometimes model-supplied executable code. Reviewing a checkpoint alone therefore
cannot prove that a long-running serve emits no telemetry.

A read-only inspection of one live vLLM-derived GLM-5.3 serve found the engine's
default usage reporter enabled and observed a TLS connection to `stats.vllm.ai` at
its documented heartbeat interval. The bounded local usage records contained
hardware, platform, engine, model-architecture, and launch-configuration metadata;
they did not contain prompt or completion bodies. This disproved the broader claim
that a local model-serving stack necessarily sends nothing, while providing no
evidence that the model weights themselves initiated exfiltration.

Per-engine opt-out variables are useful defense in depth, but they are voluntary,
runtime-specific, and incomplete against unknown code. The platform needs an
engine-agnostic boundary that fails closed before a model workload starts.

## Considered options

1. **Trust engine telemetry opt-outs.** Low friction, but every engine and plugin
   must implement and honor its own setting.
2. **Observe outbound traffic and alert.** Useful evidence, but detection happens
   after information may already have left.
3. **Deny workload egress by default and restrict exceptions to gateways.** Model
   downloads must be staged separately, but the serving boundary is enforceable
   without trusting the model or engine.

## Decision

Long-running model workloads managed by Anvil Serving use
`network_egress = "deny"` when the field is absent.

- Compose launches are preflighted through `docker compose config --format json`.
  Every network attached to each selected denied service must resolve to
  `internal: true`; `network_mode: none` is also acceptable. Host, bridge, and any
  non-internal or unproved network fail before lifecycle mutation.
- Recipe launches create or freshly verify one Anvil-owned internal bridge named
  `anvil-serving-model-egress-denied`, then attach the container to it. A
  same-named network without the expected driver, internal flag, and ownership
  label is rejected. Model recipes cannot declare an allow exception.
- Opaque launch scripts cannot claim default-deny because their network behavior
  cannot be proved before execution. Model launches must migrate to Compose or
  `models recipes load`.
- `network_egress = "allow"` is limited to a manifest entry with
  `gpu_inference = false`, a `network_egress_role` of `capability-gateway`,
  `media-gateway`, or `voice-gateway`, and a non-empty
  `network_egress_reason`. Ad-hoc Compose has no manifest, so each selected
  gateway exception uses the equivalent
  `io.anvil-serving.network.egress=allow` and
  `io.anvil-serving.network.egress-role=...` and
  `io.anvil-serving.network.egress-reason=...` service labels.
- A stopped non-Compose container or paused Compose container governed by deny
  is not blindly resumed. The operator must use `serves up NAME --recreate`,
  causing the current managed launch definition to apply the network boundary.
- Downloads remain a separate, bounded operation. Images may be pulled by the
  Docker daemon and model assets may be synchronized into managed storage before
  the inference container starts; a denied serve is not granted temporary egress
  to fetch missing assets.

The capability router may be dual-homed: it joins the internal model network to
reach backends and a separate ordinary network for its declared gateway function.
Media or voice gateways may receive the same narrowly scoped exception only when
their declared upstream topology requires it. Model services never join an egress
network.

## Consequences

- A newly created or reconciled default model serve cannot establish ordinary
  outbound internet connections through its Docker network, regardless of which
  model family or serving engine it runs.
- Missing images and model snapshots must be staged before serving. Startup that
  previously downloaded assets opportunistically will fail until the download
  phase is made explicit.
- Gateway exceptions are role-limited, visible, reviewable configuration rather
  than an ephemeral command-line switch.
- This boundary does not prove a model or engine is benign, inspect encrypted
  payload semantics, constrain filesystem/GPU behavior, or govern processes run
  outside Anvil Serving. It also does not replace credential isolation, artifact
  provenance, remote-code review, or host firewall policy.
- Source adoption and live deployment remain separate. Existing containers keep
  their current networks until a separately authorized recreate/reconciliation.
