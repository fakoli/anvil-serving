# DeepSeek V4 Flash 0731

## Current status and review date

The 2026-08-21 record qualifies Martin Vit's Infernal Invocation r18 B12X
TP=2 DSpark K5 profile at 1,048,576 tokens, maxseq8, and 4,096-token batching.
It passed 1,040,063 actual prompt tokens, repeated agentic checks 12/12,
structured tools 160/160 plus the complete functional batches, client-shaped
reserve probes, short c8 and long c2 capacity, a matched no-spec A/B, and a
clean reload gate. The operator has authorized promotion, but the public state
remains r15 until the separate guarded router transaction and Mini acceptance
complete.

The 2026-08-16 public record documents human approval of Martin Vit's
Infernal Invocation r15 B12X TP=2 DSpark K5 profile for the text
`llm.primary` route at 393,216 tokens, 4,096-token batching, and eight admitted
sequences. It passed 351,118 actual prompt tokens directly, 340,119 through
the authenticated router, repeated tools/session/diff/timeout checks 12/12,
short c8 and long c2 capacity, and OpenClaw-compatible Anthropic wire calls.
The prior r33 393K profile is the fixed-port managed rollback. A fresh actual
Mini OpenClaw turn remains open because the installed Mini controller lacks
the current status tool.

A derived WSL2 image also qualified native CPU KV offload at a 262,144
served-token ceiling. It passed a cold ladder through 249,573 prompt tokens and
the managed shared-memory lifecycle regression. This is retained historical
capacity evidence and is not part of the current r15 profile. A digest-pinned
r33 target-only control subsequently qualified the same
checkpoint at a 131,072-token envelope and reached 119,503 actual prompt
tokens. A matched batch-token A/B then reduced profiled activation memory by
34.1% and increased minimum-rank KV allocation by 0.72 GiB while retaining
the same functional and 119,503-token capacity gates. The later r33 DSpark arm
reported 725,543 GPU KV tokens and passed 238,507-, 339,310-, and
359,900-actual-token direct requests without host offload. Review date:
2026-08-21.

The earlier SGLang 32K low-reasoning lane remains valid point-in-time evidence,
not the current performance recipe. Community 0731 NVFP4 and GGUF conversions
remain unqualified. The local maxseq16 lane has bounded c16 and three-tool-burst
evidence, but not broad repeated multi-agent quality.

## Immutable identity

The r16 result pins `deepseek-ai/DeepSeek-V4-Flash-0731` revision
`9e165c30e2704aec5d9d593cce3eebd58bbef1cb`, the release revision containing
the tested weights. The upstream repository has since advanced to
`7872f01b1d1fe23eabc4c98b48bffcef5a386062` for model-card documentation; that
later commit was not substituted into the measured recipe.

DeepSeek identifies 0731 as a re-post-trained official release that supersedes
Flash Preview while retaining the V4 Flash architecture. Treat it as a
distinct behavioral generation. The target model is described as 284B total
and 13B active; checkpoint inventories that include the bundled DSpark draft
module report approximately 304B. It is MIT licensed with a declared
1,048,576-token maximum context.

The 2026-07-10 `nvidia/DeepSeek-V4-Flash-NVFP4` attempt used NVIDIA's Preview
conversion, did not retain a reusable checkpoint revision, and remains
`historical-invalid`. It is not a measurement of 0731.

## Tested hardware and topology

Two RTX PRO 6000 Blackwell Max-Q cards on Fakoli Dark in exclusive TP=2 over
PCIe without NVLink. Every other inference workload was offline. The 192 GB
figure is aggregate VRAM, not unified memory.

## Engine, quantization, KV, context, and concurrency recipe

