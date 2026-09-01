# FLUX.2 Klein 4B

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** production-enabled named image-generation workflow;
      `available=true` and `promoted=false` describe the published workflow
      decision, not a model-family promotion or live worker state.
    - **Selected or best-qualified configuration:** FLUX.2 Klein 4B FP8 in
      ComfyUI v0.33.4, workflow `image.flux2-klein-4b-fp8-v1`, concurrency
      one, and fixed `draft`, `standard`, and `high` profiles.
    - **Measured hardware:** one NVIDIA GeForce RTX 5090 with 32,607 MiB in a
      Windows 11, Docker Desktop, and WSL2 media lane.
    - **Evidence:** direct functional/capacity qualification, exact-build and
      lifecycle acceptance, real Hermes/MCP calls, and six of eight strict
      bounded visual reviews passed.
    - **Decision:** enable only the exact named workflow at 512×512, 768×768,
      and 1024×1024, all with four steps and c1; reject caller-supplied graphs,
      models, dimensions, and substitutions.
    - **Important limitation:** concurrency above one, broad comparative image
      quality, seed repeatability, monotonic quality by tier, and a normal
      cold-start latency distribution were **not tested**.
    - **Review dates:** retained evidence through 2026-08-28; dossier-format
      review 2026-08-31.

### Review narrative

#### 2026-08-28 — direct qualification

The exact managed workflow produced a decodable 258,472-byte PNG in 9.859
seconds and peaked at 12,919 MiB from a 943 MiB worker baseline. That initial
cold c1 run established functional and capacity evidence only; it did not by
itself establish routed behavior, broad visual quality, or a steady-state
latency target.

#### 2026-08-28 — gateway and exact-build validation

An isolated exact-build pass added two prompt-adherent PNGs and complete
artifact controls. The live gateway pass then exercised cold approval,
same-job resume, authenticated artifact delivery, real Hermes requests, and
managed teardown while preserving the unrelated Qwen service.

#### 2026-08-28 — production enablement and fix-forward closure

The production pass added one warm request per fixed profile and five cold
`draft` regressions. Six of eight images passed strict independent review; two
technically valid drafts failed origami/material fidelity and exact bird count.
The controller, approval, cleanup, auth, and resume defects found along that
path were fixed forward with retained regression evidence before the exact
workflow became available.

## Immutable identity

`black-forest-labs/FLUX.2-klein-4b-fp8` revision
`5b4408e59397a4a37ccb46afe426d8ed86379441`, combined with the Qwen3 4B
encoder and FLUX.2 VAE from `Comfy-Org/flux2-klein` revision
`5f526678002e43af5551dadb73ce2e8c91b43afe`. Exact file sizes and SHA-256
identities are recorded in the
[workflow bundle lock](https://github.com/fakoli/anvil-serving/blob/main/configs/media/workflows/bundle.lock.json).

The immutable workflow descriptor records graph digest
`991b63b8c61ff4322d72b8ae81ef43656f4905ddf4b0709c1989b84cfb8f2e4f`.

## Tested hardware and topology

One RTX 5090, 32,607 MiB, in a Windows 11 / Docker Desktop / WSL2 media lane.
Direct qualification, isolated exact-build acceptance, and the production
cross-host path with Fakoli Dark gateway, Fakoli Mid Mod resource ownership,
and real Hermes on Fakoli Mini were tested. The production router and bounded
controllers changed; the unrelated Qwen service remained in place.

## Engine, quantization, KV, context, and concurrency recipe

### Exact workflow and runtime

ComfyUI v0.33.4 at `7a131a3a`, CUDA 13.0, PyTorch 2.13.0+cu130, a
digest-pinned base, curated node pins, and workflow
[`image.flux2-klein-4b-fp8-v1`](https://github.com/fakoli/anvil-serving/blob/main/configs/media/workflows/image.flux2-klein-4b-fp8-v1.json)
at graph digest `991b63b8...2e4f`, c1. The server-owned profiles are `draft`
512×512, `standard` 768×768, and `high` 1024×1024, all four steps. KV cache
and language-model context are not applicable to this diffusion workflow.

### Reproduction boundary

The managed lifecycle is retained in
[`examples/fakoli-dark/serves.comfyui.toml`](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/serves.comfyui.toml)
and the exact models, node revisions, container identity, and workflow source
are retained in the bundle lock. The initial direct qualification prompt text
was intentionally **not retained** publicly; only its SHA-256 digest was
published. Later production-quality prompts and dispositions are retained in
the dated finding.

## Evidence by measurement class

### Functional and capacity

`functional`, `capacity`: the direct run produced a decodable 258,472-byte PNG
in 9.859 seconds, peak 12,919 MiB from a 943 MiB worker baseline, with max
queue running one and pending zero. The isolated exact-build pass added two
prompt-adherent PNGs and complete artifact controls.

### Routed client and bounded quality

The production pass added one real-Hermes warm request per fixed profile and
five cold `draft` regressions. Six of eight samples passed strict independent
review. Two technically valid draft results failed prompt adherence on
origami/material fidelity and exact bird count. Warm gateway E2E measured
1.352/1.242/1.650 seconds for draft/standard/high.

### Cold lifecycle

The 2,045.626-second cold E2E includes multiple fix-forward deployment
failures and is not steady-state latency. The final server-issued exact-resume
regression measured 908.936 seconds E2E and 0.087 seconds generation.

## Decision and promotion state

### Exact workflow availability

Enable the exact workflow at the three fixed c1 profiles. This is workflow
availability, not a model-family promotion or a claim of broad image quality.
Caller-supplied graphs, models, node paths, dimensions, and alternate
generators remain unavailable.

### Promotion boundary

`available=true`, `promoted=false` is the published decision. It does not
authorize another workflow, route, model, host, or live worker mutation.

## Failures and gotchas

### Operational fix-forward history

The first qualification attempts exposed missing native build dependencies in
the derived runtime. The production cold path later exposed controller
timeout, Buildx, read-only client state, no-router scoping, approval recovery,
descendant cleanup, and nested receipt defects; each was fixed forward with
regression coverage. Later fixes moved live fleet probes into the router
runtime, changed Hermes MCP auth to the least-authority router token, split
the router-to-lifecycle and lifecycle-to-resource credentials, and made skill
`1.0.4` copy the server-issued exact same-job resume bundle unchanged, keep
nonterminal states in-turn, and preserve the current request language.

Post-run revision `5ea1edc` and skill `1.0.5` then closed the profileless
empty-profile boundary, the five-input/seven-field replay distinction, and the
malformed-bundle cancellation exception without changing the measured image
runtime.

### Quality and generalization limits

One cold c1 run is not a throughput distribution or latency target. Six
strict passes and two retained draft failures do not establish broad text
rendering, hands, counting, material fidelity, seed repeatability, or
monotonic quality by tier. Those properties and concurrency above one were
**not tested**.

## Dated run history

- [2026-08-28 Hermes image-quality production enablement](../../findings/2026-08-28-hermes-image-quality-production.md)
- [2026-08-28 media gateway live validation](../../findings/2026-08-28-media-gateway-live-validation.md)
- [2026-08-28 ComfyUI media qualification](../../findings/2026-08-28-comfyui-media-qualification.md)
- [2026-08-27 feasibility screen](../../findings/2026-08-27-media-gateway-comfyui-evidence/feasibility-result.md)
