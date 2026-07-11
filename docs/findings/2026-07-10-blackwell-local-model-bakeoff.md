# Blackwell Local Model Bakeoff

- **Date:** 2026-07-10 → 2026-07-11
- **Host:** fakoli-dark (Windows 11 + WSL2 + Docker Desktop; RTX 5090 32 GB + RTX PRO 6000 Blackwell Max-Q 96 GB, both sm_120)
- **Repository revision:** branch `bench/2026-07-10-blackwell-model-bakeoff` from `0e11df6` (origin/main)
- **Benchmark purpose:** measure six community-shortlisted candidates against the current production fast/heavy tiers, on this hardware, with anvil-serving's own correctness gates — to produce public serving evidence, not to change production.

## Executive Summary

Six candidates were attempted; four produced measured profiles, two ended as
documented failures. **No production tier changed.**

- **MiniMax-M2.7-REAP-139B-A10B NVFP4** (community REAP prune) is the best
  measured heavy-role candidate: the only candidate to sweep every suite
  including both intelligence checks, at 97.2 tok/s single-stream with 86 ms
  warm TTFT — but it is a community checkpoint with no 131k headroom on 96 GB.
  **Best measured candidate for the heavy role; not promoted.**
- **Ornith-1.0-35B FP8** verified the full 131,072-token window with the
  fastest measured 131k full-prefill TTFT (13.1 s) and clean tool-calling;
  single-stream decode is modest (29 tok/s). **Retain as specialist
  (agentic / long-context); not promoted.**
- **Nemotron-3-Nano-30B-A3B NVFP4** works at 131k only with a PIECEWISE
  CUDA-graph workaround for an upstream engine bug, plus the model card's
  custom reasoning parser. After a mid-run WSL platform fix, its previously
  erratic latency proved environmental — but 15 tok/s decode keeps it
  **experimental**.
- **Nemotron-3-Nano-Omni-30B** is unservable on the production engine image
  (vLLM 0.19) and fully servable on vLLM nightly v0.23: tools 20/20, 64k
  context, 27.3 tok/s. **Keep experimental; watch for stable-release support.**
- **Gemma-4-31B-IT NVFP4 never fit the 32 GB card** across six configurations
  (KV always negative), a structural artifact of the WSL2-forced legacy model
  runner pricing Gemma 4's sliding-window layers as global. **Reject under
  tested configuration** — with named untested alternatives.
- **DeepSeek-V4-Flash NVFP4**: engine-version rejection on the NGC image;
  the nightly-image attempt was aborted mid-load by the operator (projected
  60+ min, host pressure). **Not enough evidence.**

Cross-cutting lesson: on this host the *engine image is part of the recipe*.
Three candidates changed outcome purely by engine version, and one platform
fact (WSL 2.6.2 → 2.7.10) changed a candidate's measured latency profile.

## Hardware and Topology

