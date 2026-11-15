# GLM reasoning-close GPU marker trace

**Date:** 2026-10-26 local time.

**Decision:** Closed diagnostic inconclusive. Exact operational restoration
passed, while strict JSON functional acceptance remained incomplete. This is a
focused functional diagnostic with `no-promotion`; Ignis remains held.
Synchronized tracing excludes performance claims.

<!-- benchmark-result-card/v1 -->
## Result card

> One request returned the exact answer with one reasoning-close marker at its
> 64-token boundary. Both TP ranks recorded a matching budget store for that
> observed marker, but the frozen analyzer remained `unknown`: the worker trace
> reported 76 committed tokens while the native API returned the first 75 and
> stopped. No second marker reproduced, so the target second-marker
> force-versus-sample question was not exercised.

| Setup | Frozen value |
|---|---|
| Model | `nvidia/GLM-5.3-Flash-FP4-Local@76348ddb2be2e35e39ecfc00e75dc07703f4f3d5`; public served label `served-model-1` |
| Hardware | Two RTX PRO 6000 Blackwell Max-Q GPUs, TP2/DCP2 |
| Recipe | FP4 weights, FP8 KV, configured context 327,680, configured concurrency 4, MTP3 |
| Runtime | vLLM fork `8244d1c44348ae8a5bbfc9f1b607021652283ed4`, unchanged split-top-p patch `0a1276e5e3d9000488e7590cc0957edd709f2152`, bounded actual-GPU marker tracing |
| Image composition | Candidate `789be89f…` directly applies the pinned split-top-p and marker patches to original image `745feb91…`; prior `41c4926b…` is a reference comparison image, not this build's base |
| Executable source | `626f6d8abe8f987a49e09ceab24651684e28ef4f` |
| Probe | C1, 64-token reasoning budget, 512-token completion cap, maximum effort, temperature 0.6, top-p 0.95; zero retries |
| Measurement boundary | Actual-CUDA preweight selftest and sampling gate, then conditional route-derived direct online probe; not a speed test |

| Headline evidence | Result | Boundary |
|---|---:|---|
| Preweight GPU marker selftest | Passed | Two-step synthetic fixture; original logits/cache equal |
| Sampling gate | 8 forced cases and 272 ordinary rows passed | Frozen `upstream-count-v1` policy |
| Candidate requests | 1 | 48 prompt tokens, 75 completion tokens, 64 reasoning tokens; exact answer; no reported anomalies |
| Observed close at index 64 | Both ranks recorded a matching budget store | Supplemental observation; complete trace/native reconciliation failed |
| Accepted second-close attribution | Unknown / not exercised | No second close reproduced; frozen analyzer rejected 76 trace tokens versus 75 API tokens |
| Restoration | Operational pass; functional acceptance incomplete | Exact incumbent model/image/source/template/launch/mounts, engine 0/0, router ready/admitting at zero active requests, no OOM delta; strict JSON failed before and after |

[Authoritative summary](2026-10-26-marker-gpu-trace-evidence/summary.json) ·
[Artifact manifest](2026-10-26-marker-gpu-trace-evidence/artifact-manifest.json) ·
[Evidence index](2026-10-26-marker-gpu-trace-evidence/README.md)

## Frozen question and controls

The prior one-request result retained reasoning-close tokens at zero-based
positions 64 and 126 but no per-output-token force provenance. This experiment
asks whether each accepted second close was budget-forced on its exact
verification row or sampled without a budget force. It does not retroactively
attribute the prior output.

The candidate changes only the bounded marker tracing on top of the preceding
split-top-p diagnostic image. Model, model revision, launch flags, MTP3, GPU
order, mounts, TP2/DCP2, configured C4 capacity and request controls remain
fixed. The actual-CUDA selftest must preserve baseline logits and cache state;
missing events, overflow, rank disagreement, output mismatch or identity drift
fail closed to unknown.

The first CPU-only managed build could not resolve the local preceding-patch
image manifest and stopped before any model or GPU contact. The corrected r2
build pinned the original immutable base image and composed the exact preceding
split-top-p patch plus trace layer directly. Installed source hashes and runtime
attestation bind that composition. The separately retained `41c4926b…` image is
a reference comparison image, not the r2 Docker base.

## Live result

The only allowed probe completed with `stop`, 48 prompt tokens, 75 completion
tokens and 64 reasoning tokens. It returned the exact answer
`9567,1085,10652`, and its response record reported no anomalies. Token 154842
appeared once at zero-based completion index 64; token 154827 stopped the API
response at index 74.

Both TP ranks recorded token 154842 at the corresponding observed trace
position with `budget_store=1`, stored token 154842 and a matching store. The
trace nevertheless reported 76 committed tokens per rank: its first 75 matched
the native response, then token 1654 appeared after the API-visible stop token.
The frozen analyzer therefore returned `unknown` rather than reconciling or
passing the trace. No second 154842 marker appeared in the native response or
trace, so the target second-marker provenance question was not exercised.

Observed stores and accepted-token correlation establish the measured execution
path. They do not establish a counterfactual model result, general model/KV
health, or the cause of an older request without matching internal traces.

The [durable repeated-reasoning-close ticket](https://github.com/fakoli/anvil-serving/blob/main/.tickets/glm-repeated-reasoning-close.md)
retains the nonreproduction and future post-stop reconciliation acceptance
criteria. This run does not show that duplicate closes are resolved.

## Restoration and decision

The managed swap, probe and restoration window completed in 1,278.23 seconds.
The exact incumbent model,
revision, image, captured source/template, launch and mount hashes were restored
with no diagnostic runtime patch. The engine was idle at 0 running / 0 waiting
requests, and the primary route was ready and admitting with zero active
requests. Seventy transition observations recorded at least 65,195,872,256
bytes of available host memory; neither the host OOM-kill counter nor cgroup OOM
counters increased.

Router custody returned to the same running container/image/mount ownership,
with no router restart or file mutation. Mounted router-configuration bytes
were not separately captured before the trial, so this is not a router-byte
parity claim.

Strict JSON failed before and after restoration with the same parser error. The
post-restoration response retained 58 completion tokens; the baseline raw
response is missing because the frozen invocation omitted JSON output and was
not retried. No quality regression is established, but functional restoration
acceptance is incomplete. These two smokes are not a performance population.

No promotion, quality ranking, capacity result or performance claim follows
from this synchronized diagnostic.
