# Agents-A1 multimodal and quantization qualification

**Observed:** 2026-07-28 through 2026-07-29 UTC
**Host:** Fakoli Dark, Windows/WSL2, one NVIDIA RTX PRO 6000 Blackwell
Max-Q Workstation Edition (96 GB, sm_120)
**Decision:** retain BF16 as the correctness reference, use official FP8 as
the production-shaped text/image/video candidate, and qualify ProtoLabs NVFP4
only as a compact text profile. No model or router route was promoted.

## Exact identities and evidence boundary

All model serves used vLLM nightly commit
`f25953cc59f9b4ba9b04b16228d2b86dcfbcbdb1`, image digest
`sha256:212a1bd7b4267c604408d17dc0048ef152101bc67fbe6ba8567899fc1f227bcd`,
FP8 KV, tensor parallelism one, and a 131,072-token operational window.
Thinking disabled was the production contract; default thinking was used only
as a diagnostic lane.

| Profile | Repository and revision | Local modality boundary |
|---|---|---|
| BF16 | `InternScience/Agents-A1@addff08f1653ee72765c5cf458fe84556bb34f8e` | Text control plus image, video, and mixed media |
| Official FP8 | `InternScience/Agents-A1-FP8@4d7d59380f327b76e73bc71f40e0c589ad0ca1d5` | Text control plus image, video, and mixed media |
| ProtoLabs NVFP4 | `protoLabsAI/Agents-A1-NVFP4@ff24ba5c35b99af25d7bf03c7997be5a0d2a5520` | Text only, Marlin, FlashInfer sampler off, no MTP |

The publisher says Agents-A1 supports multimodal reasoning but also asks for
broader VQA/MMMU/MMBench evaluation, so that statement was treated as a
capability prior rather than a stability result. The ProtoLabs card documents
that the vision-tower profile crashes on its sm_120 stack, requires Marlin,
and cannot combine the required Marlin path with vLLM MTP. Those boundaries
were preserved rather than experimentally bypassed. The dated
[source registry](2026-07-28-agents-a1-multimodal-qualification-evidence/source-registry.json)
records every official and community source, observed date, evidence class,
hardware relevance, and decision impact.

## Storage gate

The initial filesystem had 15,447,490,560 bytes available. Exact-revision
dry-runs and confirmed removals reclaimed 172,288,291,120 model-cache bytes
from the four approved model sets. Audited Docker cleanup then removed only
old build cache, dangling images, superseded application images outside the
protected rollback set, and rejected-candidate images. No broad Docker prune
or volume prune ran.

Available space reached 350,050,500,608 bytes before the three pinned
Agents-A1 variants were pulled. Modification and Docker timestamps were
retained only as inventory observations, not as proof of actual model use.

- [Before inventory](2026-07-28-agents-a1-multimodal-qualification-evidence/cache-inventory-before.json)
- [Guarded cleanup result](2026-07-28-agents-a1-multimodal-qualification-evidence/cache-cleanup-result.json)
- [350 GB gate inventory](2026-07-28-agents-a1-multimodal-qualification-evidence/cache-inventory-after-cleanup.json)
- [Inventory after pinned pulls](2026-07-28-agents-a1-multimodal-qualification-evidence/cache-inventory-after-model-pulls.json)

## Functional, quality, and multimodal results

BF16 and official FP8 both passed smoke, deterministic JSON, tool calling,
streaming tools, tool-result continuation, Responses API checks, session
recall, unified diff, timeout triage, image understanding, OCR, and direct
OpenAI-compatible `video_url` input. The BF16 full protocol-v3 suite passed at
32K and 120K with three repetitions. The initial FP8 protocol artifact exceeded
the 131,072-token combined tokenizer/output limit and is retained as an
invalid attempt rather than a quality result.

The 30-attempt multimodal corpus produced the same 28/30 result and byte-for-
byte same two failing outputs for BF16 and FP8:

- image cases: 12/12;
- mixed video/image cases: 4/4;
- video cases: 12/14;
- failed deterministic assertion: both event-localization outputs identified
  the exact `42.0 - 47.0 seconds` interval but omitted the required word
  `alert`.

The matched BF16 failure shows this was not an FP8 quantization regression.
Default-thinking FP8 passed that diagnostic event case 3/3, but it does not
replace the thinking-disabled production contract. Because the campaign
predeclared 100% deterministic assertions as a hard gate, neither BF16 nor FP8
is labeled fully qualified for the current multimodal corpus and c2/c4
multimodal capacity was not run.

