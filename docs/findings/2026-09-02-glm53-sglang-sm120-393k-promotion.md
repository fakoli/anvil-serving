# GLM-5.3-Flash SGLang SM120 393K promotion

- **Date:** 2026-09-02
- **Decision:** human-approved `current` published profile for text, tools,
  image, and OCR at 393,216 tokens and C1
- **Measured hardware:** 2x NVIDIA RTX PRO 6000 Blackwell Max-Q, exclusive
  TP=2 over PCIe without NVLink, Windows 11/Docker Desktop/WSL2
- **Qualification evidence:** [sanitized artifact bundle](2026-09-02-glm53-sglang-sm120-qualification-evidence/README.md)
- **Historical qualification:** [initial 240K selection and restoration](2026-09-02-glm53-sglang-sm120-qualification.md)

<!-- benchmark-result-card/v1 -->
## Result card

> Pinned GLM-5.3-Flash W4A16/NVFP4 on two local RTX PRO 6000 Max-Q cards
> passed the direct, managed, routed, and real-client gates at TP=2/393K/C1
> and became the human-approved published text/image/OCR reference.

| Setup | Qualified value |
|---|---|
| Model | `ormandj/GLM-5.3-Flash-W4A16-NVFP4-K32-Experts-FP8-WO@c3cbb9891b67c741bcbf6b176dd7af9265b069db`; served as `glm53-flash-ormandj-sglang-sm120-tp2-393k-c1-adaptive-mtp` |
| Hardware | 2x RTX PRO 6000 Blackwell Max-Q, exclusive TP=2 over PCIe without NVLink, WSL2 |
| Runtime | digest-pinned SGLang rc14; ModelOpt W4A16/NVFP4; FP8 KV; adaptive EAGLE `[3,5]` |
| Recipe | [managed 393K/C1 recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-ormandj-sglang-sm120-tp2-393k-c1-adaptive-mtp-recipe.toml) |
| Measurement path | warm online direct qualification; managed promotion; authenticated routed and real-client acceptance |
| Contract | 393,216 context, 4,096 maximum output, C1, image/OCR, explicit thinking control, no video |
| Evidence | `functional`, `capacity`, matched `performance`, bounded `quality`, multimodal, endurance, routed/client acceptance, rollback |
| Decision | human-approved published `current`; exact 524K EXL3/DFlash2 service retained as rollback |

| Headline measurement | Local result | Conditions |
|---|---:|---|
| 4K decode | **112.07 tok/s** | 2,969 measured prompt tokens, C1, p50 of three |
| Deep-context decode | **99.79 tok/s** | 304,491 measured prompt tokens at nominal 380K, C1, p50 of three |
| Post-client free VRAM | **2,543 MiB/card** | model-only zero-reserve waiver; both cards; no OOM/restart/crash |
| Boundary | C1; no video | 499K/C4 rejected and 499K/C1 unverified |

**Why it matters:** The selected profile adds about 100K measured prompt-token
headroom over the conservative 240K lane while retaining faster local decode
than the immediate 524K rollback and preserving image/OCR and agent protocols.

**Important caveat:** The standing 3,072 MiB/card reserve is waived only for
this model-only GPU pair. Mid Mod Pi is installed but remains unconverged until
that host receives its own router credential.

Artifact manifest:
[`artifact-manifest.json`](2026-09-02-glm53-sglang-sm120-qualification-evidence/artifact-manifest.json)
· Evidence index:
[`README.md`](2026-09-02-glm53-sglang-sm120-qualification-evidence/README.md)
· Publication summary:
[`promotion-publication-summary.md`](2026-09-02-glm53-sglang-sm120-qualification-evidence/promotion-publication-summary.md)

## Result

The operator waived the campaign's standing 3,072 MiB-per-card reserve for
this model-only GPU pair and authorized promotion of the already-qualified
393,216-token/C1 profile. The waiver changed the recipe classification from
`policy-infeasible` to `verified`; it did not waive functional, capacity,
quality, post-workload, crash, OOM, CUDA, restart, or rollback evidence.

The managed promotion completed after one fix-forward. The selected service is
the pinned ormandj W4A16/NVFP4 checkpoint on the digest-pinned SGLang rc14
runtime, with TP=2, FP8 KV, adaptive EAGLE MTP `[3,5]`, explicit thinking
control, one running request, a 4,096-token output cap, and image/OCR support.
The published `current` label is an evidence decision; operators must inspect
their own live route and serve state rather than infer it from this document.

## Immutable promoted contract

- Model:
  `ormandj/GLM-5.3-Flash-W4A16-NVFP4-K32-Experts-FP8-WO@c3cbb9891b67c741bcbf6b176dd7af9265b069db`
