# Anvil Media commands

[Product families](../PRODUCT-FAMILIES.md#anvil-media) ·
[CLI overview](../CLI.md) ·
[Control Plane & Fleet](control-plane.md)

`anvil-serving media` is the bounded operator path for named image and video
workflows, exact worker bundles, durable jobs, qualification, cancellation,
and opaque artifacts. The CLI and the gateway's MCP/A2A projections use the
same operation contracts.

The gateway never accepts a caller-supplied ComfyUI graph, node name, model
filename, server filesystem path, install request, backend route, host selection, or
fallback list. Operator configuration maps one workflow version to one media
service and resource owner.

## Choose a journey

| Goal | Start here | Then |
| --- | --- | --- |
| Discover callable and blocked workflows | `media capabilities` | Inspect the exact version with `media workflow show`. |
| Prepare a media worker | `media bundle inventory` | Preview missing exact assets with `media bundle stage --dry-run`; apply only with `--confirm`. |
| Check workflow compatibility | `media workflow validate` | Keep the workflow unavailable when any required feature, node, model, or backend fact is unknown. |
| Compile a shot brief | `media workflow prepare` | Inspect exact run arguments; submit them unchanged through existing admission. |
| Provide a reference | `media asset upload --dry-run` | Confirm the same bounded local file; bind its returned owned artifact ID. |
| Run a named workflow | `media workflow run ... --dry-run` | Review the binding, then submit the same request with `--confirm`. |
| Follow or cancel work | `media job status` | Use `media job cancel --dry-run`, then `--confirm`, for the same principal. |
| Inspect a result | `media artifact inspect` | Fetch bytes only through the authenticated gateway artifact boundary. |
| Qualify a blocked workflow | `media qualify run ... --dry-run` | Apply with `--confirm`, retain evidence, and require independent quality review. |

## Discover capabilities

Discovery includes workflow categories and an optional operator-owned recipe
catalog. `discovered`, `static-reviewed` and `needs-qualification` entries are
research candidates; they cannot execute merely because their source JSON
parsed. A `ready` catalog entry must name an available registered workflow.
UI workflow imports still require a reviewed API export and local qualification.

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

## Prepare a shot request

`media_workflow_prepare` accepts an explicit workflow/version, a stable
`idempotency_key`, optional `parameters`, `quality_profile`, and a structured
`brief`. `media_workflow_show` exposes each recipe's supported brief fields,
literal prompt template, reference roles and bounded parameter schema. The
server compiles those fields into declared bindings. It uses no LLM and does
not infer identity mapping, invent unsupported controls or choose another model.
Dialogue is inserted verbatim; unsupported brief fields and profile overrides
fail before submission.

```bash
anvil-serving media workflow prepare '<WORKFLOW>' --version '<VERSION>' --principal caller-1 --idempotency-key shot-01 --brief '{"scene":"Three hosts in a studio.","camera":"Wide shot."}'
```

Select a workflow that declares a prompt template and replace the quoted
placeholders with its advertised id and version. The result contains
`runArguments` and a digest covering the owner, complete workflow contract,
rendered graph, resolved parameters and request key.
Preparation checks owned reference metadata but does not contact/start the
worker, create a render job, or establish backend readiness. Call
`media_workflow_validate` with **only** `workflow_id` and `version`, then pass
`runArguments` unchanged to `media_workflow_run`. A changed/stale prepared
request fails with `media_plan_changed`. Existing direct submissions remain
supported and retain all validation/admission gates.

CLI callers can pass compiled parameters with `media workflow run` and its
`--plan-digest`; the MCP uses the same compiler and renderer. References are
owned opaque artifact IDs in declared slots, never caller filesystem paths or
node IDs. An image input does not imply face replacement, a speaker map or
lip synchronization; the chosen recipe must declare and qualify those roles.

## Upload reference media

When MCP capabilities report `assetUpload.available=true`, use the existing
protected MCP connection definition to upload a local PNG, JPEG, MP4 or WAV:

```bash
anvil-serving media asset upload --file reference.png --client-config connection.json --dry-run
anvil-serving media asset upload --file reference.png --client-config connection.json --confirm
```

The client discovers the intake policy, hashes the file and sends its bytes to
`POST /media/assets` on the configured gateway origin. It reuses the protected
credential source; it sends neither server paths nor credentials in tool
arguments or URLs. The raw intake route requires `media:submit`, one strict
Content-Length, a supported Content-Type and X-Content-SHA256. It does not accept
chunked/compressed bodies, remote source URLs or signed download authorization.
Retries create new owned input artifacts; they never submit generation jobs.

Intake permits at most32MiB and retains inputs for24hours. The managed gateway
must include `ffprobe`; otherwise intake stays unavailable. It checks signatures,
actual decoding, stream count, dimensions, decoded frame count, duration and
audio channels/rate. Decoder calls have deadlines, one thread, a file/pipe
protocol whitelist and disabled external MOV tracks, following the
[FFmpeg demuxer controls](https://ffmpeg.org/ffprobe-all.html#mov_002fmp4_002f3gp).
The global RGB-float32 extent bound is2GiB, with at most4096pixels per axis,
3600frames,120seconds, two audio channels and96kHz. This tensor estimate is not
a prediction of the complete generative runtime's memory requirement.

An imported reference can enter only a recipe declaring tighter applicable
`media_limits` for that artifact parameter; these appear as
`x-anvil-media-limits` in its schema. Existing generated outputs remain usable
by legacy recipes. Ownership, expiry, content hash and per-recipe bounds still
apply at submission. Unsupported audio/reference roles are rejected. Importing
three face images does not establish subject tracking, identity replacement or
three-speaker support. Original audio bytes remain a separate retained input;
native video saving can trim/re-encode audio and needs a separately qualified
master-preservation step.

## Prepare the worker bundle

For private deployment registries, follow the
[operator-owned video workflow contract](../VIDEO-MCP-WORKFLOWS.md). Keep model
selection, asset identities and qualification records in private operator storage.

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

Inventory uses only a cached, digest-pinned verification image. If that image
is missing, staging preview reports `helperPullRequired` and defers asset
inventory. Confirmed staging pulls the exact helper and checks existing files
before writing to either volume; unknown files are never treated as missing.

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
For a standalone CLI submission without a gateway reconciliation daemon, add
`--backend-url` with the same endpoint used for submission. This refreshes only
that principal-owned job from backend history and retains completed artifacts:

```bash
anvil-serving media job status <JOB_ID> --principal caller-1 \
  --backend-url http://127.0.0.1:8188
```

Repeat while the job is queued or running. Refresh never submits another
prompt or starts a worker. Without the option, status reads the stored snapshot.
Submission records a private endpoint digest before sending the prompt; refresh
rejects a different endpoint before contacting it. Older jobs without that
binding support stored status only.
Phase times are observation times; a delayed refresh is not engine latency.

Cancellation is principal-scoped, idempotent, and reconciled with the selected
backend. It never cancels unrelated backend work by filename or queue position.

## Background completion

Run a watcher on the harness host after MCP submission returns a job ID:

```bash
anvil-serving media job watch --job-id JOB_ID --client-config /path/to/client.json
```

The protected connection definition contains the existing stdio MCP `command`,
`args`, and optional `env`. It refers to the existing credential file; no
credential is added to the command line. The watcher calls only
`media_job_status` and `media_artifact_inspect`. It emits state changes and a
final JSON record containing completed artifact metadata and any download links
returned by the gateway. A gateway without link support returns relative
resources; the watcher never invents a URL.
Polling defaults to five seconds, with a two-hour watcher deadline.

In Pi, use its installed `bg_run` with `isAgent:false`,
`notifyOnCompletion:true`, and `triggerOnCompletion:true`. Set that task's
timeout at least 30 seconds longer than the watcher deadline for client cleanup.
Keep the background registration
receipt in the conversation, then continue independent work or end the turn.
Completion wakes a follow-up turn in the same open Pi session; no manual
"check" prompt is needed. See the `anvil-media-watch` companion skill.

Exit 0 means completed; exit 1 means a failed/canceled job or observation
error; exit 3 means operator approval is required; exit 4 means the watcher
deadline expired. A watcher deadline or shutdown never cancels rendering.
After a session restart, reattach to the saved job ID. The watcher cannot
notify a closed Pi session and never starts workers or resubmits a job.

## Inspect opaque artifacts

```bash
anvil-serving media artifact inspect <ARTIFACT_ID> --principal caller-1
```

Artifact records contain bounded metadata and provenance. Backend output paths
are never public identifiers. Full bytes are delivered through the
authenticated `/artifacts/{opaque-id}` boundary; eligible small images may
also be returned as bounded native MCP image content. Video is resource-only.

Through a gateway with `media_public_origin` configured, `media_artifact_inspect`
also returns `artifact.downloadUrl` for caller-owned artifacts. MP4 results
include a native MCP resource link to the same URL. By default,
`requiresAuthentication: true` means HTTP GET/Range requires the existing MCP
credential in the Authorization header. Each response remains bounded by
`artifact.maxRangeBytes` (16 MiB); larger files require sequential bounded
Range requests. A full GET above that limit returns 416.

For direct browser playback, an operator can set `ANVIL_MEDIA_SIGNED_DOWNLOADS=1`
on a dedicated gateway using static MCP authentication (device-key policy is
unsupported and rejected at startup). Inspection then returns
`requiresAuthentication: false`, `downloadAuthorization: "signed-url"` and
`downloadExpiresAt`. This URL authorizes reads of that one artifact for at most
one hour, capped by artifact expiry; it never contains the reusable MCP key.
Anyone holding it can read the video until expiry, so keep it private. Key
rotation revokes outstanding links. Inspect the artifact again to refresh an
expired link. This mode supports full GET, HEAD and one fixed, open-ended or
suffix byte range, streaming in bounded chunks. It does not authorize MCP calls
or other artifacts. No network exposure or tailnet configuration is changed.

The client verifies downloaded size and SHA-256 against inspection metadata.
The URL
uses the configured origin, never a caller-supplied Host header. Without an
origin, inspection keeps the relative `resource` path instead of guessing a URL.
Cross-principal inspection also keeps the relative path; its metadata scope
does not grant cross-principal HTTP downloads.

### Use an owned result as workflow input

A descriptor may declare a parameter with `kind: "artifact"` and optional
`media_types`, such as `["video/mp4"]`. Pass the opaque artifact ID returned by
a completed job. Before submission, the shared operation checks ownership,
expiry, declared MIME types and upload size; it verifies the content SHA-256,
uploads the bytes to the selected worker, and binds the returned input name
only inside the private execution graph. Public requests and job records keep
opaque IDs. An idempotent replay returns the existing job without uploading or
submitting it again, including after the original input expires.

Input handoff currently supports PNG and MP4 artifacts, bounded by 32 MiB and
the workflow's artifact-byte limit, whichever is smaller. It does not accept a
caller filesystem path or import an arbitrary local recording. Long recordings
must first be cut into supported, bounded shots by the owning application;
external-media ingestion is a separate integration boundary.

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

### Windows host memory guard

For a guarded Windows/Docker deployment, copy the operator-owned
`media-guard.toml` policy into the operator home. Give `lease_path` a dedicated
subdirectory beneath that home. Defaults retain the measured trial safeguards:
stop below 4 GiB available Windows RAM, below 16 GiB commit headroom, or below
8 GiB available RAM for 15 seconds. The worker also stops when the host signal
is more than five seconds old or its 45-minute lifetime expires.

```powershell
anvil-serving media memory-guard sample
anvil-serving media memory-guard install --confirm
anvil-serving media memory-guard status
```

Installation starts a hidden, small Windows publisher and registers it in the
current user's login startup entries. It reads native physical-memory and
commit counters; it does not load a model or operate other services. Its global
mutex prevents duplicate publishers across login sessions. The dedicated
signal directory has private ACLs, with existing explicit permissions reset.
Its policy and implementation digest must match the worker's expectations.

The Linux worker's fixed entrypoint uses `memory-guard supervise --confirm`
with its declared ComfyUI command after `--`. Mount the signal directory
read-only and map its path with `--lease`; never mount credentials in the
worker. Unsafe, missing, invalid or stale signals prevent startup or immediately
terminate only the supervisor's own child process group. The lifetime limit is
for the worker container, including idle time, rather than an individual shot.
Normal job-owned teardown remains the lifecycle owner's responsibility.

`install` and `uninstall` preview unless `--confirm` is supplied. To update a
guard policy or implementation, run `uninstall --confirm`, allow the publisher
to report `guard stopped`, then reinstall. Uninstall removes only the matching
login entry and requests its publisher to stop; it preserves its files. A
running worker will stop when its host signal becomes unsafe. Foreground
`run --confirm` is available for diagnosis and writes an unsafe signal on exit.

### Connect Codex to the media MCP

Register a dedicated connection to the authenticated gateway after the operator
has installed its configuration and protected token file:

```bash
codex mcp add anvil-media -- anvil-serving mcp serve --controller-url http://127.0.0.1:8189/mcp --auth-file /protected/media-token
```

Use the installed gateway URL and token-file path for your machine. The file
contains one UTF-8 token line and uses an absolute path. The bridge checks the
opened file's ownership and permissions before and after reading. On POSIX,
only the current user or root may own it and group/other permissions must be
absent. On macOS, extended ACLs on the file or any ancestor are rejected;
native macOS acceptance remains unverified. On Windows, access must be limited to the current user, SYSTEM, and
Administrators. Missing permission information fails closed.
Its contents are never stored in Codex settings or
process arguments. `--auth-env` remains supported when the client already has
the credential in its environment. The bundled bridge requires Node.js 20+ and
the same Anvil version as the gateway. Start a new Codex session after adding
the connection so its tools can be loaded.

A dedicated media gateway may use an empty `[router]` table with a complete,
authenticated `[server]` media declaration (`auth_env`, `media_principal`,
`media_scopes`, and `media_public_origin`). Set its explicit
`ANVIL_MEDIA_BACKEND_URL` and private state/artifact locations in the managed
deployment. No chat route or chat-model serve is required. An ordinary empty
router without that media declaration remains invalid.

Verify the connection by listing the nine `media_*` tools, validating an
available workflow, submitting a named image workflow, polling its job to
completion, and inspecting the resulting artifact. The image content digest
must match the artifact metadata. Wrong-token and cross-principal access must
be refused. The connection does not grant model installation, worker lifecycle,
or workflow-promotion authority; a cold worker still needs its managed operator
startup path.

The resource-owning host operates ComfyUI through managed `serves` and
controller tools; raw Docker is not the operational path. The caller-facing
media catalog excludes lifecycle and general operator tools. To reconcile the
packaged media-only skill and nine-tool allowlist into selected Hermes
profiles, use `anvil-serving harness sync hermes-media --dry-run` and review
the [Control Plane & Fleet reference](control-plane.md#harness).

See [ADR-0040](../adr/0040-media-gateway-and-controller-authority.md) for the
gateway/controller authority split and
[ADR-0041](../adr/0041-initial-media-workflows-and-policy.md) for the initial
workflow and qualification policy.
