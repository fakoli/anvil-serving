# Media gateway live validation through Hermes, MCP, and A2A

**Date:** 2026-08-28

**Scope:** exact merged Anvil Serving wheel, isolated authenticated gateway on
Fakoli Dark, managed ComfyUI worker on one RTX 5090, disposable Hermes profile
on Fakoli Mini, c1 image/video execution

**Decision:** exact-build live acceptance passed for the gateway and image
path; the exact Wan2.2 workflow failed bounded perceptual review; both
workflows remain unavailable and `no-promotion`

<!-- benchmark-result-card/v1 -->
## Result card

> The exact merged media gateway completed real Hermes image and video jobs on
> one RTX 5090, passed MCP/A2A/artifact/lifecycle controls, produced two
> prompt-adherent FLUX.2 images, and produced one decodable but visibly failed
> Wan2.2 video.

| Setup | Qualified value |
|---|---|
| Models | FLUX.2 Klein 4B FP8 image workflow; Wan2.2 TI2V 5B video workflow; exact revisions remain in the bundle lock |
| Hardware | one NVIDIA RTX 5090, 32,607 MiB, sm_120; Fakoli Dark gateway, Fakoli Mid Mod resource owner, Fakoli Mini Hermes client |
| Runtime | Anvil Serving `0.35.1` from merge `19320de6`; ComfyUI v0.33.4 at `7a131a3a`; CUDA 13.0; PyTorch 2.13.0+cu130 |
| Recipe | managed `serves.comfyui.toml`; immutable workflow graphs `991b63b8...2e4f` and `bd12b2de...9572` |
| Measurement path | authenticated cross-host gateway; modern MCP, bundled stdio bridge, real Hermes, A2A 1.0, and authenticated artifact origin |
| Contract | image 512×512/four steps; video 512×288/17 frames/eight steps/16 fps; c1; cold approval required |
| Evidence | `functional`, routed/client acceptance, bounded independent `quality`; teardown complete |
| Decision | image path live-accepted but not enabled; video workflow quality-failed; no production cutover or promotion |

| Headline measurement | Local result | Conditions |
|---|---:|---|
| Prompt-adherent FLUX.2 artifacts | 2/2 | one gateway approval-path image and one real-Hermes image; independent visual review |
| Real-Hermes Wan2.2 transport/decode | 1/1 | 117,738-byte H.264 MP4; 512×288; 17 frames; 16 fps; 1.0625 seconds |
| Real-Hermes Wan2.2 perceptual result | fail | severe spatial/frame artifacts; requested subject and clear-sky scene not reliably present |
| Cold-backend negative control | 0 new jobs | Hermes stopped after `backend_unavailable`; worker remained absent |
| Final reservation state | 0 MiB committed | worker absent; 28,511 MiB free inside the protected media envelope |

**Why it matters:** An agent can discover and execute the bounded image/video
API through the same Anvil gateway using the packaged Hermes skill, while the
gateway still fails closed when the backend is cold and preserves one durable
job across MCP and A2A.

**Important caveat:** This was an isolated exact-build deployment, not a
production-router cutover. Two image samples and one very short video are not a
broad quality corpus, and the failed video contact sheet cannot assess temporal
smoothness.

Evidence manifest:
[README.md](2026-08-28-media-gateway-live-validation-evidence/README.md) ·
Publication summary:
[publication-summary.md](2026-08-28-media-gateway-live-validation-evidence/publication-summary.md)

## Outcome and decision

The exact wheel from merge `19320de6b4fa83814ebffc0ac3b6a6ce1234ae7a`
was installed into isolated gateway and lifecycle environments on Fakoli Dark
and the resource controller on Fakoli Mid Mod. Fakoli Mini used the matching
Anvil Serving package and its bundled MCP bridge through a disposable Hermes
profile. The authenticated gateway exposed the expected eight media tools,
modern MCP `2026-07-28`, A2A 1.0, Agent Card, health, and artifact routes.
Wrong-token access returned 401 before dispatch.

