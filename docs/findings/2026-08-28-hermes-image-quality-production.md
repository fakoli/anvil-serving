# Hermes image-quality profiles and production enablement

**Date:** 2026-08-28

**Scope:** production Anvil media gateway on Fakoli Dark, managed on-demand
ComfyUI worker on one RTX 5090 in Fakoli Mid Mod, and the real Hermes client on
Fakoli Mini; c1 text-to-image execution

**Decision:** the exact FLUX.2 Klein workflow is production-enabled through
three fixed quality profiles; `available=true`, `promoted=false`. The Wan2.2
video workflow remains unavailable with `quality_failed`; no fallback exists.

<!-- benchmark-result-card/v1 -->
## Result card

> Real Hermes produced four independently reviewed FLUX.2 images through the
> authenticated MCP gateway, including all three fixed quality profiles and a
> fully cold approval/build/resume path. Every image passed its bounded visual
> gate; video remains fail-closed.

| Setup | Qualified value |
|---|---|
| Model | FLUX.2 Klein 4B FP8 `5b4408e5`; Qwen3 4B encoder and FLUX.2 VAE `5f526678`; immutable graph `991b63b8...2e4f` |
| Hardware | one NVIDIA RTX 5090, 32,607 MiB, sm_120; Fakoli Dark gateway, Fakoli Mid Mod resource owner, Fakoli Mini Hermes client |
| Runtime | Anvil Serving `0.36.0`, executable fix-forward line through `57910c7`; ComfyUI v0.33.4 at `7a131a3a`; CUDA 13.0; PyTorch 2.13.0+cu130 |
| Managed recipe | `image.flux2-klein-4b-fp8-v1`; lifecycle-owned `serves.comfyui.toml`; worker absent when idle |
| Measurement path | real Hermes natural-language turn → exact eight-tool skill → authenticated MCP gateway → bounded controller approval → managed ComfyUI → authenticated artifact origin |
| Profiles | `draft` 512×512, `standard` 768×768, `high` 1024×1024; four steps each; prompt and optional seed are caller-controlled; c1 |
| Evidence | `functional`, routed/client acceptance, bounded `capacity`, bounded independent `quality`; production cutover and teardown |
| Decision | image workflow available at the three exact profiles; arbitrary graph/size/model input rejected; video remains `quality_failed` |

| Headline measurement | Local result | Conditions |
|---|---:|---|
| Independent image review | 4/4 pass | one warm image per profile plus one cold `draft`; two prompts; single sample per cell |
| Warm gateway E2E | 1.352 / 1.242 / 1.650 s | `draft` / `standard` / `high`; one natural Hermes request per profile |
| Warm generation phase | 0.101 / 0.128 / 0.189 s | gateway phase telemetry for the same three requests; not caller wall time |
| Warm Hermes caller wall | 38.354 / 24.420 / 41.962 s | complete agent turns, including model reasoning and tool use; one request per profile |
| Cold lifecycle E2E | 2,045.626 s | one `draft`; includes repeated fix-forward failures, approval/build/resume, queue, and generation; not steady-state latency |
| Cold retry caller wall | 47.788 s | identical Hermes request after approval/start completed; 363,074-byte PNG |

**Why it matters:** Hermes can now satisfy an ordinary image request without
learning ComfyUI graphs or infrastructure details. The user selects a small
quality vocabulary, while the gateway retains workflow identity, lifecycle
authority, authentication, idempotency, artifact ownership, and latency
telemetry.

**Important caveat:** Four images over two prompts establish only a bounded
production smoke. They do not establish broad image quality, text rendering,
hands, counting, or monotonic visual improvement between tiers. The cold E2E
number intentionally includes multiple defects found and repaired during the
same run and must not be presented as normal generation latency.

Evidence manifest:
[README.md](2026-08-28-hermes-image-quality-production-evidence/README.md) ·
Publication summary:
[publication-summary.md](2026-08-28-hermes-image-quality-production-evidence/publication-summary.md)

## Product contract

The production gateway exposes the same eight bounded media tools through MCP
and A2A. Hermes receives only those tools and an environment-reference-only
configuration. Its media skill completes the generation turn, reports an
approval requirement when the worker is cold, and retries the identical
idempotency key after approval. It does not diagnose infrastructure, submit a
raw ComfyUI graph, choose a model filename, or silently substitute another
workflow.

Image requests accept a prompt, an optional seed, and one of three profiles:

| Profile | Width × height | Steps | Intended use |
|---|---:|---:|---|
| `draft` | 512×512 | 4 | fastest bounded preview |
| `standard` | 768×768 | 4 | default balanced result |
| `high` | 1024×1024 | 4 | largest qualified output |

The profile, dimensions, graph, runtime, model assets, node set, and output
format are server-owned. A direct width override failed with
`quality_profile_parameter_override` before execution. A video request failed
with `quality_failed` and did not fall back to image or another generator.

Native MCP image results are bounded to 6 MiB and the framed bridge to 10 MiB.
The largest observed result was 1,495,385 bytes. Video artifacts are
resource-only even after a future workflow passes its independent quality
gate.

## Method

1. Rotate the production gateway credential, distribute only environment
   references, prove the current token returns 200, and prove retired and
   incorrect tokens return 401 before dispatch.
2. Deploy the image-enabled router and the bounded lifecycle/resource
   controllers without replacing or restarting the unrelated Qwen service.
3. Verify modern MCP discovery exposes exactly eight media tools and that the
   Hermes profile has the exact matching allowlist with fallback disabled.