- Runtime:
  `ghcr.io/ormandj/sglang-glm53-flash-sm120@sha256:0c0637959c3931829f05154087bbefd2c50003fb9b2010200ce0ec82f4d71a53`
- Served identity:
  `glm53-flash-ormandj-sglang-sm120-tp2-393k-c1-adaptive-mtp`
- Recipe:
  [`glm53-flash-ormandj-sglang-sm120-tp2-393k-c1-adaptive-mtp-recipe.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-ormandj-sglang-sm120-tp2-393k-c1-adaptive-mtp-recipe.toml)
- Envelope: 393,216 configured tokens, C1, 4,096 maximum output tokens, image
  and OCR input, no video
- Topology: exclusive TP=2 on two equal PCIe GPUs without NVLink

## VRAM policy reclassification

The qualification measured 3,351 MiB free per card after startup and 2,101
MiB per card after the complete direct workload. The original standing reserve
was 3,072 MiB per card, so the first decision correctly classified the profile
as policy-infeasible under that campaign rule. The operator subsequently
declared both cards model-only and set an effective reserve of 0 MiB for this
profile. No physical-capacity result changed.

After managed promotion plus routed and real-client work, each card reported
2,543 MiB free. The service remained healthy, and bounded managed logs plus a
narrow container-state check found no OOM, restart, crash, CUDA failure,
traceback, NCCL error, dead state, or shared-memory residue. The narrow
container-state check was needed because the managed status surface does not
yet expose restart and OOM-kill fields; it did not mutate container state.

The machine-readable reclassification is
[`vram-policy-reclassification.json`](2026-09-02-glm53-sglang-sm120-qualification-evidence/vram-policy-reclassification.json).

## Promotion gate and fix-forward

The first managed transaction reached the promotion preflight but the image
and OCR checks were not runnable: promotion manifests could name the checks
but could not supply their fixture path and expected text. Automatic rollback
restored the retained incumbent service, but the rollback gate encountered the
same manifest-validation defect. The incumbent was then authenticated,
readmitted, and verified before any retry.

The durable fix extends promotion-gate manifests with a manifest-relative
image fixture plus image, OCR, and video expectations, validates each field,
and forwards them to the independent preflight command. Unit coverage proves
path resolution, validation, command forwarding, and the existing
exclusive-to-exclusive dry-run transition. The stable public image fixture is
hash-identical to the one used by the qualification corpus.

The second managed transaction passed:

- thinking-disabled smoke, JSON, needle, tools 20/20, long tool use,
  streaming tools, tool-result continuation, Responses, image, and OCR;
- thinking-enabled smoke, JSON, tools 20/20, tool-result continuation, and
  Responses with required reasoning-channel evidence;
- exact target health and identity, atomic router configuration install,
  authenticated readmission, and post-promotion cache handling.

The routed follow-up repeated the complete functional gate through the
Primary alias and passed independent image and OCR gates through their
dedicated aliases.

## Real-client acceptance

Client catalogs were synchronized through the managed client-sync surface and
verified by a no-change second preview. All affected aliases advertise 393,216
context tokens and a 4,096-token output cap. Existing unrelated provider,
profile, compression, and client settings were preserved.

- Pi 0.84.4 completed an isolated real turn through the Primary alias.
- OpenClaw 2026.9.1-beta.1 completed a real Gateway turn with the exact
  selected provider/model, 393,216 resolved context, and no fallback or
  reroute.
- Hermes 4 completed real one-shot turns for the default, Primary, Secondary,
  and work profiles; each resolved to its configured alias.

An initial Pi harness invocation held SSH standard input open and never issued
a routed request. Closing standard input in the bounded invocation wrapper
made the unchanged client turn complete immediately, so this was classified as
a harness-transport issue rather than a model or route failure.

Pi is also installed on the separate Mid Mod host. Its four Anvil model entries
still advertise the former 524,288-token/8,192-output contract. A managed sync
preview stopped before mutation because that host has no router credential in
its process, persistent user environment, or standard host-local secret files.
No Mid Mod configuration or backup was changed. Credential provisioning is a
separate operator action; copying a credential from another host was not used
as a workaround.

## Rollback

The exact former GLM EXL3 K3 plus DFlash2 K5 524K container was retained in an
exited state for fast rollback. Its recipe, served identity, router
configuration, and client configuration backups remain available through the
managed promotion and host-local rollback bundles. The candidate promotion did
not delete model caches or overwrite the rollback image.

## Decision

Record the 393,216-token/C1 SGLang adaptive-MTP profile as the human-approved
published `current` GLM reference for text, tools, image, and OCR. Retain the
524,288-token EXL3 K3 plus DFlash2 K5 profile as the immediate exact rollback
and the 245,760-token/C1 SGLang profile as a conservative verified fallback.
The 499,712-token variants remain rejected or unverified and are not part of
the promoted contract.