The live path passed:

- cold-job persistence and explicit approval before the managed worker start;
- one-use lifecycle approval and identical idempotent replay;
- real Hermes image and video submission through only the packaged media skill;
- full artifact download, byte-range download, missing-artifact 404, signature,
  declared-size, and SHA-256 checks;
- cross-protocol A2A replay of the Hermes video job without a second execution;
- A2A task projection and fail-closed cancellation of an already completed job;
- cold-backend validation that returned `backend_unavailable` and created no
  job; and
- managed teardown to an absent worker, idle GPU baseline, zero reservation
  commitment, and no owner.

The independent adversarial visual review passed both FLUX.2 images for prompt
adherence. It failed the Wan2.2 contact sheet because the requested kite and
minimal clear-sky scene were replaced by severe smearing, chromatic separation,
repeated distorted structures, and a strong horizontal streak. The exact video
workflow is therefore not a promotion candidate in its current form. The image
workflow also remains unavailable: this bounded review does not authorize a
production cutover or route exposure.

## Exact configuration

The deployed wheel was `anvil_serving-0.35.1-py3-none-any.whl`, 1,395,378
bytes, SHA-256
`43643570eae62510f70cadb82895543347a0a729df687025bf355fb380407a73`.
It includes the controller idempotency fix merged at `7920752d` and the
cold-proxied-backend classification fix merged at `19320de6`.

The worker retained the pinned configuration from the prior direct
qualification:

- ComfyUI v0.33.4 at `7a131a3afadc8200120f67f9236311a2c48b7445`;
- FLUX.2 graph digest
  `991b63b8c61ff4322d72b8ae81ef43656f4905ddf4b0709c1989b84cfb8f2e4f`;
- Wan2.2 graph digest
  `bd12b2de2a33bbedc91d7ad6120714f3c3adbd174694ac74d6f0213ecef9572e`;
- immutable model and custom-node revisions from the packaged bundle lock; and
- one 32,607 MiB RTX 5090 with a 4,096 MiB protected reserve and a 28,511 MiB
  on-demand media envelope.

No raw ComfyUI graph, node ID, model filename, backend path, alternate
generator, or fallback was caller-selectable.

## Method

1. Build and hash the wheel from the exact merged source revision, then install
   that artifact into isolated gateway, lifecycle, and resource-controller
   environments.
2. Start authenticated candidate services without replacing the production
   router or controller. Verify health, auth-before-dispatch, Agent Card, MCP
   negotiation, and the eight-tool media catalog.
3. Submit an image while ComfyUI is cold. Verify a durable
   `awaiting_approval` job and an exact `media_worker_prepare` action, execute
   the user-authorized action through the lifecycle controller, replay it with
   the same idempotency key, and then complete the job.
4. Configure a disposable Hermes profile with environment-variable credential
   references and the exact media-tool allowlist. Generate one image and one
   video through the packaged skill with fallback disabled.
5. Replay the exact video request through A2A, poll the projected task, and
   attempt cancellation after completion.
6. Retrieve every artifact with a separate standard-library client and verify
   full, range, missing, signature, size, and digest behavior. Decode the video
   independently with `ffprobe`. Have the separately administered adversarial
   reviewer inspect both images and a video contact sheet.
7. Tear the worker down through the managed lifecycle action. With the backend
   cold, ask Hermes to validate another image request and require it to stop
   before `media_workflow_run`. Confirm the durable job count is unchanged.
8. Remove the disposable Hermes profile and isolated candidate runtimes, then
   verify candidate ports are closed and the original Hermes default has no MCP
   server configured.

## Results

### Artifacts and independent review

