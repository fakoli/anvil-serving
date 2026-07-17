# GPT-OSS Puzzle 88B local qualification

Date: 2026-07-17

Status: **implemented and locally qualified; recipe remains `unverified`**

This finding records the local Anvil vLLM port and RTX PRO 6000 proof for
`nvidia/gpt-oss-puzzle-88B`. It does not promote the recipe, change router
assignments, publish an image, or make a general hardware-support claim.

## Immutable identities

- Anvil vLLM branch: `codex/gpt-oss-puzzle`
- Selected upstream base: `9354f222042986addf20709e5274fc26e0d09745`
- Behavioral source base/head: `f819265a` / `2561d92b`
- Anvil vLLM commit: `1bf3b12d5bbeb09136e8478e37133ab0ffad3e51`
- Anvil Serving branch: `codex/gpt-oss-puzzle-serving`
- Serving configuration commit: `1c4cba0d9d15ea39b5ff0e2eff68196373b21bea`
- Model: `nvidia/gpt-oss-puzzle-88B`
- Checkpoint revision: `9c0e0746a0d2218b28cc7b2cb3ce4e1a2f50fdb2`
- Served name: `gpt-oss-puzzle-88b`
- Image tag:
  `anvil-vllm:gpt-oss-puzzle-1bf3b12d5bbeb09136e8478e37133ab0ffad3e51`
- Image ID:
  `sha256:2191e2740c6aac83489e2e4de597f1cafdabc2ca21cf8e412d6430b8239ad362`
- vLLM version: `0.23.1rc1.dev1229+g1bf3b12d5`
- Qualification GPU: NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition,
  UUID `GPU-d0f446cf-1771-414c-e116-a39138798a8c`

The vLLM change was ported from the behavioral diff and was not cherry-picked
from the PR's original or merge commits.

## Implementation

The port adds native `GptOssPuzzleForCausalLM` registration and
`gpt_oss_puzzle` normalization, uses the current GPT-OSS wrapper/block/attention
extension points, and keeps ordinary GPT-OSS behavior on its existing path.
Each Puzzle block uses its own configuration and explicit sliding window.

The GPT-OSS weight loader now exposes a per-layer expert descriptor. It keeps
the existing MXFP4, Quark, unquantized, pipeline-parallel, KV-scale, and expert
remapping behavior while selecting each layer's physical experts through that
layer's actual map. Linear and round-robin expert placement are covered, and EP
sizes above the checkpoint's smallest layer expert count (64) fail at startup.

The OpenAI protocol paths use one GPT-OSS-family predicate for Chat, Responses,
parser selection, rendering, and derendering. Weighted KV grouping was added to
the current uniform-page-size allocator, including iterative regrouping after
auto-fit and cycle handling. The Puzzle pattern selects group size 10.

Anvil Serving contains a Compose candidate, a serves entry, and an `unverified`
recipe with TP=1, Marlin MXFP4, FP8 KV cache, 131072 max model length, native
Harmony handling, and no FlashInfer MXFP4/MXFP8 compatibility shortcut. Recipe
loading does not build or publish the image.

## Static and unit verification

The Linux targeted suites passed:

- Puzzle model: 22 tests
- Puzzle configuration: 2 tests
- quantization additions: 3 tests
- heterogeneous loader: 1 parameterized suite
- protocol: 3 tests
- KV grouping/auto-fit: 7 tests
- LoRA behavior: 5 tests
- Harmony regression: 255 tests
- KV-cache regression: 76 tests
- quantization-config regression: 15 tests
- expert-parallel filter regression: 37 tests

The tests cover the exact 36-layer 10/18/8 attention pattern, 18x128 and 18x64
expert layout, all six MXFP4 tensor types, TP, linear EP, round-robin EP,
PP+EP, unquantized and Quark preservation, KV scales, CPU-to-target loading,
and a test-fatal unexpected `.cuda()` call.

`pre-commit run mypy-3.12 --all-files --hook-stage manual` passed in Linux.
Useful Windows pre-commit hooks passed; the remaining Windows failures were the
repository's POSIX `/bin/bash`/encoding assumptions rather than changed-file
failures. The Anvil Serving full test suite passed with 4,443 passed and one
skipped; focused recipe tests passed 28/28; Ruff, diff checks, and Compose
configuration validation passed. Independent adversarial reviews found no
actionable code or configuration finding after the GPU label and missing-image
issues were corrected.

## Image build