See `…-evidence/environment.json`. Two-GPU reference host: RTX 5090 (32 GB,
fast tier / small candidates) and RTX PRO 6000 (96 GB, heavy tier / large
candidates). Weights live in the `vllm-hfcache` named docker volume
(D:-backed ext4). All serves bind `127.0.0.1`. WSL2 lacks UVA, forcing
`VLLM_USE_V2_MODEL_RUNNER=0` (repo gotcha #14) — this constraint materially
shaped the Gemma result. GPU pinning by PCI_BUS_ID index for NGC images
(gotcha #13 note in the compose file).

## Current Baselines

Measured in place before any candidate ran (evidence: `baseline-*.bakeoff.json`):

| Tier | Model | Result |
|---|---|---|
| heavy (:30002) | gpt-oss-120b | all suites pass; 131k context pass (full-prefill TTFT 25.2 s); intelligence 2/2; prior measured 183.2 tok/s |
| fast (:30003) | qwen36-35b-a3b-nvfp4 | thinking disabled (production tier knob): passes everything except the known `parallel_timeout_triage` check — identical to its 2026-07-08 promotion profile |

A deliberate negative artifact is preserved: benchmarking the fast serve with
*default* thinking reproduces the empty-content thinking-starvation gotcha
(`baseline-qwen36-…-thinking-default.bakeoff.json`).

## Candidate Selection

Candidates came from the internal model-landscape shortlist (community-prior
research notes), filtered to what plausibly fits this hardware:
Nemotron-3-Nano-30B text and Omni (NVIDIA, NVFP4-native), Gemma-4-31B
(general assistant), Ornith-1.0-35B (agentic-coding), MiniMax-M2.7 REAP
139B (heavy MoE, community prune), DeepSeek-V4-Flash (heavy stretch).

## Community Research Used as Recipe Priors

`…-evidence/source-registry.json` records every source with an
`evidence_type`. Community/Reddit material (rtx6kpro wiki, blackwell-gpu-wiki,
model-landscape notes, vendor benchmark claims) was used **only** to select
candidates and seed serve flags. Every conclusion below rests exclusively on
local measurements. No community number appears in any measured field.

## Test Method

Per candidate: `anvil-serving models pull` (named volume) → compose-defined
evaluation serve (loopback-only, `anvil-serving serves up/down`) →
`anvil-serving preflight` → `anvil-serving benchmark --bakeoff` (suites:
chat, context, tool, session, intelligence; thinking disabled to match the
production router's tier behavior) → single-stream throughput benchmark
(10 req, 8k ctx, 256 out). Readiness was probed with a real 1-token
completion (the `/v1/models` endpoint answers while an engine core is still
initializing — learned the hard way). Engine image is chosen per candidate
and recorded in every artifact. Reproduction commands:
`…-evidence/reproduction.md`.

## Correctness Gates

The bakeoff suites are anvil-serving's standard independent gates (repo rule:
never self-verify): structural JSON validity, deterministic text checks,
needle retrieval at the claimed context, tool-call shape via a 20-request
shared-prefix batch, and a session-memory recall probe.

## Tool-Calling Results

Two instruments: each bakeoff JSON's `tool` suite is a single structured
smoke check; the "20/20" figures are preflight's 20-request shared-prefix
batch, archived verbatim in `…-evidence/preflight-transcripts.md`.

- **Pass (20/20 clean):** Ornith FP8, MiniMax REAP (thinking disabled),
  Nemotron Omni (nightly + `qwen3_coder` parser), Nemotron text
  (with the `nano_v3` reasoning-parser plugin; without it, think-text leaks
  and tool extraction misfires).
- **Config-sensitive:** Nemotron text under 64k+FULL-graphs (pre-workaround)
  returned plain text instead of `tool_calls` in one run — recorded.
- vLLM rejects tool-bearing requests outright (HTTP 400) when
  `--enable-auto-tool-choice` is absent — an integration footgun worth
  remembering, recorded during the Omni cycles.

## Multi-Turn Memory Results

Session-recall passed for every candidate that reached the suite with
thinking disabled. With default thinking and small budgets, session checks
fail with empty content (starvation, gotcha #6/#9) — reproduced on the fast
baseline and on Ornith/MiniMax preflights.

## Long-Context Results

| Candidate | Claimed ctx | Needle/context result |
|---|---|---|
| Ornith FP8 | 131,072 | pass — needle at ~128k in 11.9 s; full-prefill TTFT 13.1 s (fastest 131k full-prefill of the set; see preflight-transcripts.md) |
| Nemotron text | 131,072 | pass — needle retrieved; PIECEWISE workaround required (see Failed Runs) |
| MiniMax REAP | 65,536 | pass — TTFT 14.3 s at 64k; **no VRAM headroom for 131k** (94.3 GB used) |
| Nemotron Omni | 65,536 | pass — TTFT 3.1 s at 64k (post-WSL-fix) |
| Gemma 4 31B | — | never allocated KV on 32 GB (see Failed Runs) |

## Coding and Agent Results

The two-check deterministic intelligence suite (unified-diff edit + parallel
timeout triage): heavy baseline 2/2, **MiniMax REAP 2/2** (only candidate to
match), fast baseline / Ornith / Nemotron text (clean re-measure) 1/2,
Nemotron Omni 0/2. Two checks is a coarse instrument — treat as a gate, not
a ranking.

## Multimodal Results

Nemotron Omni on nightly initializes its sound encoder ("Nemotron AVLM") and
serves the text path cleanly. Modality-specific quality (image/audio input)
was **not tested** — out of scope for this text-role bakeoff; do not read
multimodal quality claims into this finding.

## Performance Results

Single-stream, 8k ctx, 256 out, thinking disabled, measured warm:

| Candidate | tok/s | TTFT p50 (warm) |
|---|---|---|
| MiniMax REAP 139B NVFP4 | **97.2** | 86 ms |
| Ornith 35B FP8 | 29.2 | 772 ms |
| Nemotron Omni 30B NVFP4 (nightly) | 27.3 | 675 ms |
| Nemotron text 30B NVFP4 | 15.0 | 1.68 s |

Reference: production gpt-oss-120b measured 183.2 tok/s in its own prior
evidence — no candidate approaches it on raw decode.

## Operational Behavior

- **VRAM:** MiniMax 94.3 GB @64k (hard ceiling); Ornith ~36 GB weights leaves
  generous KV on 96 GB; both Nemotrons ~18 GB on the 5090.
- **Host RAM:** repeated 60–90 GB weight streams balloon the WSL page cache;
  `autoMemoryReclaim=gradual` (added mid-run) lags load bursts — manual
  `drop_caches` is safe and instant. A zombie loader PID survived a container
  stop during the aborted DeepSeek load (WSL2 mmap hazard); `docker rm -f`
  cleared it.
- **Engine images:** NGC 26.04 (vLLM 0.19) carried four candidates; upstream
  nightly (v0.23.1rc1) was required for Omni and DeepSeek's config parse.
  llama.cpp and SGLang remain available seams (a Gemma GGUF service is staged
  but unmeasured).

## Failed and Incomplete Runs

Full detail with error excerpts, classifications, and not-attempted lists:
`…-evidence/failures.md`. Headlines:

1. Nemotron text @131k + FULL CUDA graphs: silent engine-init hang
   (matches upstream vLLM #34094 class; PIECEWISE + `--mamba-ssm-cache-dtype
   float16` is the working config).
2. Nemotron Omni on vLLM 0.19: architecture unsupported; the NGC release-notes
   `--hf-overrides` masquerade fails on NVFP4 scale keys. Fixed by nightly.
3. Gemma-4-31B NVFP4 on 32 GB: six-config OOM ladder, KV negative even at
   16k/eager/text-only. Structural under the legacy runner.
4. DeepSeek-V4-Flash: `deepseek_v4` unknown to NGC-image transformers;
   nightly attempt aborted at shard 18/46 by operator (time + host pressure).
5. Operational notes: interrupted pull stream (storage resize), unauthenticated
   pull-token quoting bug and the resulting lock contention.

## Candidate-by-Candidate Findings

(Every entry: exact repo, engine+version, image, GPU, quant, context,
concurrency, KV format, launch config = compose service, results, verdict.
Machine-readable versions in the evidence JSONs.)

### nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4 — verdict: **keep experimental**
Compose `cand-nemotron3-nano-30b` (:39020), NGC vLLM 0.19/26.04, RTX 5090,
`modelopt_fp4`, fp8 KV auto-selected by the ModelOpt checkpoint (Mamba cache
float16), 131,072 ctx, 2 seqs. Requires:
PIECEWISE cudagraph mode (else init hang at 131k), `nano_v3` reasoning-parser
plugin (else think-leak breaks JSON/tools), thinking disabled for small
budgets. Clean re-measure (WSL 2.7.10, idle host): preflight ALL PASS,
context/tool/session pass, intelligence 1/2, 15.0 tok/s, warm TTFT 1.68 s.
The dramatic 17–41 s TTFT of earlier runs did not survive the platform fix —
recorded as environmental. Slow decode for a "nano"; revisit on newer engines.

### nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4 — verdict: **keep experimental; watch for runtime fixes**
Unservable on vLLM 0.19 (arch missing; masquerade incompatible with NVFP4
scales). On `vllm/vllm-openai:nightly` v0.23.1rc1 (native support,
`nemotron_v3` parser, `qwen3_coder` tools): tools 20/20, session pass, 64k
context TTFT 3.1 s, intelligence 0/2, 27.3 tok/s, warm TTFT 675 ms, sound
encoder initialized. Pin to a stable ≥0.23 release before any real role.

### nvidia/Gemma-4-31B-IT-NVFP4 — verdict: **reject under tested configuration**
Image `vllm/vllm-openai:gemma4-unified`, RTX 5090 32 GB, WSL2 legacy runner.
Six configurations (0.88→0.94 gmu, 32k→16k, modality-zeroing,
`--language-model-only`, eager) all died at KV allocation (best: −0.66 GiB).
This is a *stack* result, not a model judgment: the legacy runner prices 50
sliding-window layers as global. Untested alternatives, in order of promise:
llama.cpp QAT-q4_0 GGUF at 64k (service `cand-gemma4-31b-llamacpp` :39025 is
staged in compose, unmeasured), the 96 GB PRO 6000, the NGC 26.04 image,
encoder-stripped community NVFP4.

### deepreinforce-ai/Ornith-1.0-35B-FP8 — verdict: **retain as specialist; not promoted**
Compose `cand-ornith-35b-fp8` (:39022), NGC vLLM 0.19, RTX PRO 6000,
compressed-tensors FP8 (no `--quantization` flag), fp8 KV, 131,072 ctx,
qwen3/qwen3_coder parsers. Thinking-disabled bakeoff: 131k needle 11.9 s,
full-prefill TTFT 13.1 s (fastest 131k full-prefill measured; heavy baseline
25.2 s), tools 20/20, session pass, intelligence 1/2, 29.2 tok/s. Default-thinking empties small budgets
(tier config must disable or budget generously). The `fla/ops` GDN warning
appeared at load; no instability followed. Vendor SOTA claims remain
unverified and played no part in this verdict.

### dervig/m51Lab-MiniMax-M2.7-REAP-139B-A10B-NVFP4 — verdict: **best measured heavy candidate; not promoted**
Compose `cand-minimax-m27-reap` (:39023), NGC vLLM 0.19, RTX PRO 6000,
compressed-tensors NVFP4, fp8 KV, 65,536 ctx, 1 seq, `minimax_m2` tool
parser. Thinking-disabled bakeoff: clean sweep — context, tools, session,
intelligence 2/2 (only candidate to match the heavy baseline). 97.2 tok/s,
warm TTFT 86 ms. Caveats that block promotion regardless of scores: community
REAP prune (provenance/long-tail quality unaudited), 94.3 GB VRAM (no 131k
path on this card), no reasoning parser configured (default-thinking leaks),
and decode still ~half the production heavy baseline.

### nvidia/DeepSeek-V4-Flash-NVFP4 — verdict: **not enough evidence**
NGC image: config-parse rejection (`deepseek_v4` unknown to bundled
transformers). Nightly image: loads, but ~80 s/shard × 46 shards projected
60+ min; aborted at shard 18/46 by operator to protect concurrent
measurements. The 96 GB fit question stays open; re-attempt in isolation.


## Extension — 2026-07-11 (post-merge round)

Four additional candidates, measured with the same gates after PR #198 merged.
Operational deltas from the base round: the Operator CLI v2 merge renamed
`preflight`/`benchmark` to `eval preflight`/`eval benchmark run` and gated
`serves up/down` behind `--confirm` (the reproduction section below reflects
the new surface); llama.cpp endpoints need an explicit `--max-model-len` hint
(they do not advertise a window, and the context probe overshoots without it);
and this llama.cpp build resolves `-hf` downloads into the HF hub cache layout,
so the compose anchor now mounts the cache volume at both paths.

### nvidia/NVIDIA-Nemotron-Labs-3-Puzzle-75B-A9B-NVFP4 — verdict: **best measured heavy candidate; not promoted**
vLLM nightly v0.23.1rc1, PRO 6000, `mtp` n=3 per the model card. Preflight ALL
PASS; full sweep at 131k (needle 13.8 s); intelligence 2/2. Long-generation
A/B: 91.4 tok/s -> **137.0 tok/s with MTP (1.50x)** — matching the
community-reported ~1.53x. The engine auto-disabled prefix caching for the
Mamba+MTP combination (consistent with upstream #39809). Official checkpoint,
distilled from Nemotron-3-Super-120B with trained MTP. Supersedes MiniMax REAP
as the heavy-role recommendation: official provenance, 131k vs 64k, 137 vs 97
tok/s, same 2/2 intelligence. Promotion still requires a pinned stable engine
release and an operator decision.

### sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP — verdict: **262k big-KV experiment validated; community checkpoint; not promoted**
The community-favorite Qwen3.6-27B dense, as a text-only NVFP4 conversion with
the bf16 MTP head preserved. Preflight ALL PASS; context verified at 131,072
AND **262,144 tokens** (largest verified window of the exercise; quarter-million
-token prefill TTFT ~124 s); intelligence 2/2; tools 20/20. MTP
(`qwen3_5_mtp` n=3): 69.9 -> **95.0 tok/s (1.36x)**; the author-reported 1.74x
did not reproduce at this config. Requires `--language-model-only` and prefix
caching off with MTP (three upstream issues; see the recipe).

### unsloth/Qwen3.5-35B-A3B-GGUF Q4_K_M (llama.cpp) — verdict: **strongest fast-tier candidate measured; not promoted**
RTX 5090, 64k window (GDN hybrid makes KV nearly free), unsloth non-thinking
sampling defaults. Intelligence 2/2 — the only fast-track candidate to match
the heavy baseline — tools 20/20, session pass, 64k full-prefill 8.1 s,
~147 tok/s decode at 178 ms warm TTFT. Below the 214 tok/s community 5090
reference (newer llama.cpp build there; build freshness measurably matters on
this arch). llama.cpp MTP (`--spec-type draft-mtp`) untested — the obvious
next optimization pass.

### unsloth/gemma-4-E4B-it-qat-GGUF UD-Q4_K_XL (llama.cpp) — verdict: **low-latency specialist; not promoted**
QAT UD-Q4_K_XL chosen over naive Q4_K_M on unsloth's published KLD data.
Tools 20/20, session pass, 32k/64k context pass, intelligence 1/2, 97 tok/s at
**61 ms warm TTFT**. Two recorded artifacts: default-mode probes can route all
output into `reasoning_content` (zero-completion-token measurement trap), and
the open upstream PLE gap (#22243) leaves a standing quality question for
E-class Gemma on llama.cpp specifically.

### Extension operational notes
- The standard 10-request short-completion benchmark **under-generates for MTP
  A/Bs** (completions EOS at 10-31 tokens; speculative overhead cannot
  amortize). The archived `*-mtp-ab-longgen.json` probes (3x1024 tokens,
  temp 0) are the deciding artifacts; the short-probe JSONs are kept as the
  negative methodology result.
- An in-layer GGUF salvage stream was killed by rotating the card mid-copy
  (operator error, ~20 GB re-download exposure only; measurements were
  complete). Rule: never rotate a card while a container-layer salvage is live.
- `docker-compose.mtp-off.yml` mirrors the candidate commands minus
  speculation; the mirrors must be edited in lockstep (one drift, one failed
  start, fixed).

## Comparison Scorecard

Machine-readable: `…-evidence/scorecard.csv`. Compact reader-facing table in
`docs/BENCHMARKS.md` ("Blackwell candidate bakeoff" section).

## Recommendations

`…-evidence/recommendations.json` is authoritative. In brief: heavy role —
MiniMax REAP is the candidate to watch (pending provenance and headroom
answers); agentic/long-context — Ornith earns a specialist slot; fast role —
the incumbent stands; multimodal — Omni when a stable engine lands.
**A benchmark win is evidence, not a promotion.**

## Production Impact

**None.** Production heavy (gpt-oss-120b) and fast (qwen36-35b-a3b) are
running and preflight-verified post-bakeoff (see
`…-evidence/runtime-restoration.md`). No router config, tier assignment, or
harness config changed. Candidate serves are evaluation-only compose services,
none wired into the router.

## Reproduction

`…-evidence/reproduction.md` — exact commands from pull to evidence, all via
the product CLI (`models pull`, `serves up/down`, `preflight`,
`benchmark --bakeoff`).

## Evidence Index

All under `docs/findings/2026-07-10-blackwell-local-model-bakeoff-evidence/`:

- `environment.json` — host, GPUs, driver, platform (incl. mid-run WSL 2.6.2→2.7.10 upgrade)
- `source-registry.json` — provenance registry (official / community-prior / local)
- `baseline-gpt-oss-120b-vllm-mxfp4-131k.bakeoff.json`, `baseline-qwen36-35b-a3b-vllm-nvfp4-32k.bakeoff.json` (+ `-thinking-default` negative artifact)
- `candidate-nemotron-text-*` — 131k-hang failure, eager-64k, graphs-64k, PIECEWISE-131k, clean re-measure (`-wsl2710`), throughput
- `candidate-nemotron-omni-*` — load-failure record, nightly bakeoff, throughput
- `candidate-gemma4-31b-*` — none (all six configs died pre-serve; ladder in `failures.md`)
- `candidate-ornith-35b-*` — 131k bakeoff, throughput
- `candidate-minimax-m27-reap-*` — 64k bakeoff, throughput
- `preflight-transcripts.md` — verbatim preflight console captures (needle timings, 20/20 tool batches) + operator nvidia-smi observations
- `candidate-nemotron-puzzle-75b-*`, `candidate-qwen36-27b-mtp-*` — extension heavy candidates: bakeoff JSONs, short-probe throughput, and `*-mtp-ab-longgen.json` A/B probes
- `candidate-qwen35-35b-llamacpp-*`, `candidate-gemma4-e4b-llamacpp-*` — extension llama.cpp fast candidates (incl. the context-suite rerun with the `--max-model-len` hint)
- `scorecard.csv`, `recommendations.json`, `failures.md`, `reproduction.md`, `runtime-restoration.md`, `checksums.sha256`
