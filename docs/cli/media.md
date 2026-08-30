# Anvil Media commands

[Product families](../PRODUCT-FAMILIES.md#anvil-media) ·
[CLI overview](../CLI.md) ·
[Control Plane & Fleet](control-plane.md)

`anvil-serving media` is the bounded operator path for named image and video
workflows, exact worker bundles, durable jobs, qualification, cancellation,
and opaque artifacts. The CLI and the gateway's MCP/A2A projections use the
same operation contracts.

Anvil Media never accepts a caller-supplied ComfyUI graph, node name, model
filename, filesystem path, install request, backend route, host selection, or
fallback list. Operator configuration maps one workflow version to one media
service and resource owner.

## Choose a journey

| Goal | Start here | Then |
| --- | --- | --- |
| Discover callable and blocked workflows | `media capabilities` | Inspect the exact version with `media workflow show`. |
| Prepare a media worker | `media bundle inventory` | Preview missing exact assets with `media bundle stage --dry-run`; apply only with `--confirm`. |
| Check workflow compatibility | `media workflow validate` | Keep the workflow unavailable when any required feature, node, model, or backend fact is unknown. |
| Run a named workflow | `media workflow run ... --dry-run` | Review the binding, then submit the same request with `--confirm`. |
| Follow or cancel work | `media job status` | Use `media job cancel --dry-run`, then `--confirm`, for the same principal. |
| Inspect a result | `media artifact inspect` | Fetch bytes only through the authenticated gateway artifact boundary. |
| Qualify a blocked workflow | `media qualify run ... --dry-run` | Apply with `--confirm`, retain evidence, and require independent quality review. |

## Discover capabilities

```bash
anvil-serving media capabilities
anvil-serving media workflow list
anvil-serving media workflow show image.flux2-klein-4b-fp8-v1 --version v1
```

Capability discovery reports stable workflow ids, versions, parameter schemas,
explicit quality profiles, limits, and availability reasons. `available=true`
means the exact deployed workflow passed its declared functional, capacity,
and independent quality gates. It does not mean every prompt is guaranteed to
meet a perceptual goal, and it does not set `promoted=true` automatically.

`--registry`, `--state-db`, and `--artifact-root` select operator-owned state.
Public packaged workflow descriptors remain templates and pinned defaults; real
deployment values belong in the private operator home.

## Prepare the worker bundle

Inventory verifies the exact workflow version, lock file, and protected model
volume without writing:

```bash
anvil-serving media bundle inventory <WORKFLOW> \
  --version <VERSION> \
  --models-volume <VOLUME>
```

Stage only the missing, digest-pinned assets after reviewing the same plan:

```bash
anvil-serving media bundle stage <WORKFLOW> \
  --version <VERSION> \
  --models-volume <VOLUME> \
  --user-volume <VOLUME> \
  --runtime-uid <UID> \
  --runtime-gid <GID> \
  --dry-run

anvil-serving media bundle stage <WORKFLOW> \
  --version <VERSION> \
  --models-volume <VOLUME> \
  --user-volume <VOLUME> \
  --runtime-uid <UID> \
  --runtime-gid <GID> \
  --confirm
```

Staging never replaces an existing mismatched file. A mismatch is a refusal,
not permission to repair by guessing or downloading another revision.

## Validate one named workflow

```bash
anvil-serving media workflow validate image.flux2-klein-4b-fp8-v1 \
  --version v1 \
  --backend-url http://127.0.0.1:8188
```

Validation compares the immutable descriptor with bounded worker feature,
node, and model inventories. Backend readiness is transport evidence only. It
does not prove capacity, perceptual quality, or promotion.

## Preview and submit a workflow

The request binds only descriptor-declared parameters. Quality profiles are
locked parameter sets inside the same workflow; they cannot select another
model, worker, backend, or provider.

```bash
anvil-serving media workflow run image.flux2-klein-4b-fp8-v1 \
  --version v1 \
  --quality-profile standard \
  --parameters '{"prompt":"a forged steel anvil","seed":7}' \
  --principal caller-1 \
  --backend-url http://127.0.0.1:8188 \
  --dry-run
```

Submit the identical reviewed request with `--confirm`. Supply an
`--idempotency-key` when a caller may retry. An accepted job is durable and
continues across caller disconnects. If the declared worker is cold, ordinary
submission does not silently start it: the job remains `awaiting_approval`
with the exact managed lifecycle preview unless an already-reviewed private
policy authorizes the existing transaction.

## Inspect and cancel durable jobs

```bash
anvil-serving media job status <JOB_ID> --principal caller-1
anvil-serving media job cancel <JOB_ID> \
  --principal caller-1 \
  --backend-url http://127.0.0.1:8188 \
  --dry-run
anvil-serving media job cancel <JOB_ID> \
  --principal caller-1 \
  --backend-url http://127.0.0.1:8188 \
  --confirm
```

Status derives phase and end-to-end latency from ordered durable events.
Cancellation is principal-scoped, idempotent, and reconciled with the selected
backend. It never cancels unrelated backend work by filename or queue position.

## Inspect opaque artifacts

```bash
anvil-serving media artifact inspect <ARTIFACT_ID> --principal caller-1
```

Artifact records contain bounded metadata and provenance. Backend output paths
are never public identifiers. Full bytes are delivered through the
authenticated `/artifacts/{opaque-id}` boundary; eligible small images may
also be returned as bounded native MCP image content. Video is resource-only.

## Qualify without promoting

```bash
anvil-serving media qualify run image.flux2-klein-4b-fp8-v1 \
  --version v1 \
  --quality-profile standard \
  --parameters '{"prompt":"qualification sample","seed":7}' \
  --principal qualification \
  --backend-url http://127.0.0.1:8188 \
  --models-volume <VOLUME> \
  --dry-run
```

Apply with `--confirm` only on the declared worker. The qualification captures
functional artifact checks and bounded capacity evidence. Perceptual quality
must be decided by a human or separately administered evaluator that did not
generate the artifact. A successful run does not change availability or
promotion state by itself.

## Lifecycle and integration boundary

The resource-owning host operates ComfyUI through managed `serves` and
controller tools; raw Docker is not the operational path. The caller-facing
media catalog excludes lifecycle and general operator tools. To reconcile the
packaged media-only skill and eight-tool allowlist into selected Hermes
profiles, use `anvil-serving harness sync hermes-media --dry-run` and review
the [Control Plane & Fleet reference](control-plane.md#harness).

See [ADR-0040](../adr/0040-media-gateway-and-controller-authority.md) for the
gateway/controller authority split and
[ADR-0041](../adr/0041-initial-media-workflows-and-policy.md) for the initial
workflow and qualification policy.