The current working profile uses image digest
`sha256:f1b13c8604b274212e1164def7d4ed7a4cac9e4f7fa06fa1739730195eca4e18`,
vLLM integration tree `068fc8e7270b92077ba753d002da179c865e444d`, B12X tree
`96e5d3d5c2057fa5d4f542e2368951ddbdcb5b42`, W4A8 kernels, FP8 compressed
MLA KV, InstantTensor `BUFFERED`, TP=2/DCP=1, 393,216 context, eight admitted
sequences, and fixed probabilistic DSpark K5. Native KV offload and LMCache
are disabled. The matched performance workload is c1; capacity also covers
c8 short requests and c2 at 99,175 prompt tokens per request.

The native-Linux source recipe required an SM120-to-WSL2 translation:
direct NCCL P2P and NCCL cuMem device/host allocation are disabled, shared
memory remains enabled, and PyTorch expandable segments are disabled. Model,
JIT, and temporary build data use named Docker volumes.

- [DSpark K5 recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-128k-recipe.toml)
- [Same-image no-spec control](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-nospec-128k-recipe.toml)
- [256K 8 GiB native-offload recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-256k-offload8-wsl2-mmap-unpinned-recipe.toml)
- [256K 16 GiB CPU-reload recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-256k-offload16-wsl2-mmap-unpinned-recipe.toml)
- [650K maxseq16 Pi recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq16-650k-recipe.toml)
- [1M maxseq4 deep-session recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq4-1m-recipe.toml)
- [1M maxseq16 deep-session recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq16-1m-recipe.toml)
- [Complete qualification](../../findings/2026-08-01-deepseek-v4-flash-0731-r16-dspark-qualification.md)
- [Native-offload and 256K qualification](../../findings/2026-08-02-deepseek-v4-flash-0731-native-kv-offload-256k.md)
- [650K/1M Pi qualification](../../findings/2026-08-02-deepseek-v4-flash-0731-650k-1m-pi-qualification.md)
- [650K Primary promotion](../../findings/2026-08-02-deepseek-v4-flash-0731-primary-promotion.md)
- [r33 target-only 131K recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r33-b12x-nospec-maxseq1-131k-recipe.toml)
- [r33 target-only 131K batch-4096 control](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r33-b12x-nospec-maxseq1-batch4096-131k-recipe.toml)
- [r33 quality-first 393K candidate](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r33-b12x-nospec-maxseq1-393k-recipe.toml)
- [r33 promoted DSpark 393K recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r33-b12x-dspark5-maxseq16-batch4096-393k-recipe.toml)
- [r33 quality-control qualification](../../findings/2026-08-10-deepseek-v4-flash-0731-r33-quality-control.md)
- [r33 batch-token A/B](../../findings/2026-08-10-deepseek-v4-flash-0731-r33-batch-token-ab.md)
- [r33 393K Primary promotion](../../findings/2026-08-11-deepseek-v4-flash-0731-r33-393k-promotion.md)
- [Infernal Invocation r15 K5 393K recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-infernal-r15-b12x-dspark5-maxseq8-batch4096-393k-recipe.toml)
- [Infernal Invocation r15 matched no-spec control](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-infernal-r15-b12x-nospec-maxseq8-batch4096-393k-recipe.toml)
- [Infernal Invocation r15 393K promotion](../../findings/2026-08-16-deepseek-v4-flash-0731-infernal-r15-393k-promotion.md)
- [Infernal Invocation r18 K5 1M recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-infernal-r18-b12x-dspark5-maxseq8-batch4096-1m-recipe.toml)
- [Infernal Invocation r18 K5 1M fixed-port promotion recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-infernal-r18-b12x-dspark5-maxseq8-batch4096-1m-port39077-recipe.toml)
- [Infernal Invocation r18 matched no-spec control](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-infernal-r18-b12x-nospec-maxseq8-batch4096-1m-recipe.toml)
- [Infernal Invocation r18 1M qualification](../../findings/2026-08-21-deepseek-v4-flash-0731-infernal-r18-1m-promotion.md)

## Evidence by measurement class