4. Ask real Hermes for the same cinematic observatory scene once at each
   quality profile. Retain caller wall time, gateway phase telemetry, artifact
   size and digest, and an independent visual disposition.
5. Tear the worker down, submit a new `draft` request through Hermes, approve
   its declared lifecycle action, build/start the managed worker, and replay
   the exact request and idempotency key to completion.
6. Confirm teardown through the managed controller, then verify the worker is
   absent, the reservation has no owner or committed memory, and the resource
   controller has no orphan descendants.
7. Exercise negative controls for authentication, arbitrary dimensions, video
   quality state, duplicate approval, and idempotent replay.

## Warm profile results

All three warm requests used the prompt “A cinematic glass observatory on a
mossy cliff, a copper telescope visible inside, blue hour, warm interior
lights.” The images were distinct, decodable PNGs at their exact declared
dimensions.

| Profile | Dimensions | Bytes | SHA-256 | Gateway E2E | Generation | Hermes wall | Review |
|---|---:|---:|---|---:|---:|---:|---|
| `draft` | 512×512 | 321,710 | `67867fe5...cd56` | 1.352 s | 0.101 s | 38.354 s | pass; complete scene, minor simplified geometry |
| `standard` | 768×768 | 766,691 | `6d88d55d...d063` | 1.242 s | 0.128 s | 24.420 s | pass; strong adherence, minor roof/railing perspective |
| `high` | 1024×1024 | 1,495,385 | `7c3874ca...2a7e` | 1.650 s | 0.189 s | 41.962 s | pass; detailed and coherent, mildly ambiguous glass extension |

These are single observations. The non-monotonic caller wall times reflect a
complete agent turn, not just GPU work, and do not rank the profiles. The very
short generation phases are warm/cached gateway observations and do not
replace the earlier cold direct qualification.

## Cold approval, build, resume, and teardown

The cold request asked for “A small copper observatory dome emerging from a
foggy pine forest at dawn” at `draft` with a fixed seed. It entered the durable
approval state without starting the worker. The approved managed start built
the pinned worker, passed its storage and `/system_stats` checks, and gave the
job lifecycle ownership. Hermes then replayed the same request and idempotency
key; the existing job advanced to completion instead of creating another
execution.

The 363,074-byte, 512×512 PNG had SHA-256
`ebb6f351c07c2109bea995ffb7dd03e48c40667b53d2b5e0aa8bc1b056c1b3ce`.
Independent review passed it: the copper-toned dome, layered pine forest, fog,
and dawn light were clear and coherent. The dome was slightly pink from the
sunrise and somewhat pavilion-like without an obvious telescope slit; distant
trees repeated, but no major rendering defect was present.

Recorded job phases were 0.061 seconds submission, 2,035.526 seconds from
acceptance to queued, 10.0 seconds queued, 0.1 seconds generation, and
2,045.626 seconds E2E. The approval-wait instrument recorded 151.78 seconds of
the final approval segment. The much larger accepted-to-queued interval spans
the complete fix-forward sequence below, so it is an incident/lifecycle
measurement rather than a normal cold-start service objective.

Managed teardown then removed the worker. The final resource view showed the
worker absent, zero media memory committed, 28,511 MiB free inside the media
envelope, no lifecycle owner, and no orphan build/start process.

## Defects fixed forward during the cold run

The live path exposed seven actionable defects. Each was repaired in the
product surface with regression coverage before the same user request was
allowed to complete:

1. The parent controller had a 30-second child timeout while the child allowed
   300 seconds. Child timeout plus a bounded margin is now propagated.
2. The controller image did not include Docker Buildx. The pinned plugin is
   now part of the managed controller image.
3. A resource-scoped `serves up` attempted to co-locate the router. Controller
   starts now use the explicit no-router path.
4. A failed approved start stranded the job without a fresh approval path.
   The same job/key may now mint a new one-use approval after failure, while an
   in-flight start cannot mint a competing approval.
5. Buildx tried to write its client state under a read-only home. The
   controller uses writable tmpfs-backed Docker configuration state.
6. A five-minute cold-build timeout was too short and killed only the direct
   child, leaving Compose/Buildx descendants. The bounded timeout is now 1,800
   seconds, and timeout handling terminates the whole process tree on POSIX and
   Windows.
7. A successful remote apply nested `applied=true`, but the lifecycle receipt
   reported false. Apply status is now normalized from the controller
   envelope.

The final behavior is a fix-forward result, not a waiver of those failures.

## Safety and negative controls

| Control | Result |
|---|---|
| Current credential | authenticated health/model request passed |
| Retired or incorrect credential | 401 before dispatch |
| Hermes tool scope | exactly eight media tools; environment references only |
| Caller width override | rejected before execution |
| Video request | `quality_failed`; no fallback or execution |
| Cold worker before approval | remained absent |
| Approval replay | one durable job and one owner; no competing start |
| Post-run teardown | worker absent; reservation reclaimed; no orphan descendants |
| Existing Qwen service | retained throughout the router/controller cutover |

## Evidence boundary

This finding establishes that the exact production image contract works from
real Hermes through MCP, the router gateway, lifecycle approval, an on-demand
ComfyUI worker, and authenticated artifact return. It also establishes exact
profile dimensions, telemetry fields, fail-closed overrides, idempotent cold
resume, and teardown on one RTX 5090.

It does not establish concurrency above one, broad or comparative image
quality, monotonic improvement by profile, arbitrary aspect ratios, a cold
start latency objective, or a usable video generator. The Wan2.2 workflow
remains `quality_failed` and unavailable until a new immutable workflow version
passes a separate temporal and perceptual corpus.
