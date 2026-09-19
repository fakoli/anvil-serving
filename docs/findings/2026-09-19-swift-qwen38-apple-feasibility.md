# Swift / stock Qwen3.8-27B Apple artifact feasibility stop

**Date:** 2026-09-19

**Scope:** Apple M4 Max 40-core GPU / 48 GiB unified-memory workstation,
macOS 27.0 (build 26A428). This is a read-only artifact and runtime-contract
feasibility screen. It is not a model evaluation, load, preflight, latency
benchmark, or quality comparison.

**Decision:** `no-promotion`; stop before any selected GGUF download or trial.
The available disk screen fails even its optimistic arithmetic bound. The
separate memory screen is unresolved, and the candidate runtime contract does
not yet enforce a loader peak or RAM-plus-swap containment bound.

<!-- benchmark-result-card/v1 -->
## Result card

> The Swift and stock Qwen3.8-27B GGUF candidates were not downloaded or run
> on the measured Apple lane: every selected uncached artifact pair exceeds the
> available-disk policy before temporary-file requirements, and safe memory
> containment remains unavailable.

| Setup | Recorded value |
|---|---|
| Candidates | Swift and stock Qwen3.8-27B GGUF, Q6_K and Q4_K_M artifact pairs |
| Hardware | Apple M4 Max 40-core GPU, 48 GiB unified memory; macOS 27.0 build 26A428 |
| Measurement path | Read-only disk and runtime-contract inspection; no model process, endpoint, route, or client |
| Disk state | 26,295,853,056 B (24.4899 GiB) free; swap 0 B at capture |
| Memory policy | 32 GiB nominal candidate envelope; 16 GiB system/voice reserve |
| Disk policy | 10 GiB disk reserve plus 1 GiB runtime/evidence allowance |
| Evidence | feasibility-only; no quality, functional, capacity, or latency evidence |
| Decision | investigation stopped; `no-promotion`; `promoted=false` |

| Disk candidate including F16 projector | Required bytes | Shortfall before temporary files |
|---|---:|---:|
| Swift Q6_K | 23,812,014,432 | 9,327,321,440 |
| Stock Q6_K | 24,788,172,736 | 10,303,479,744 |
| Swift Q4_K_M | 18,951,987,552 | 4,467,294,560 |
| Stock Q4_K_M | 18,370,006,976 | 3,885,313,984 |

**Why it matters:** no selected artifact can be acquired while retaining the
declared disk reserve and allowance, so a comparison would require a separate
storage decision first.

**Important caveat:** this screen does not establish that either model is
impossible on Apple hardware. It records the current storage-policy stop and
the missing enforced loader/RAM/swap limit; it makes no quality, speed, vision,
or model-rejection claim.

Evidence: [artifact manifest](2026-09-19-swift-qwen38-apple-feasibility-evidence/artifact-manifest.json) ·
[evidence index](2026-09-19-swift-qwen38-apple-feasibility-evidence/README.md) ·
[feasibility input](2026-09-19-swift-qwen38-apple-feasibility-evidence/feasibility-input-v1.json) ·
[feasibility result](2026-09-19-swift-qwen38-apple-feasibility-evidence/feasibility-result-v1.json) ·
[disk screen](2026-09-19-swift-qwen38-apple-feasibility-evidence/disk-feasibility-v1.json) ·
[runtime contract](2026-09-19-swift-qwen38-apple-feasibility-evidence/managed-runtime-contract.json) ·
[identity summary](2026-09-19-swift-qwen38-apple-feasibility-evidence/identity-summary.json) ·
[summary](2026-09-19-swift-qwen38-apple-feasibility-evidence/summary.json).

## Outcome and decision

The uncached disk screen stopped all four selected artifacts before a download,
temporary-file calculation, or model launch. The stated shortfalls include the
selected model body and its family-matched F16 projector, then apply the 10 GiB
disk reserve and 1 GiB runtime/evidence allowance. They are therefore an
optimistic stop: temp files, caches, and download retries would add demand.

No weights, models, launchd entries, routes, client settings, or direct aliases
changed. The voice baseline remained protected: STT/TTS/voice health was 200,
but no Talk-latency or candidate-latency measurement was collected. This Apple
machine is a same-host local-audio investigation lane, not the model-free
Companion Node reference topology.

The memory calculator is unresolved for every candidate. The 32 GiB nominal
candidate envelope retains a 16 GiB system/voice reserve, but cannot be
evaluated because loader peak, Metal allocation behavior, temporary memory, and
selected context are unknown. The current native runtime
validator rejects native llama.cpp and permits MLX only, while its static
`memory_mib` admission is not an enforced loader/RAM-plus-swap cap. MLX's
[`mx.set_memory_limit`](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.set_memory_limit.html)
is a guideline that can use swap, so it does not close the containment gap.

## Exact configuration

The public identity screen pins the Swift and stock source/quantizer families
and records their bytes and hashes in the linked identity summary. Both public
GGUF cards cite llama.cpp b10896, pinned to
`fa6769818708afd9807b22183ccda112fd563427`; this is a conversion-runtime
prior, not proof of identical packaging: imatrix/per-tensor layouts and MTP
packaging differ. The managed built binary and effective runtime configuration
remain unverified, so no execution or behavioral attribution is claimed.

## Method

The screen compared available bytes with each selected model-plus-matching-F16
projector pair, preserving the 10 GiB reserve and 1 GiB allowance. It made no
temporary-space estimate and did not treat the four outcomes as a performance
matrix. Artifact metadata and the shared b10896 claim are external priors only.

## Results

All four selected pairs fail the optimistic disk policy bound. The memory
outcome remains `unresolved`, rather than pass or fail, because its load-bearing
peak and runtime limits are absent. The managed-runtime contract blocks a safe
trial: native exact-artifact pull/recipe identity and enforced loader
peak/RAM/swap/Metal containment are not yet product-supported.

## Failures and caveats

- **Storage:** all selected artifacts are uncached and fail before temporary
  files; this is neither cleanup approval nor a request to remove cached data.
- **Memory:** no peak/RAM/swap cap is enforced, and Metal containment is
  missing. Candidate operations requires enforced containment when peak is
  unknown.
- **Evidence scope:** workloads, live functional/quality/timing/capacity,
  vision, soak, and repeated attempts are missing. There is no quality ranking
  or candidate latency evidence.
- **Attribution:** shared b10896 is only a prior; conversion, imatrix,
  per-tensor, and MTP differences prevent a packaging-equivalence claim.

## What to test next

First complete the scoped product gap for native exact-artifact pull/recipe
identity and enforced loader peak/RAM/swap plus Metal containment. A future
trial also needs a separately approved storage decision and a fixed selected
runtime, context, concurrency, vision policy, and workload. Only then can
functional, quality, latency, capacity, and soak evidence be collected.

## Evidence boundary

This finding proves only the recorded disk-policy stop and runtime containment
gap on the dated Apple lane. It does not prove model feasibility or infeasibility,
quality, latency, capacity, vision support, or a Swift-versus-stock winner. No
human-approved serve promotion result exists; `promoted=false` and any future
promotion remains human-gated.