The r18 DSpark K5 profile adds `functional`, `capacity`, matched
`performance`, and bounded `quality` evidence at the model's 1,048,576-token
limit. Calibrated retrieval passed through 1,040,063 actual prompt tokens;
agentic intelligence/session/tools passed 12/12; an additional structured-tool
soak passed 160/160; client-shaped 5,120- and 8,192-output-reserve probes
passed; c8 short and c2 at 490,861 prompt tokens per request completed. The
engine reported 1,323,176 KV tokens, or 1.26 full windows. Against the matched
no-spec control, K5 raised median decode from 76.4 to 142.1 tok/s at 4K and
76.3 to 129.5 at 32K. The operator-authorized live transaction remains a
separate pending acceptance step in this publication.

The r15 DSpark K5 profile adds `functional`, `capacity`, matched `performance`,
bounded `quality`, and routed acceptance evidence. K5 versus the otherwise
identical no-spec control measured 150.0 versus 76.4 tok/s median decode at
4K/c1 and 119.245 versus 76.767 at 32K/c1. Direct retrieval passed at 351,118
actual prompt tokens and authenticated routed retrieval at 340,119. Repeated
tools, session recall, unified diff, and parallel timeout triage passed 12/12.
The engine reported 797,689 KV tokens, or 2.03 full 393,216-token windows.
The stock English 390K routed needle still failed closed under the router's
conservative byte estimator, and generic thinking-disable did not suppress
r15 reasoning. Those are retained limits, not relabelled successes.

The historical r33 DSpark profile adds `functional`, `capacity`, and bounded
`quality` evidence: exact managed routing passed, the direct context ladder
reached 359,900 actual prompt tokens, and OpenClaw/Hermes client-path requests
passed after safe gateway restarts. It uses FP8 DS-MLA KV with no host
offload and reported 725,543 GPU KV tokens. Routed, OpenClaw, and Hermes >300K remain
unproven because the legacy routed needle generated a conservative 450,028-
token admission estimate and failed 413.

The r33 target-only control adds `functional`, `capacity`, and bounded
`quality` evidence on the same released checkpoint:

- Functional preflight passed 6/6, including 20/20 typed tool calls, streaming
  tool use, tool-result continuation, and the Responses API.
- High-reasoning intelligence passed 6/6 attempts, session recall 3/3, and
  tools 3/3. Low/high/max prompt fingerprints measured 6/85/98 prompt tokens.
- A single 119,503-prompt-token request measured 17.445-second TTFT, 7,537
  effective prefill tok/s, and 73.86 decode tok/s. A short coding smoke passed
  after each context probe.
- The engine exposed 15.27 GiB or 283,917 GPU KV tokens. That cannot support a
  393,216-token request without another capacity tier. The prepared 393K arm
  retains FP8 DS-MLA KV and adds 16 GiB native host offload; it was not loaded.
- Requested context targets were non-monotonic against API-reported prompt
  tokens. Capacity claims therefore use only the reported actual size, and a
  harness-integrity ticket remains open.

The matched r33 batch-token arm adds `functional`, `capacity`, and bounded
`performance` evidence. Reducing only `max_num_batched_tokens` from 8,192 to
4,096 lowered peak activation from 1.73 to 1.14 GiB per rank and raised the
minimum-rank KV allocation from 15.27 to 15.99 GiB. It passed 6/6 functional
checks and the same 119,503-actual-token request. The engine reported 553,243
KV tokens, but the 94.861% reported-token increase does not reconcile with the
4.715% KV-byte increase. A configured 393K start and actual >300K request are
therefore still required before claiming over-300K GPU-only capacity.

The r16 revision has `functional`, `capacity`, and bounded `quality` evidence:

- Low reasoning passed smoke, JSON, three typed tools, streaming tools,
  tool-result continuation, and Responses. High and max passed smoke, JSON,
  tools, and tool-result continuation.
- The context ladder passed 32K, 64K, and a clamped 126,464-token request.
  The warmed 125,785-prompt-token row measured 19.44-second TTFO,
  23.81-second first-visible TTFT, 6,469 effective prefill tok/s, and
  128.9 tok/s combined reasoning/visible decode.
