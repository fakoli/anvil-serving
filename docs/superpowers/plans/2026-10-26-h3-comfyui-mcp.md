# H3 ComfyUI MCP integration implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task. Native execution follows the user's already authorized persistent goal.

**Goal:** Execute the verified local Cobijada H3 generation and guided 2x upscale recipes through Anvil Media MCP, publish reproducible evidence, and identify useful companion pipelines for a three-host podcast.

**Architecture:** Reuse the existing immutable named-workflow registry, principal-scoped artifacts, durable jobs, lifecycle transactions and MCP tools. Resolve artifact parameters in the shared operation before submission, then register the tested graphs. Keep machine storage, deployment inputs and unsanitized evidence in operator-owned storage.

**Tech stack:** Existing stdlib Python Anvil Serving, ComfyUI, Docker Desktop Linux containers, pinned safetensors and reviewed H3 upsampler nodes. No new production dependency.

**Spec:** The user's active goal; concrete requirements and acceptance criteria below.

## Requirements and constraints

- Preserve the verified generation and upscale models, recipes and outputs.
- Local inference only; preserve verification and Windows memory safeguards.
- Reuse named workflows and opaque artifact IDs; callers cannot supply paths, graphs, model names or backend routes.
- Generation defaults: 864x480, 124 frames, 24 fps, 20 res_multistep/simple steps, caller prompt and seed. The initial bounded profile retains the measured envelope; broader settings require separate evidence.
- Guided upscaling: source-owned MP4, target 1728x960 at source FPS, denoise 0.1, two Euler refinement steps, 25,000-token tiles, original soundtrack. Describe audio re-encoding versus bitstream preservation accurately.
- Public descriptors remain unavailable templates; private activation records the user's visual acceptance and exact tested revisions. Underlying model licensing remains separately documented.
- Accept and report progress, failures, cancellation and output artifacts through existing MCP contracts. Preserve cold-start preview/approval transactions and idempotency.
- Do not imply that H3 character LoRA training, recorded-speech conditioning or long-form identity consistency has been demonstrated.
- Publish a sanitized finding and full benchmark navigation matrix. Never commit credentials, operator identity, personal paths, raw private media or weights.
- Research recommendations must include current original sources, multiple relevant benchmarks/usage evidence, recipes and quantitative hardware fit. Installing new models solely to research them is outside scope.

## Review focus

- Wrong-owner, expired, changed or oversized input artifacts fail before upload or prompt submission.
- Artifact filename handling cannot inject multipart headers or escape ComfyUI's input directory.
- Idempotent replay and ambiguous submission recovery never submit a second generation.
- Startup and cleanup operate only the declared worker; incompatible GPU reservation budgets are explicit.
- Public claims distinguish original isolated trials, MCP integration measurements and unresolved perceptual/production limits.

## Tasks

### Task 1: Resolve owned artifacts in the shared submission path

**Files:** `anvil_serving/media/operations.py`, `contracts.py`, `workflows.py`, `comfyui.py`; focused media tests.

**Interfaces:** Existing artifact parameters remain opaque IDs. Optional descriptor-owned `media_types` constrains accepted MIME types. `MediaOperations._submit_rendered` stages verified owned bytes and substitutes only declared bindings in a copied graph; the original request digest and public job remain path-free.

- [ ] Establish the existing media-test baseline with a working isolated interpreter; record environment defects.
- [ ] Write failing tests for MP4 handoff, MIME/ownership/expiry/size/hash refusal, safe filenames and replay.
- [ ] Implement the smallest shared artifact resolution and filename validation changes.
- [ ] Run the focused tests and existing media/MCP regressions.

### Task 2: Register and deploy the exact tested H3 workflows

**Files:** `configs/media/workflows/video.h3-cobijada-t2v-v1.*`, `video.h3-cobijada-upscale-v1.*`, matching packaged workflows and lock metadata; private worker/controller/gateway configuration.

- [ ] Derive immutable graph descriptors from retained trial graphs and pinned asset manifests; preserve the tested settings.
- [ ] Add graph/descriptor tests and the documented MCP input/output contract.
- [ ] Reuse managed lifecycle verbs with a pinned H3 runtime, verified existing model mounts and reviewed custom nodes. Resolve the old 4-GiB reservation mismatch explicitly.
- [ ] Configure the local MCP gateway and lifecycle controller against the same durable state, with protected secret references and no exposure beyond the intended local surface.
- [ ] Validate compatibility and model readiness before generation.

### Task 3: Prove a cold MCP run and publish the evidence

**Files:** Dated private campaign evidence; sanitized public finding, artifact set, model dossier, run catalog and measured GPU page.

- [ ] Capture starting state, run generation through actual MCP from a stopped worker, then upscale its owned video artifact through MCP.
- [ ] Verify decoding, dimensions/frame count/FPS/audio, idempotency, observable job states, cancellation behavior and scoped teardown. Record GPU, container and Windows memory, and distinguish engine from observation time.
- [ ] Retain and sanitize the native evidence, failures and restoration receipts; derive public repeated metrics from retained JSON.
- [ ] Apply the benchmark publication matrix and required documentation/code checks, review the changes, create/attach a PR and publish through the existing documentation owner. Verify the published surface or record its specific external blocker.

### Task 4: Recommend the next useful pipeline additions

**Files:** Dated source registry, concise research brief and publication-safe recommendations.

- [ ] Inventory current sources and existing recipes for H3 controls/adapters, character training, reference/studio construction, recorded speech/lip sync, restoration and assembly.
- [ ] Verify promising candidates with original code/model cards, multiple relevant evaluation sources and real-project examples where available; explicitly record unavailable evidence.
- [ ] Size weight residency plus workspaces/offload against the measured single-GPU and host-memory envelope. Rank a short list as worth testing, watch or skip, with reusable recipes and one informative next experiment each.

## Completion audit

The goal remains active until actual MCP execution, durable operating instructions, publication (or a specific external publication blocker), and the source-backed shortlist are all evidenced. Passing unit tests or preparing graphs alone is incomplete.