- [BF16 multimodal corpus](2026-07-28-agents-a1-multimodal-qualification-evidence/bf16-multimodal-c1.json)
- [FP8 multimodal corpus](2026-07-28-agents-a1-multimodal-qualification-evidence/fp8-multimodal-c1.json)
- [FP8 default-thinking diagnostics](2026-07-28-agents-a1-multimodal-qualification-evidence/fp8-event-localization-default-thinking-r1.json)
- [BF16 repeated quality](2026-07-28-agents-a1-multimodal-qualification-evidence/bf16-mm-quality-protocol-v3-r3.json)
- [NVFP4 corrected repeated quality](2026-07-28-agents-a1-multimodal-qualification-evidence/nvfp4-text-quality-protocol-v3-r3-full-fixed.json)

Two licensed Wikimedia Commons WebM files were retained with provenance. The
pinned decoder recovered only four of 112 frames from each original, so the
benchmark uses deterministic MP4 derivatives created with the pinned fixture
runtime. Their hashes and provenance are part of
`multimodal-corpus/v1`; no media bytes are logged by the router.

## Capacity and memory

All 128K lanes retained at least 16K output/reasoning headroom. BF16, FP8, and
NVFP4 passed 128K at concurrency one, two, and four. Each 240K boundary request
failed closed with the actionable 131,072-token maximum rather than OOM or
parser corruption.

| Profile | 8K aggregate output throughput | 128K result | Runtime memory observations |
|---|---|---|---|
| BF16 multimodal | c1 90, c8 151, c16 162 tok/s | c1/c2/c4 pass, about 5 tok/s | 65.53 GiB model, 2.50 GiB activation, 19.53 GiB KV |
| FP8 multimodal | c1 104, c8 193, c16 200, c32 218 tok/s | c1/c2/c4 pass, about 5–6 tok/s | 35.31 GiB model, 2.11 GiB activation, 49.66 GiB KV |
| FP8 text-only | c1 101, c8 190, c16 207, c32 225 tok/s | control passes | 34.46 GiB model, 1.42 GiB activation, 50.81 GiB KV |
| NVFP4 text-only | c1 105, c8 187, c16 204 tok/s | c1/c2/c4 pass | 21.03 GiB model, 1.35 GiB activation, 63.80 GiB KV at 0.92 allocation |
| NVFP4 compact text | c16 198 tok/s | c4 pass | 0.40 allocation; dual-instance fit remains an estimate |

Enabling the FP8 vision tower added about 0.85 GiB of model weights and
0.69 GiB of activation space and reduced KV allocation by about 1.15 GiB:
approximately 1.5 GiB of practical runtime overhead. Video did not add
persistent model weights; its main cost is request-dependent visual tokens,
decode work, and KV/activation pressure. That is why the isolated router
profile starts at one video and four images and admits against explicit visual-
token estimates.

NVFP4 is the memory Pareto choice for text: it uses materially less runtime
allocation than FP8 while retaining the hard functional, repeated quality,
128K c4, and throughput gates. It is not a speed win over FP8 and cannot be
recommended for vision/video while the documented vision crash remains.

## Kernel tuning

The official FP8 serve emitted:

```text
Using default MoE config. Performance might be sub-optimal!
Config file not found at .../E=256,N=512,device_name=NVIDIA_RTX_PRO_6000_Blackwell_Max-Q_Workstation_Edition,dtype=fp8_w8a8.json
```

The warning proves only that vLLM fell back to a generic configuration. The
pinned official tuner searched 1,920 candidates independently for each of 18
batch sizes: 34,560 configurations in 12,650 seconds (3h 30m 50s). The
resulting 3,294-byte config has SHA-256
`232cda3b62b2367c26f48dd16f506b9f7b84da903585c6bc36be0fe793cb50e5`.
Startup logs proved the derived image loaded that exact artifact.

Both default and tuned configurations passed all seven functional assertions
and all 204 capacity requests. The identical three-warmed-repetition A/B
produced these client-observed `capacity-v3` means:

| FP8 8K c16 primary lane | Default | Tuned | Interpretation |
|---|---:|---:|---|
| Aggregate output throughput | 214.21 tok/s | 211.21 tok/s | 1.399% regression |
| TTFT p50 / p95 | 1,057 / 3,909 ms | 1,243 / 3,826 ms | p95 improved 2.127%; p50 worsened |
| Effective prefill p50 / p95 | 6,780 / 19,658 tok/s | 5,838 / 18,969 tok/s | Includes queueing, scheduling, and first-token work |
| Generation p50 / p95 | 3,533 / 4,133 ms | 3,161 / 4,123 ms | p95 improved 0.225% |
| Decode p50 / p95 | 17.71 / 81.69 tok/s | 18.30 / 70.41 tok/s | Per-request derived rate |
| Mean inter-token latency p50 / p95 | 56.45 / 68.57 ms | 52.44 / 73.60 ms | Mean interval, not raw token timestamps |
| E2E p50 / p95 | 4,465 / 7,476 ms | 4,367 / 7,544 ms | p95 worsened 0.907% |