- Nine coding/intelligence/session/tool items ran three times each and passed
  27/27 attempts with 4,096 reasoning-headroom tokens.
- In a same-image c1 A/B, DSpark increased median per-request decode from
  64.9 to 130.7 tok/s, aggregate output from 59.6 to 101.7 tok/s, and reduced
  median E2E from 3.88 to 1.60 seconds.
- Cumulative DSpark counters recorded 4,865 accepted of 8,830 draft tokens,
  or 55.1% acceptance and 2.75 accepted tokens per draft.

The derived native-offload lane adds `functional` and `capacity` evidence:

- The 128K configuration stored 7.15 GB GPU-to-CPU and replayed 1.13 GB
  CPU-to-GPU; a 52,495-token replay reached 99.5% cache hits and 0.514-second
  time to first output.
- The 256K configuration passed cold 92,754-, 193,064-, and
  249,573-prompt-token requests. The largest measured 43.75-second TTFO,
  45.58-second first-visible TTFT, 5,705 effective prefill tok/s, and
  135.2 tok/s decode.
- The 8 GiB 256K session stored 11.06 GB GPU-to-CPU, but its follow-ups stayed
  in the larger GPU prefix tier. With a 16 GiB CPU tier, six distinct 150K
  planned-context requests stored 13.63 GB; an exact replay then produced
  113,408 external hits and loaded 1.002 GB CPU-to-GPU in 0.344 seconds while
  GPU-prefix hits remained unchanged. Replay TTFO was 0.825 seconds and visible
  TTFT was 1.974 seconds.
- The ownership-aware managed lifecycle blocked cleanup while both TP workers
  mapped the 8 and 16 GiB files, then reclaimed each after container removal
  and restored split mode.

The GPU-only Pi-context lane adds `functional` and `capacity` evidence:

- The 650K/maxseq16 profile recovered a needle at approximately 640K in 120.6
  seconds and passed smoke, JSON, three typed tools, streaming tools,
  tool-result continuation, and Responses.
- Its matched 32K c1 run completed 3/3 in 16.6 seconds at 5.59-second median
  E2E, 8,793 effective prefill tok/s, and 141.6 tok/s median decode.

The 2026-08-03 remote benchmark-worker smoke adds bounded infrastructure and
quality evidence. An 8K native context case passed 1/1, but no larger context
bucket was attempted. In the tool-error scenario, the model followed the tool
protocol, incorporated results, and retried correctly, then failed the
reasoning and final-answer checks. One pinned SWE-bench Verified instance,
`django__django-11099`, resolved under the official grader. This qualifies the
remote harness path for a scout campaign; it is not a representative SWE-bench
score and does not change the current routing decision.
- The first 1M profile admitted one sequence and recovered a 985K needle, but
  a three-tool burst fatally exceeded its locked B12X workspace. It is rejected
  for Pi agentic use.
- Raising admission to four retained 1M capacity, passed the same Pi gate and
  985K retrieval, and delivered 119.9 tok/s median decode at 32K c1. Near-limit
  retrieval still took 235.7 seconds.
- Raising admission again to 16 retained 1,715,610 KV tokens, passed the same
  Pi gate and 985K retrieval plus a post-probe coding request, and improved
  median 32K decode to 129.0 tok/s. Later real requests required 703.64 and
  687.83 MiB workspaces when only 514.25 MiB was available. The latter had a
  19,118-token Pi prompt and only 5,120 requested output tokens, proving that
  output clamping alone does not make the 1M profile safe.

The prior SGLang lane used image digest
`sha256:0aa5324c4f38bc66f4b55e1e12efab821ef614b1a8629259b2810ff72a6570e6`,
publisher hybrid FP4-expert/FP8 weights, FP8 E4M3 KV, 32,768 context, one
admitted request, low reasoning, and no speculative decoding. It passed
functional and repeated quality gates. Its final 32K capacity lane completed
11/12 at 2.705-second TTFO, 29.106-second first-visible TTFT, 7,818 effective
prefill tok/s, and 11.5 tok/s combined reasoning/visible decode.