The final image was built from the exact fork commit with the `vllm-openai`
target and SM120 source-built kernels (`torch_cuda_arch_list=12.0`,
`max_jobs=16`, `nvcc_threads=4`, Ninja `-j4`). The exact-commit precompiled
kernel endpoint returned 404. Reusing 0.25.1 precompiled ops was rejected after
smoke testing found an incompatible `_moe_C::moe_sum` schema. The final source
image exposes the matching four-argument schema and is intentionally a local
RTX PRO 6000 qualification image, not a broad portable binary.

An offline in-image configuration smoke resolved the Puzzle architecture,
36 layers, 10/18/8 windows, 18x64 plus 18x128 experts,
`gpt_oss_mxfp4`, `is_moe=true`, and max model length 131072.

## Control proof

The exact custom image also served the ordinary `openai/gpt-oss-120b`
checkpoint at revision `b5c939de8f754692c1647ca79fbf85e8c1e70f8a`.
It loaded 60.77 GiB of MXFP4 weights with Marlin and passed the Anvil preflight:
coding, JSON, long-context needle, tool batch 20/20, and reasoning evidence.
The capacity run completed 5/5. The official GPQA debug sample scored 0/1; a
single sample is retained only as a runner/control smoke, not a quality claim.

## Puzzle live proof

Startup resolved `GptOssPuzzleForCausalLM`, loaded all 11 real checkpoint
shards (46.56 GiB checkpoint, 50.71 GiB model allocation), selected Marlin
MXFP4, and initialized a 38.72 GiB FP8 KV cache. The server reported 2,819,653
KV tokens and 21.51x maximum concurrency at 131072 tokens. Loaded GPU memory was
93,480 MiB of 97,887 MiB.

The two weighted-KV warnings were exactly the expected group-size-10 padding:

- 18-layer group: add two layers, at most 11.11% waste
- 8-layer group: add two layers, at most 25.00% waste

Live functional results:

- deterministic greedy Chat: identical visible and reasoning text twice
- structured JSON: pass
- reasoning channel: pass
- Responses API: separate reasoning and final-message items, pass
- preflight long-context needle: pass
- shared-prefix tool batch: 20/20 valid tool calls
- repeated-prefix 160-token crossing: pass
- repeated-prefix 8,500-token crossing: pass; warm TTFT improved from about
  0.92 seconds to 0.51 seconds
- exact near-limit request: 130,696 prompt tokens, 256-token requested
  completion budget, 130,952-token total request budget (120 below 131072),
  exact needle returned, 130,803 tokens actually consumed
- a tighter 131,024-token budget was accepted and processed but consumed its
  128-token completion budget in reasoning; it is retained as a boundary
  artifact, not counted as the completion proof

The five-request capacity sample completed 5/5 with TTFT p50/p95 of
0.38/0.68 seconds and E2E p50/p95 of 0.43/0.72 seconds. These short mixed
prompts are not a controlled decode benchmark. During the 20-worker GPQA run,
server logs commonly reported about 575-605 aggregate generation tokens/s with
eight active requests. Prefix-cache hit rate reached 72.4%. Performance is
recorded but is not a promotion gate.

## Quality and known failures

The official `gpt-oss` Responses sampler completed the full GPQA run:

- evaluations: 1,584
- elapsed: 37 minutes 17 seconds
- score: `0.6571969696969697` (65.72%)
- standard deviation: `0.47464630275405073`

The Anvil quality bakeoff is retained as a failed comparison artifact:

- 128K-target context probe: pass
- three-attempt session recall: 3/3 pass
- timeout triage: 3/3 pass
- unified-diff check: 2/3; one answer had an extra leading space after the
  diff marker and failed the strict string validator
- `record_weather_zip` tool check: 0/3 because the model emitted a token before
  the required Harmony start token; vLLM surfaced
  `openai_harmony.HarmonyError` as HTTP 500

The independent preflight's `get_weather` tool workload passed 20/20, so the
failure is prompt/tool-shape-specific rather than evidence that all tool use is
broken. It still prevents calling this candidate verified or promotion-ready.

## Restoration and publication boundary

Both candidate runs were stopped and their candidate containers removed. The
machine began with no running containers and was restored to no running
containers. GPU 1 returned to 19 MiB used and 0% utilization. Heavy, Fast, and
the router were not restarted because they were already stopped before the
qualification; starting them would not have restored the initial state.

The recipe remains `unverified`. No image was pushed, no upstream PR was
created, no recipe was promoted, and no production routing was changed. A human
must still review every changed line before any publication decision.

The final duplicate-work search still found PR #38135 open for the model and
PR #36512 open for AnyModel; no additional open model-specific PR was found.

## Evidence

Raw evidence is in
`docs/findings/2026-07-17-gpt-oss-puzzle-qualification-evidence/`.
`checksums.sha256` contains SHA-256 hashes for every retained artifact.