The protected 128K c1 lane improved E2E p95 by 1.576% and TTFT p95 by
1.655%, but the primary throughput regression misses the predeclared 5%
adoption threshold. The tune is therefore **rejected and inert**. Do not
activate it. Output-token totals varied slightly because the endpoint request
did not pin an API seed and some 64-token-capped answers stopped early;
throughput uses each response's exact usage count.

- Canonical tune manifest:
  `configs/kernel-tunes/vllm/f25953cc59f9b4ba9b04b16228d2b86dcfbcbdb1/nvidia-rtx-pro-6000-blackwell-max-q-workstation-edition/manifest.json`
- [Default/tuned comparison](2026-07-28-agents-a1-multimodal-qualification-evidence/fp8-moe-ab-comparison.json)
- [Default startup warning](2026-07-28-agents-a1-multimodal-qualification-evidence/fp8-moe-ab-default-startup.log)
- [Tuned startup proof](2026-07-28-agents-a1-multimodal-qualification-evidence/fp8-moe-ab-tuned-startup.log)

## Router boundary

Direct vLLM video passed before router work began. The isolated qualification
router preserves same-dialect `video_url` blocks, fails closed instead of
dropping video during unsupported cross-dialect translation, and applies
content-free admission accounting to the opt-in candidate tier. The initial
policy is one video, four images, 2,048 estimated tokens per image, 16,384
estimated tokens per video, and at least the requested output headroom.

The test configuration contains no live Primary alias. Existing router tiers
do not change unless `media_admission_enabled=true`, and decision logs retain
counts and estimates rather than media bytes.

The first routed smoke inherited default thinking, exhausted 1,024 completion
tokens, and returned no visible answer. Pinning
`chat_template_kwargs.enable_thinking=false` in the isolated tier restored the
production contract; the rerun passed smoke, JSON, 20 tools, streaming tools,
tool-result continuation, and video. The routed matrix also passed one video
plus four images, count overflows, tools with video, and SSE video.

Malformed video and unsupported Anthropic-to-OpenAI video initially surfaced
as generic 500s. The router now maps only caller-correctable upstream statuses
400, 413, 415, and 422 to sanitized 4xx responses, rejects unsupported video
translation before opening the upstream, and performs the check before a
streaming 200 is committed. Upstream 5xx and transport failures remain
sanitized internal errors. Both corrected cases passed in streaming and
non-streaming form.

- [Initial routed matrix](2026-07-28-agents-a1-multimodal-qualification-evidence/fp8-router-multimodal-matrix.json)
- [Corrected error-classification probes](2026-07-28-agents-a1-multimodal-qualification-evidence/fp8-router-error-classification-fixed.json)
- [Corrected routed preflight](2026-07-28-agents-a1-multimodal-qualification-evidence/fp8-router-preflight-fixed.json)

## Decision table

| Variant | Text | Image/video | Quality | Memory | Speed/concurrency | Recommended role |
|---|---|---|---|---|---|---|
| BF16 | Pass | Direct support; corpus 28/30 | Quality ceiling; same two multimodal assertion failures as FP8 | Largest, about 65.5 GiB weights | c16 validated; slower than FP8 | Correctness and quant-regression control |
| Official FP8 | Pass | Direct support; corpus 28/30 | No observed quant regression; current hard multimodal gate not 100% | About 35.3 GiB weights; vision overhead about 1.5 GiB practical | Best tested c32 profile | Principal production-shaped candidate, still no-promotion |
| ProtoLabs NVFP4 | Pass | Not run; publisher-documented vision crash | Repeated text quality passes after validator correction | About 21.0 GiB weights; compact allocation passes | Similar, not faster than FP8 | Pareto-preferred compact text-only profile |

The corpus is a deterministic serving-contract gate, not a broad intelligence
ranking. Publication does not authorize promotion or any production routing
change.

## Reproducibility and friction

The campaign added the versioned multimodal corpus/evidence CLI, video
preflight, read-only cache inventory, opt-in router admission, exact-GPU recipe
override, reusable LLM qualification skill, and reusable kernel-tuning skill.
All fixes are recorded in the repository campaign ticket
`.tickets/2026-07-28-agents-a1-multimodal-qualification.md`.
The chronological
[friction log](2026-07-28-agents-a1-multimodal-qualification-evidence/friction-log.json)
retains failed starts and corrected validator/runtime behavior rather than
hiding them.

The complete bounded evidence directory is
[here](2026-07-28-agents-a1-multimodal-qualification-evidence/README.md).
The [restoration record](2026-07-28-agents-a1-multimodal-qualification-evidence/serve-state-after.json)
confirms all campaign containers and the isolated router were removed while
the exact production router identity, health, port, restart policy, and
pre-campaign managed-serve absence were preserved.