A separate vision-adapter package,
`webbrain-one/DeepSeek-V4-Flash-0731-Vision-NVFP4` (rev `3a8f168c`,
digest-pinned SGLang v0.5.16, marlin/marlin kernels, TP=2, 4,096 context,
`--mem-fraction-static 0.97`), first-loaded and served on 2026-08-07 with
`compatibility-only` and bounded `functional`/negative `quality` evidence:
text-lane gates and image conditioning pass, but dense OCR and GUI-affordance
reading confabulate against known ground truth, and the checkpoint has no
chat template. See the
[vision first-load finding](../../findings/2026-08-07-deepseek-0731-vision-nvfp4-sglang-first-load.md).

Current `external-prior` evidence strengthens the research priority without
expanding the local contract:

- DeepSeek reports large agentic gains over Preview, including 82.7 versus
  61.8 on Terminal Bench 2.1 and 54.4 versus 7.3 on DeepSWE. The code-agent
  runs used an unreleased harness at `max`; two DSBench sets are internal.
- Artificial Analysis independently scores max-effort 0731 at 50, number 3 of
  101 comparable models, while reporting 210 million evaluation output tokens.
- Community 0731 NVFP4 conversions now exist. MJPansa has the strongest
  conversion receipt; Auroter has the strongest four-RTX-PRO performance
  prior. Neither is local TP=2 qualification.

The 2026-08-10 community refresh adds a quality-first decision order without
expanding the local contract. It keeps FP8 KV for the first over-300K r33 arm,
separates NVFP4 weights from NVFP4 KV, treats Auroter W4A16/FP8-KV as a later
precision challenger, and defers W4A4 and NVFP4 KV until matched task-quality
gates pass. The proposed 393,216-token target-only arm is an unmeasured
experiment design, not a replacement for the recorded r16 profiles. The same
refresh also records active vLLM reasoning-template defects and broad Reddit
channel coverage; prompt-token fingerprints and a fixed harness are required
before any precision comparison.

See the [deep research update](../../findings/2026-08-01-deepseek-v4-flash-0731-research-update.md)
and its [source registry](../../findings/2026-08-01-deepseek-v4-flash-0731-research-evidence/source-registry.json)
for the benchmark deltas, architecture, runtime matrix, conversion identities,
GGUF size ladder, DSpark caveats, and source classifications.
See the
[2026-08-10 community configuration refresh](../../findings/2026-08-10-deepseek-v4-flash-0731-community-config-refresh.md)
and its machine-readable candidate ledger for the r33 candidates, precision
decision, source classifications, subreddit coverage, and required gates.

## Decision and promotion state

2026-08-21 Infernal Invocation r18 qualification: the exact digest-pinned K5
profile is `promotion-ready` at 1,048,576 tokens with maxseq8 and batch4,096.
The matched no-spec arm is rejected for deployment performance. Operator
authorization is recorded, but r15 remains the public current state until the
guarded router transaction, Mini context/compaction convergence, and real
Hermes/Pi/OpenClaw acceptance pass.

2026-08-16 Infernal Invocation r15 promotion: after explicit human approval,
the digest-pinned K5 profile became the exclusive TP=2 text `llm.primary` at
393,216 tokens. The guarded transaction reran direct context, tools,
streaming, tool-result, and Responses gates, installed the router profile,
restarted the router, and verified exact post-restart identity and admission.
The endpoint-adapted r33 393K profile is the immediate transactional rollback.
Martin Vit's upstream receipt qualified 131,072 tokens on native Linux with
two RTX PRO 6000 Blackwell GPUs on direct PCIe root ports; this WSL2 393K
result is an independent local qualification.

The 2026-08-02 public finding records human approval of the maxseq16 r16 DSpark
profile as the exclusive TP=2 Primary for one Pi/OpenClaw coding user, with
high reasoning as the client default and `llm.rollback` preserved explicitly.
That is a dated promotion record, not a claim about the live operator
assignment. The recorded router's optional per-tier output cap was 32,768 and
warned instead of rejecting an oversized caller budget.