| Path | Artifact | SHA-256 | Independent disposition |
|---|---:|---|---|
| Cold approval image | 248,804-byte PNG | `2d7628b8...99f4c` | pass; glossy red ceramic robot, one blue flower, watering action, and cream background were clear; minor nozzle-source ambiguity |
| Real-Hermes image | 397,383-byte PNG | `226cf4c2...b2ec6` | pass with caveats; brass observatory robot, teal lantern, stars, and midnight-blue sky were clear; small signature-like mark and slightly ambiguous lantern geometry |
| Real-Hermes video | 117,738-byte MP4 | `083d8a54...c370f` | functional/decode pass, perceptual fail; subject and clean sky were not reliably identifiable and severe spatial artifacts dominated the frames |

The video decoded as H.264 at 512×288, 17 frames, 16 fps, and 1.0625 seconds.
The contact-sheet review cannot establish left-to-right motion, temporal
smoothness, or a static camera; those remain unassessed. The visible spatial
failure is sufficient to block this exact workflow version.

### Protocol and lifecycle controls

| Control | Result |
|---|---|
| Modern MCP / stdio bridge | pass; protocol `2026-07-28`, eight scoped tools, real Hermes calls |
| A2A | pass; exact video request replayed the MCP-created job; task projection matched; completed-task cancellation returned `TASK_NOT_CANCELABLE` |
| Artifact origin | pass; full 200, range 206 with exact content range, missing 404, signatures, sizes, and hashes |
| Cold approval | pass; worker stayed absent before explicit approval; identical approval replay did not start a second worker |
| Cold backend | pass; `backend_unavailable`, no run call, durable job count stayed at three |
| Teardown | pass; worker absent, GPU 448/32,607 MiB, reservation committed 0 MiB/free 28,511 MiB, no owner |
| Client restoration | pass; disposable profile/alias removed, original default active, no default MCP server |

## Failures and caveats

- The exact Wan2.2 workflow failed bounded prompt adherence and spatial quality.
  Longer clips, other prompts, and temporal scoring were not attempted after
  that failure.
- A legacy Windows PowerShell artifact client stalled after a partial transfer.
  Gateway logs showed client connection resets while the service remained
  healthy; a separate standard-library client immediately completed the same
  authenticated transfer and all framing/hash checks. This is retained as a
  client-harness caveat, not hidden as a gateway pass.
- An initial administrative attempt showed that setting `HERMES_PROFILE` alone
  does not scope Hermes configuration. The transient default MCP entry was
  immediately removed and verified absent before testing resumed with explicit
  profile selection. Final client state matches the pre-test default.
- The validation did not rebuild or replace the production router/controller.
  Exact wheel parity and live behavior are proven only for the isolated
  candidate deployment.
- Generated binaries were transient validation artifacts. The public packet
  retains sizes, hashes, decode metadata, and the independent review record,
  but not the binary image/video files themselves.

## What to test next

1. Replace or revise the Wan2.2 workflow as a new immutable version, then run a
   multi-prompt temporal and prompt-adherence corpus before another live gate.
2. Expand FLUX.2 review beyond two samples, including text rendering, hands,
   multi-object counting, negative prompts if supported by a new descriptor,
   and repeatability across seeds.
3. If image enablement is desired, perform a reviewed production
   router/controller cutover with exact endpoint identity, rollback, and a
   fresh real-Hermes smoke. Do not couple that image decision to the failed
   video candidate.
4. Add a product-owned cross-platform artifact download probe so legacy
   PowerShell client behavior cannot masquerade as a gateway framing defect.

## Evidence boundary

This finding proves that the exact merged package can run the designed
cross-host media control plane and real Hermes client path, maintain
cross-protocol idempotency, serve authenticated artifacts correctly, fail
closed while cold, and restore the managed resource state. It provides two
bounded positive FLUX.2 perceptual samples and one bounded negative Wan2.2
sample.

It does not prove production-router deployment, broad image quality, temporal
video quality, concurrency above one, long-video stability, or authorization
to expose either workflow. Both public descriptors remain unavailable and no
model, workflow, route, or client default was promoted.
