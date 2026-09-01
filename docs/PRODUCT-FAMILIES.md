# Product families and user journeys

Anvil Serving is one umbrella product for operating, qualifying, and exposing
local AI capabilities through explicit, reviewable contracts. It is broader
than the request router and narrower than a general orchestration platform.

The public product has six families. Anvil Voice and Anvil Media are branded,
first-class domains inside Anvil Serving; they are not separate repositories,
installations, or release lines. Run `anvil-serving product families` for the
same map from installed code, or `anvil-serving product journey FAMILY` for an
ordered CLI journey.

## The boundary in one table

| Product family | Promise | Primary commands | Does not own |
| --- | --- | --- | --- |
| [Model Serving](#model-serving) | Discover, pin, start, inspect, switch, and stop local model serves. | `init`, `models`, `serves` | Caller capability choice or automatic promotion. |
| [Capability Gateway](#capability-gateway) | Expose exact aliases through one authenticated protocol boundary. | `router` | Intent classification, semantic model selection, or fallback. |
| [Evaluation & Evidence](#evaluation-evidence) | Prove compatibility and retain comparison-safe evidence. | `eval` | Serve, route, or promotion mutation from a result alone. |
| [Anvil Voice](#anvil-voice) | Operate qualified STT, TTS, and realtime voice paths. | `voice` | Undeclared placement or hidden split-host fallback. |
| [Anvil Media](#anvil-media) | Run bounded named image/video workflows with durable jobs and artifacts. | `media` | Raw backend graphs, paths, installs, placement, or fallback. |
| [Control Plane & Fleet](#control-plane-fleet) | Resolve ownership, dispatch bounded operations, and expose fleet state. | `topology`, `controller`, `mcp`, `fleet`, `host`, and integration utilities | Resource-owner bypass, embedded secrets, or SSH-first operations. |

Every visible operational root command belongs to exactly one family in the
machine-readable command manifest. A journey may call a supporting command
from another family—qualification follows serving, for example—but that does
not transfer authority between the families.

## Model Serving

Model Serving owns model artifacts, reusable recipes, manifest-backed serve
lifecycle, GPU reservations, switching, and guarded promotion. A recipe can be
loaded and qualified without becoming caller-visible. A serve can be ready
without being routed.

1. Create a private operator scaffold with
   `anvil-serving init --out-dir <OPERATOR_HOME>`.
2. Inspect pinned candidates with `anvil-serving models recipes list`.
3. Review lifecycle and reservation changes with
   `anvil-serving serves up --group <GROUP> --dry-run`.
4. Start only the reviewed group with the same command plus `--confirm`.
5. Inspect declared ownership and readiness with `anvil-serving serves status`.

Start with [Models & recipes](cli/models.md) and
[Serve lifecycle commands](cli/serves.md). Qualification and promotion remain
separate gates.

## Capability Gateway

The Capability Gateway owns authentication, exact alias resolution, dialect
translation, readiness, admission, streaming, and relay. Each accepted chat
alias selects one configured tier. Unknown aliases return 404; an unavailable
selected tier returns an error. It does not try another model.

1. Preview the declared router lifecycle with
   `anvil-serving router up --dry-run`.
2. Start the reviewed router with `anvil-serving router up --confirm`.
3. Inspect the installed service with `anvil-serving router status`.
4. Use `anvil-serving eval routed --help` to select the real-client acceptance
   contract for a caller-visible alias.

Read [Capability meta-router](META-ROUTER.md) for the authority model and
[Router commands](cli/router.md) for lifecycle and transition operations.

## Evaluation & Evidence

Evaluation & Evidence owns endpoint preflight, routed acceptance, capacity and
quality benchmarks, external evidence normalization, and durable run records.
Passing evidence supports a human decision; it never changes serving state on
its own.

1. Resolve the endpoint and checks with
   `anvil-serving eval preflight --tier <TIER> --dry-run`.
2. Execute the reviewed gate with the same command plus `--confirm`.
3. Choose the benchmark matching the claim through
   `anvil-serving eval benchmark --help`.
4. Review retained runs with
   `anvil-serving eval benchmark evidence list` before promotion.

See [Evaluation & benchmark commands](cli/eval.md) and the
[evidence methodology](benchmarks/methodology.md).

## Anvil Voice

Anvil Voice is the STT, TTS, realtime proxy, voice-profile, sidecar, corpus,
and voice-benchmark domain. The aggregate lifecycle is only for co-located
ownership; split-host deployments operate audio and proxy owners explicitly.

1. Validate an explicit profile with
   `anvil-serving voice profiles validate --profile <PROFILE>`.
2. Review STT/TTS startup with
   `anvil-serving voice audio up --profile <PROFILE> --dry-run`.
3. Review realtime proxy startup separately with
   `anvil-serving voice proxy up --profile <PROFILE> --dry-run`.
4. Inspect the managed audio path with
   `anvil-serving voice audio status --profile <PROFILE>`.
5. Select an independent voice gate through
   `anvil-serving voice benchmark --help`.

See the [Voice pipeline](VOICE.md) and [Voice commands](cli/voice.md).

## Anvil Media

Anvil Media owns named workflow validation, exact bundle inventory and staging,
durable jobs, idempotency, cancellation, restart reconciliation, qualification,
and opaque artifact metadata. A caller selects one stable workflow id, version,
and declared quality profile. The family never accepts a raw ComfyUI graph,
model filename, filesystem path, installation request, host, or fallback list.

1. Discover qualification-gated workflows with
   `anvil-serving media capabilities`.
2. Verify exact assets with
   `anvil-serving media bundle inventory <WORKFLOW> --version <VERSION> --models-volume <VOLUME>`.
3. Check worker compatibility with
   `anvil-serving media workflow validate <WORKFLOW> --version <VERSION> --backend-url <URL>`.
4. Review a bounded submission by adding `--dry-run` to
   `anvil-serving media workflow run` with explicit workflow, version,
   parameters, principal, and backend URL.
5. Follow the durable record with
   `anvil-serving media job status <JOB_ID> --principal <ID>`, then inspect any
   opaque result through `media artifact inspect`.

Read [Media commands](cli/media.md) for the complete worker-to-artifact journey
and [ADR-0040](adr/0040-media-gateway-and-controller-authority.md) for the
gateway/controller authority split.

## Control Plane & Fleet

Control Plane & Fleet owns topology and target resolution, bounded MCP and
controller dispatch, host readiness and repair, fleet parity/drift reports,
client reconciliation, observability, tailnet edge management, upgrades, and
the optional Workbench stack. Normal operations flow through typed Anvil
surfaces. Verified SSH exists only as bounded recovery for commands that
declare it.

1. Validate ownership offline with `anvil-serving topology validate`.
2. Inspect the command host with `anvil-serving host status`.
3. Probe the typed remote boundary with `anvil-serving controller status`.
4. Check package parity with `anvil-serving fleet version`.
5. Inspect the agent-facing catalog with `anvil-serving mcp tools` before
   reconciling a client.

Continue with [Control Plane & Fleet commands](cli/control-plane.md),
[Private networking with Tailscale](TAILSCALE-NETWORKING.md),
[Fleet commands](cli/fleet.md), [Host & setup](cli/host.md), and the
[Agent workbench](WORKBENCH.md).

## Cross-family handoffs

The normal promotion path deliberately crosses boundaries:

```text
Model Serving candidate
  -> Evaluation & Evidence gate
  -> human review
  -> guarded Model Serving promotion
  -> Capability Gateway acceptance
  -> Control Plane & Fleet parity and client checks
```

Voice and Media use the same pattern with domain-specific qualification. No
arrow is an implicit fallback, auto-promotion, or transfer of authority.