2026-08-06 operator-directed retune: the pinned recipe now serves
`MAX_MODEL_LEN=262144` with `MAX_NUM_BATCHED_TOKENS=8192` (previously
650,000/4,096; serve and model names retain the historical `650k` suffix). The
engine sized GPU KV cache at 272,040 tokens (1.04x concurrency at a full
262,144-token request) and a same-day functional preflight (smoke, structured
JSON, tool batch x20, tool-result continuation) passed. The ~640K retrieval,
650K-envelope performance rows, and client smokes above are dated history from
the prior envelope and do not transfer without fresh measurement.

2026-08-07 image upgrade: the pinned recipe moved from the r16 to the
digest-pinned r27 community image (official 0731 reasoning/tool prompt
contract, tiered-offload lifetime fixes, InstantTensor registration fallback,
`PYTHONHASHSEED=0`). KV cache re-sized to 272,107 tokens; a same-day
functional preflight passed and a matched 4K/c16 capacity probe showed
concurrency parity with the recorded r16 artifact (520 vs 513.5 aggregate
tok/s). All r16-labeled performance figures on this page predate the image
upgrade.

2026-08-10 r33 quality control: the exact released checkpoint passed the
target-only/no-spec 131K control on a digest-pinned r33 B12X runtime. The
largest validated request contained 119,503 actual prompt tokens. The
quality-first 393K candidate keeps FP8 KV and adds host capacity rather than
introducing NVFP4 KV before a matched quality A/B. No route or promotion
changed.

2026-08-11 r33 393K promotion: after human approval, the GPU-only DSpark K5
profile became `llm.primary`. Direct capacity reached 359,900 actual prompt
tokens; OpenClaw and Hermes were updated/restarted at 393,216 context,
32,768 output, and high reasoning.
The managed split restoration group is Agents-A1 plus Omni; Qwen was not
started. Routed/OpenClaw/Hermes >300K and SWE scoring remain open.

2026-08-10 batch-token A/B: the otherwise matched 4,096-token arm reduced
profiled activation pressure, increased GPU KV allocation, passed the same
bounded gates, and was healthy/direct-only at campaign close. It is the preferred basis
for a GPU-only 393K experiment, but no route or promotion changed and no
request above 300K has yet run.

The 1M profiles are experimental only. The current exclusive AI-only policy
has no separate video-workload reserve gate; the historical 3 GiB reserve
failures below remain point-in-time evidence from mixed-use policy. Remaining
gates include sustained multi-turn high/max testing, fixing and
requalifying the client-shaped 1M B12X workspace failure, and pinned 0731 NVFP4
W4A16/W4A4 comparisons.

2026-08-07 vision-adapter first load: `no-promotion`, evaluated separately
from the text Primary. The WebBrain 0731 vision overlay loaded and served on
TP=2 with grounded image conditioning, but OCR/GUI reading confabulated and
the checkpoint exposes no chat template, so it cannot serve a router chat
client. The 650K Primary was restored and its health verified in the same
session; no alias, route, or promoted serve changed.

## Failures and gotchas

The first r16 start exposed an offline nested-speculative-model localization
gap. The second exposed native-Linux NCCL defaults incompatible with WSL2. The
third loaded both ranks but missed the 131K KV admission gate by 0.15 GiB at
`max_num_seqs=16`; the qualified c1 recipe reduces admission to eight and the
CUDA-graph cap to 48 without raising its memory-utilization ceiling.

Both r16 profiles fail the 3 GiB reported-free reserve. Per-context sampling
found only 1,179-1,203 MiB free on `dark-compute-a` and 2,031 MiB on
`dark-compute-b`. WSL/WDDM global allocation differs from native Linux, but a
successful request does not authorize silently weakening the gate.
That gate was tied to mixed graphics/video operation and is not part of the
2026-08-10 exclusive AI-only r33 acceptance policy.

