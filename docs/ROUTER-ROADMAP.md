# Router improvements: evidence and delivery plan

Research observed on **2026-09-05**, starting from public source
`944b7df3`. This plan develops the gateway's position on the request path while
preserving explicit capability selection, engine independence, and separate
model qualification and deployment decisions.

## Delivery batch

| Improvement | Operator benefit | Acceptance boundary |
| --- | --- | --- |
| Generated request correlation | Follow one request from client response to gateway evidence and a supporting upstream log. | Real loopback HTTP/SSE across three dialects, duplicate caller IDs, auth and keepalive isolation; upstream adoption remains engine-specific. |
| Phase, finish, and usage telemetry | Distinguish response startup from completion; identify output ceilings and upstream-reported accounting. | Deterministic timing, eager/lazy streams, tool-only output, failure/cancellation and unknown measurements. No payload capture. |
| `router diagnose` | Retrieve and interpret terminal evidence in one read-only CLI operation. | Bounded authenticated GETs, no redirects or replay, schema/identity validation, honest missing/current-state handling. |
| Private remote endpoint bundle | Prepare a reviewed Tailscale container endpoint and direct router tier without manually assembling unrelated files. | Offline schema/render tests; actual provider, image, tailnet and model acceptance remain unproven until deployed and qualified. |
| Authenticated readiness | A protected `/v1/models` endpoint can satisfy readiness and exact model identity. | Actual protected loopback endpoint, missing credential fail-closed, redirect denial, no credential output. |

The executable user documentation is [request diagnostics](ROUTER-DIAGNOSTICS.md)
and [remote tailnet endpoints](REMOTE-TAILNET-ENDPOINTS.md). The implementation
and review ledger is the
[diagnostics ticket](https://github.com/fakoli/anvil-serving/blob/main/.tickets/2026-09-05-router-request-diagnostics.md).

## Research that shaped the scope

OpenTelemetry's GenAI conventions distinguish operation timing, token usage,
finish reasons, and model metadata from potentially sensitive message content.
Content capture is optional rather than a requirement for useful telemetry.
Anvil therefore starts with measured metadata and provenance, keeping an
exporter out of its stdlib inference path.
[GenAI conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)
and [OpenTelemetry's 2026 observability guidance](https://opentelemetry.io/blog/2026/genai-observability/).

W3C trace context has validation and propagation rules; an arbitrary request ID
is not a distributed trace. This batch supplies an application correlation ID.
A later exporter must implement context validation, fresh span identity, and
sampling deliberately rather than renaming the current field.
[W3C Trace Context](https://www.w3.org/TR/trace-context/).

vLLM documents optional request-ID headers. A gateway-generated ID can be sent
to that upstream without forwarding caller lineage or capturing headers. The
operator still has to enable and verify support on the pinned engine; another
compatible serving engine may ignore it.
[vLLM OpenAI-compatible server](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html).

Tailscale provides container configuration, userspace networking, and Serve
configuration. Those are useful building blocks for a private endpoint, but
they do not establish model authentication, qualification, or cloud-host trust.
The generated bundle keeps the inference listener on loopback, publishes no
host port, and retains application authentication.
[Docker parameters](https://tailscale.com/docs/features/containers/docker/docker-params)
and [userspace networking](https://tailscale.com/docs/concepts/userspace-networking).

The provider environment matters. Vast's standard instance is already a Docker
container; it cannot be treated as a VM on which to start a Compose sidecar
stack. A custom multi-process image or a host that supports Docker Compose is
needed before that deployment recipe can be qualified. Rendering files is not
evidence of a working Vast deployment.
[Vast Docker execution environment](https://docs.vast.ai/guides/instances/docker-environment).

Current code and prior operator investigations also exposed a recurring
interpretation gap: end-to-end latency alone cannot establish engine queueing,
and a current metadata snapshot cannot prove a historical request's identity.
The diagnostic output separates observations from suggested checks and labels
its current router status as a separate read.

## Next delivery order

1. **Finish the existing same-host replica and workload campaign.** The
   [qualified replica](prds/qualified-replica-sets.md),
   [capacity scheduler](prds/replica-capacity-scheduler.md),
   [fleet enrollment](prds/fleet-node-enrollment.md), and
   [workload visibility](prds/workload-visibility.md) contracts own those
   implementations. Request diagnostics complements their lifecycle views.
2. **Extend explicitly qualified replicas across hosts.** Establish identity,
   context/tool parity, freshness, admission, cancellation, and no-replay
   invariants before changing the same-host restriction. See the
   [cross-host replica ticket](https://github.com/fakoli/anvil-serving/blob/main/.tickets/2026-09-05-cross-host-qualified-replicas.md).
3. **Add optional trace export and request-time configuration identity.** Keep
   export disabled by default, use bounded queues with visible drops, and prove
   collector failure cannot delay inference. See the
   [trace export ticket](https://github.com/fakoli/anvil-serving/blob/main/.tickets/2026-09-05-router-trace-export.md).

Per-principal quotas and explicit affinity are later candidates after the
authorization and replica contracts stabilize. Quotas need deterministic
reservation/release accounting; affinity needs a declared opaque key and a
bounded expiry policy. Neither should read prompt content or introduce hidden
fallback. Response caching, prompt inspection, automatic model selection, and
automatic cloud provisioning are outside this batch.

## Completion evidence

Source verification uses synthetic transports and real loopback HTTP servers,
the full repository regression suite, strict docs and link checks, CLI manifest
checks, a clean wheel install, and independent adversarial review. Record exact
results and every fixed review defect in the delivery ticket. Source merge,
package release, remote enrollment, and live client acceptance remain distinct
outcomes.