Moving Windows display output to the AMD iGPU allowed the previously failing
maxseq16 graph envelope to start. The 650K profile reported only 797/805 MiB
free after its workload, the 1M/maxseq4 profile reported 207/209 MiB after
the 985K probe, and the 1M/maxseq16 profile reported 339/335 MiB. The experiment
intentionally waived the separate 3 GiB
acceptance gate while retaining `GPU_MEMORY_UTILIZATION=0.975`; this is a
capacity result, not a new reserve policy. The 1M/maxseq1 profile also remains
an explicit retained failure because its locked 514.25 MB B12X workspace could
not satisfy a later 873.62 MB compressed-MLA allocation.

Reasoning-budget exhaustion is still a real operational risk. One earlier
SGLang capacity request and one additional r16 no-spec control run completed
without visible content. High/max support proves protocol compatibility, not
that arbitrarily large reasoning budgets are efficient.

The pinned runtime source repository has no root license file at the tested
revision. The model weights are MIT, but local qualification does not establish
permission to redistribute the image or derived runtime code.

The historical NGC vLLM architecture rejection and aborted NVFP4 load are not
measurements of the current 0731 checkpoint.

Native offload originally crashed on WSL2 because CUDA host registration of
the process-shared mmap conflicted with the allocator lifetime. The derived
image skips registration only for that mmap while preserving the global V2
UVA path. A separate 256K failure was stale tmpfs exhaustion: four orphan
offload files filled `/dev/shm`, producing `mmap.madvise: Bad address`. Anvil
Serving now performs two ownership checks and exact-path cleanup before load
and after managed teardown. Page-cache reclaim alone is not sufficient.

## Dated run history

- [2026-08-21 Infernal Invocation r18 1M qualification](../../findings/2026-08-21-deepseek-v4-flash-0731-infernal-r18-1m-promotion.md)
- [2026-08-16 Infernal Invocation r15 393K Primary promotion](../../findings/2026-08-16-deepseek-v4-flash-0731-infernal-r15-393k-promotion.md)
- [2026-08-11 r33 393K Primary promotion](../../findings/2026-08-11-deepseek-v4-flash-0731-r33-393k-promotion.md)
- [2026-08-10 r33 batch-token A/B](../../findings/2026-08-10-deepseek-v4-flash-0731-r33-batch-token-ab.md)
- [2026-08-10 r33 target-only quality control](../../findings/2026-08-10-deepseek-v4-flash-0731-r33-quality-control.md)
- [2026-08-10 community configuration refresh](../../findings/2026-08-10-deepseek-v4-flash-0731-community-config-refresh.md)
- [2026-08-07 vision-adapter (NVFP4) first load](../../findings/2026-08-07-deepseek-0731-vision-nvfp4-sglang-first-load.md)
- [2026-08-03 context, agentic-recovery, and SWE-bench smoke](../../findings/2026-08-03-deepseek-context-agentic-swe-smoke.md)
- [2026-08-02 650K/1M Pi qualification](../../findings/2026-08-02-deepseek-v4-flash-0731-650k-1m-pi-qualification.md)
- [2026-08-02 650K Primary promotion](../../findings/2026-08-02-deepseek-v4-flash-0731-primary-promotion.md)
- [2026-08-02 native KV offload and 256K qualification](../../findings/2026-08-02-deepseek-v4-flash-0731-native-kv-offload-256k.md)
- [2026-08-01 r16 DSpark qualification](../../findings/2026-08-01-deepseek-v4-flash-0731-r16-dspark-qualification.md)
- [2026-08-01 deep research update](../../findings/2026-08-01-deepseek-v4-flash-0731-research-update.md)
- [2026-08-01 dual-PRO TP=2 campaign](../../findings/2026-08-01-dual-pro-tp2-model-campaign.md)
- [2026-07-10 bakeoff](../../findings/2026-07-10-blackwell-local-model-bakeoff.md)
- [Retained failure detail](../../findings/2026-07-10-blackwell-local-model-bakeoff-evidence/failures.md)
